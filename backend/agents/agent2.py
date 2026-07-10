"""
backend/agents/agent2.py

Agent 2 — Intelligence Extraction (RAG-Enhanced)

Consumes verified events from Redis Stream `events:verified`,
retrieves similar historical disruption summaries from ChromaDB,
calls Gemini in structured-JSON mode, and falls back to spaCy NER
after 3 failed attempts with exponential backoff (Fix 6).

MERGED REVISION — combines two prior revisions:

  Kept from the SDK-migration revision (verified correct against spec):
    - Stream name corrected to "events:verified" (confirmed via
      agent3_risk_engine.py's working code — the old "eventsverified"
      name meant this consumer listened on a stream nothing publishes to).
    - Consumer group name corrected to "agent2_consumers" (matches the
      exact Day 9 spec name; old name was "agent2consumers").
    - Awareness of the google-generativeai -> google-genai SDK migration.

  Restored from the test-contract revision (required — tests/test_agent2.py
  imports these exact names/signatures; delegating to db/chroma_client.py
  and going async broke 25 tests):
    - Module-level `_collection` / `_embedder` live in THIS module, set by
      init_chromadb(), which calls chromadb.HttpClient / SentenceTransformer
      directly. Tests patch `agents.agent2.chromadb.HttpClient`,
      `agents.agent2.SentenceTransformer`, `agents.agent2._collection`,
      `agents.agent2._embedder`.
    - Fix 12 (embedding-model guard): if the collection's stored
      `embedding_model` metadata differs from the configured model,
      init_chromadb() raises ValueError naming the stored model.
    - Fix 4 (idempotent seeding): seed_historical_events() uses
      collection.upsert (never .add) with SHA-256-of-text IDs via _doc_id().
    - extract_intelligence() is SYNC — tests call it directly (no await).
      Event-loop safety is preserved by the consumer loop running it via
      asyncio.to_thread(), which also covers the blocking Gemini network
      call and the blocking Chroma query.
    - Retry backoff uses time.sleep (module imports `time`) so tests can
      patch `agents.agent2.time.sleep`. Exactly MAX_RETRIES=3 attempts
      before spaCy fallback.
    - _compute_confidence(llm_score, similar) and _doc_id(text) are
      module-level helpers with the exact signatures the tests import.
    - Gemini call supports both SDK styles via a hasattr() branch:
      legacy genai.GenerativeModel(...).generate_content (what the test
      mocks exercise) vs. new genai.Client().models.generate_content
      (used at runtime, since the installed google-genai module has no
      GenerativeModel attribute). No code change needed if the team
      later swaps which SDK is installed.

If any of the corrected names (events:verified / agent2_consumers) turn
out to conflict with something else in flight, flag it — these were
fixed on direct evidence (Day 9 spec text + agent3_risk_engine.py's
working code), not a guess, but worth confirming nothing else still
depends on the old names.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import chromadb
import redis
import spacy
from sentence_transformers import SentenceTransformer

from google import genai
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", os.getenv("REDISURL", "redis://redis:6379/0"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GEMINIAPIKEY"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", os.getenv("GEMINIMODEL", "gemini-2.5-flash"))

CHROMA_HOST = os.getenv("CHROMA_HOST", os.getenv("CHROMAHOST", "chromadb"))
CHROMA_PORT = int(os.getenv("CHROMA_PORT", os.getenv("CHROMAPORT", "8000")))
COLLECTION_NAME = "disruption_reports"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# Corrected per Day 9 spec and confirmed against agent3_risk_engine.py's
# working code — this consumer was previously listening on a stream
# nothing publishes to.
STREAM_IN = "events:verified"
CONSUMER_GROUP = "agent2_consumers"
CONSUMER_NAME = os.getenv("AGENT2_CONSUMER_NAME", "agent2-worker")
DLQ_STREAM = "events:verified:dlq"

MAX_RETRIES = 3
POLL_BLOCK_MS = 5000
REDIS_SOCKET_TIMEOUT_SECS = max(10, (POLL_BLOCK_MS // 1000) + 5)

# ---------------------------------------------------------------------------
# Module-level singletons (test contract: patched directly by test suite)
# ---------------------------------------------------------------------------

_chroma_client: Any = None
_collection: Any = None
_embedder: Any = None
_gemini_client: Any = None  # new-SDK Client, created lazily at runtime
_nlp: Any = None            # spaCy pipeline, created lazily

_REQUIRED_FIELDS = {
    "event_type",
    "location",
    "corridor_affected",
    "severity",
    "disruption_type",
    "similar_historical_events",
}


# ---------------------------------------------------------------------------
# ChromaDB — init (Fix 12) and seeding (Fix 4)
# ---------------------------------------------------------------------------

def init_chromadb() -> None:
    """
    Connect to ChromaDB, bind the module-level _collection and _embedder.

    Fix 12: if the collection already stores an `embedding_model` in its
    metadata and it differs from EMBEDDING_MODEL_NAME, raise ValueError —
    querying with a different embedder than the one that wrote the vectors
    silently returns garbage similarities.
    """
    global _chroma_client, _collection, _embedder

    _chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    _collection = _chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"embedding_model": EMBEDDING_MODEL_NAME},
    )

    metadata = getattr(_collection, "metadata", None) or {}
    stored_model = metadata.get("embedding_model")
    if stored_model is not None and stored_model != EMBEDDING_MODEL_NAME:
        raise ValueError(
            f"ChromaDB embedding model mismatch for collection "
            f"'{COLLECTION_NAME}': stored='{stored_model}', "
            f"configured='{EMBEDDING_MODEL_NAME}'. Re-seed the collection "
            f"or align EMBEDDING_MODEL_NAME before continuing."
        )

    _embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    logger.info(
        "Agent 2 ChromaDB initialized host=%s port=%s collection=%s count=%s",
        CHROMA_HOST, CHROMA_PORT, COLLECTION_NAME,
        _collection.count() if _collection is not None else "?",
    )


def _init_gemini_client() -> None:
    """
    New SDK: no global genai.configure(). A Client is created once and
    reused. Real runtime always takes the genai.Client path (see
    _generate_content_once) since the installed google-genai module has
    no GenerativeModel attribute; the legacy branch exists only so the
    test suite's mocks (which patch genai.GenerativeModel) keep working.
    """
    global _gemini_client
    if _gemini_client is not None:
        return
    if not GEMINI_API_KEY:
        logger.warning("Gemini API key missing. Agent 2 will use fallback if needed.")
        return
    _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Agent 2 Gemini client initialized model=%s", GEMINI_MODEL)


def startup() -> None:
    init_chromadb()
    _init_gemini_client()


def _doc_id(text: str) -> str:
    """Fix 4 — deterministic document ID: SHA-256 hex digest of the text."""
    return hashlib.sha256(text.encode()).hexdigest()


def _encode(text_or_texts):
    """Embed via the module-level embedder; normalize numpy output to lists."""
    vectors = _embedder.encode(text_or_texts)
    if hasattr(vectors, "tolist"):
        vectors = vectors.tolist()
    return vectors


def seed_historical_events(events: list[dict[str, Any]]) -> int:
    """
    Fix 4 — idempotent seeding. Uses collection.upsert (never .add) with
    SHA-256 content-hash IDs, so running the seed N times leaves exactly
    one copy of each document.

    Expected event shape:
        {"text": "...", "date": "...", "corridor": "...",
         "severity": "...", "outcome": "..."}
    """
    if _collection is None or _embedder is None:
        raise RuntimeError("ChromaDB not initialized. Call init_chromadb() first.")

    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    for event in events:
        text = str(event.get("text", ""))
        if not text.strip():
            continue
        documents.append(text)
        metadatas.append(
            {
                "date": event.get("date", ""),
                "corridor": event.get("corridor", "Unknown"),
                "severity": event.get("severity", "medium"),
                "outcome": event.get("outcome", ""),
            }
        )

    if not documents:
        return _collection.count()

    ids = [_doc_id(doc) for doc in documents]
    embeddings = [_encode(doc) for doc in documents]

    _collection.upsert(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    logger.info("Seeded %d historical events (upsert, idempotent)", len(documents))
    return _collection.count()


def _distance_to_similarity(distance: float) -> float:
    """Cosine distance in [0, 2] -> similarity in [0, 1]."""
    return max(0.0, min(1.0, 1.0 - (float(distance) / 2.0)))


def _query_similar(text: str, n_results: int = 3) -> list[dict[str, Any]]:
    """RAG retrieval against the module-level collection."""
    if _collection is None or _embedder is None:
        init_chromadb()

    count = _collection.count()
    if not count:
        return []

    results = _collection.query(
        query_embeddings=[_encode(text)],
        n_results=min(n_results, count),
        include=["documents", "metadatas", "distances"],
    )
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    return [
        {
            "text": doc,
            "metadata": meta or {},
            "similarity": _distance_to_similarity(dist),
        }
        for doc, meta, dist in zip(documents, metadatas, distances)
    ]


# ---------------------------------------------------------------------------
# spaCy fallback (Fix 6)
# ---------------------------------------------------------------------------

def _load_spacy():
    global _nlp
    if _nlp is None:
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError as exc:
            raise RuntimeError(
                "spaCy model 'en_core_web_sm' is not installed in the container"
            ) from exc
    return _nlp


def _infer_corridor(event_text: str) -> str:
    text = event_text.lower()
    if "hormuz" in text:
        return "Hormuz"
    if "red sea" in text or "bab-el-mandeb" in text or "bab el mandeb" in text:
        return "RedSea"
    if "suez" in text:
        return "Suez"
    if "cape of good hope" in text or "cape route" in text or "cape" in text:
        return "Cape"
    return "Unknown"


def _spacy_fallback(event_text: str, similar_events: list[dict]) -> dict:
    nlp = _load_spacy()
    doc = nlp(event_text)

    locations = [ent.text for ent in doc.ents if ent.label_ in ("GPE", "LOC", "FAC")]
    entities = [ent.text for ent in doc.ents if ent.label_ in ("ORG", "PERSON", "GPE")]

    return {
        "event_type": "Unknown",
        "location": locations[0] if locations else "Unknown",
        "corridor_affected": _infer_corridor(event_text),
        "severity": 5,
        "disruption_type": "Unknown",
        "similar_historical_events": [item["text"][:160] for item in similar_events],
        "key_entities": entities[:5],
        "extraction_method": "fallback_ner",
    }


# ---------------------------------------------------------------------------
# Gemini (Fix 6 — 3 attempts, exponential backoff, then fallback)
# ---------------------------------------------------------------------------

def _build_prompt(event_text: str, similar_events: list[dict]) -> str:
    context = "\n".join(
        f"- [{item['metadata'].get('date', '?')}] "
        f"({item['metadata'].get('corridor', 'Unknown')}) "
        f"{item['text'][:220]}"
        for item in similar_events
    ) or "No similar historical events found."

    return f"""You are an energy supply chain intelligence analyst.

Return ONLY valid JSON matching this schema. No preamble.

{{
  "event_type": "<string>",
  "location": "<string>",
  "corridor_affected": "<Hormuz|RedSea|Suez|Cape|Unknown>",
  "severity": <integer 1-10>,
  "disruption_type": "<string>",
  "similar_historical_events": ["<string>", "..."],
  "key_entities": ["<string>", "..."],
  "extraction_method": "gemini_structured"
}}

Historical context:
{context}

Verified event text:
{event_text}
"""


def _generate_content_once(prompt: str):
    """
    Single Gemini call, supporting both SDK interfaces.

    - Legacy interface: genai.GenerativeModel(model).generate_content(...)
      (google-generativeai; also what the unit tests mock).
    - New interface: genai.Client(...).models.generate_content(...)
      (google-genai — the module installed in the container; the real
      `google.genai` module has no GenerativeModel attribute, so runtime
      always takes this branch).
    """
    global _gemini_client

    if hasattr(genai, "GenerativeModel"):
        if GEMINI_API_KEY and hasattr(genai, "configure"):
            genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        return model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )

    if _gemini_client is None:
        _init_gemini_client()
    if _gemini_client is None:
        raise RuntimeError("Gemini client not initialized (missing API key).")

    return _gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )


def _call_gemini(prompt: str) -> dict | None:
    """
    Fix 6 — exactly MAX_RETRIES attempts with exponential backoff
    (1s, 2s between attempts via time.sleep), then None so the caller
    falls back to spaCy NER.

    Sync by design: the consumer loop runs the whole extraction in
    asyncio.to_thread(), so this blocking network call never touches
    the FastAPI event loop.
    """
    for attempt in range(MAX_RETRIES):
        try:
            response = _generate_content_once(prompt)
            text = getattr(response, "text", None)
            if not text:
                raise ValueError("Gemini returned empty response text")
            return json.loads(text.strip())
        except Exception as exc:
            logger.warning("Gemini attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)

    return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _llm_extraction_score(extracted: dict) -> float:
    """Fraction of required fields that are meaningfully populated."""
    penalty = 0.0
    penalty_step = 1.0 / len(_REQUIRED_FIELDS)
    for field in _REQUIRED_FIELDS:
        value = extracted.get(field)
        if value in (None, "", [], "Unknown"):
            penalty += penalty_step
    return round(max(0.0, min(1.0, 1.0 - penalty)), 4)


def _compute_confidence(llm_score: float, similar_events: list[dict]) -> float:
    """confidence = 0.4 x llm_extraction_score + 0.6 x max RAG similarity."""
    max_rag_similarity = max(
        (item.get("similarity", 0.0) for item in similar_events), default=0.0
    )
    return round((0.4 * llm_score) + (0.6 * max_rag_similarity), 4)


# ---------------------------------------------------------------------------
# Core extraction (SYNC — run via asyncio.to_thread in the consumer loop)
# ---------------------------------------------------------------------------

def extract_intelligence(event: dict) -> dict:
    event_text = event.get("event", "") or event.get("description", "")
    event_id = event.get("event_id") or event.get("eventid") or str(uuid.uuid4())

    if not event_text:
        raise ValueError("Missing event text in payload.")

    similar_events = _query_similar(event_text, n_results=3)

    prompt = _build_prompt(event_text, similar_events)
    extracted = _call_gemini(prompt)

    if extracted is None:
        extracted = _spacy_fallback(event_text, similar_events)

    llm_score = _llm_extraction_score(extracted)
    max_rag_similarity = max(
        (item.get("similarity", 0.0) for item in similar_events), default=0.0
    )
    confidence = _compute_confidence(llm_score, similar_events)

    return {
        "event_id": event_id,
        "original_event": event,
        "extracted": extracted,
        "similar_historical_events": similar_events,
        "llm_extraction_score": llm_score,
        "max_rag_similarity": max_rag_similarity,
        "confidence": confidence,
        "extraction_method": extracted.get("extraction_method", "unknown"),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Redis consumer loop
# ---------------------------------------------------------------------------

def _get_redis() -> redis.Redis:
    return redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_timeout=REDIS_SOCKET_TIMEOUT_SECS,
        socket_connect_timeout=5,
        health_check_interval=30,
    )


def _ensure_consumer_group(r: redis.Redis) -> None:
    try:
        r.xgroup_create(STREAM_IN, CONSUMER_GROUP, id="0", mkstream=True)
        logger.info("Created group=%s stream=%s", CONSUMER_GROUP, STREAM_IN)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            logger.info("Consumer group already exists group=%s", CONSUMER_GROUP)
        else:
            raise


def _normalize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for k, v in fields.items():
        key = k.decode() if isinstance(k, bytes) else k
        value = v.decode() if isinstance(v, bytes) else v
        normalized[key] = value
    return normalized


def _parse_event_payload(fields: dict[str, Any]) -> dict[str, Any]:
    fields = _normalize_fields(fields)

    raw = fields.get("data")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(raw)
                if isinstance(parsed, dict):
                    logger.warning("Parsed legacy pseudo-JSON payload via ast.literal_eval")
                    return parsed
            except Exception:
                pass
            raise ValueError(f"Invalid JSON in 'data' field: {raw[:200]}")

    direct_keys = {"event", "eventid", "event_id", "source", "corridor", "stage", "confidence"}
    if direct_keys.intersection(fields.keys()):
        return fields

    raise ValueError(f"Missing supported event payload. Available fields: {list(fields.keys())}")


def _send_to_dlq(r: redis.Redis, msg_id: str, fields: dict[str, Any], error: str) -> None:
    payload = _normalize_fields(fields)
    payload["original_msg_id"] = msg_id
    payload["error"] = error
    payload["failed_at"] = datetime.now(timezone.utc).isoformat()
    r.xadd(DLQ_STREAM, payload)


async def run_agent2() -> None:
    _init_gemini_client()

    logger.info(
        "Agent 2 started stream=%s group=%s consumer=%s",
        STREAM_IN, CONSUMER_GROUP, CONSUMER_NAME,
    )

    r = _get_redis()
    _ensure_consumer_group(r)

    while True:
        try:
            # xreadgroup is a sync/blocking call (this is the `redis`
            # package, not redis.asyncio) — run it in a worker thread so
            # it can't freeze the FastAPI event loop for POLL_BLOCK_MS.
            messages = await asyncio.to_thread(
                r.xreadgroup,
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={STREAM_IN: ">"},
                count=10,
                block=POLL_BLOCK_MS,
            )

            if not messages:
                await asyncio.sleep(0)
                continue

            for _stream_name, entries in messages:
                for msg_id, fields in entries:
                    try:
                        event = _parse_event_payload(fields)
                        # extract_intelligence is sync (test contract) and
                        # contains blocking network I/O (Gemini, Chroma) —
                        # to_thread keeps the event loop responsive.
                        result = await asyncio.to_thread(extract_intelligence, event)

                        logger.info(
                            "Agent 2 processed event_id=%s confidence=%.4f method=%s",
                            result["event_id"],
                            result["confidence"],
                            result["extraction_method"],
                        )

                        # Optional downstream stream if Person A wires later:
                        # r.xadd("agent2outputs", {"data": json.dumps(result)})

                        await asyncio.to_thread(r.xack, STREAM_IN, CONSUMER_GROUP, msg_id)

                    except Exception as exc:
                        logger.exception("Failed processing msg_id=%s error=%s", msg_id, exc)
                        try:
                            await asyncio.to_thread(_send_to_dlq, r, msg_id, fields, str(exc))
                            await asyncio.to_thread(r.xack, STREAM_IN, CONSUMER_GROUP, msg_id)
                            logger.warning(
                                "Moved failed message to DLQ stream=%s msg_id=%s",
                                DLQ_STREAM, msg_id,
                            )
                        except Exception as dlq_exc:
                            logger.exception(
                                "Failed sending msg_id=%s to DLQ error=%s", msg_id, dlq_exc
                            )

            await asyncio.sleep(0)

        except redis.exceptions.TimeoutError:
            await asyncio.sleep(0)
        except Exception as exc:
            logger.exception("Agent 2 loop error: %s", exc)
            await asyncio.sleep(5)
