"""
agents/agent2.py
================
Agent 2 — Intelligence Extraction: RAG-Enhanced, Gemini-Structured Output.

Responsibilities
----------------
1. Consume verified events from Redis Stream `events_verified`
   (consumer group: agent2_consumers).
2. Embed each event with sentence-transformers all-MiniLM-L6-v2.
3. Query ChromaDB `disruption_reports` collection for top-3 similar
   historical events (RAG grounding).
4. Call Gemini 2.5 Flash in structured-output mode with the event text
   + retrieved historical context.
5. Fix 6 — exponential back-off: 3 attempts (1 s, 2 s delays) then
   fall back to spaCy en_core_web_sm NER.
6. Emit confidence = 0.4 * llm_score + 0.6 * max_rag_similarity.
7. Fix 4 — ChromaDB upsert with SHA-256 content hash as document ID.
8. Fix 12 — verify embedding model name in collection metadata at startup;
   raise ValueError on mismatch.

Startup helpers
---------------
* init_chromadb() — call once at FastAPI lifespan startup.
* seed_historical_events(events) — idempotent; safe to call repeatedly.

Run Agent 2 background loop
---------------------------
asyncio.create_task(run_agent2()) in FastAPI lifespan.
"""

from __future__ import annotations

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
import google.generativeai as genai
import redis
import spacy
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — everything from .env, nothing hardcoded
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CHROMA_HOST = os.getenv("CHROMA_HOST", "chromadb")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
STREAM_IN = "events_verified"
CONSUMER_GROUP = "agent2_consumers"
CONSUMER_NAME = "agent2_worker"
COLLECTION_NAME = "disruption_reports"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
MAX_RETRIES = 3
POLL_BLOCK_MS = 5_000

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_chroma_client: chromadb.HttpClient | None = None
_collection: Any = None
_embedder: SentenceTransformer | None = None
_nlp: Any = None

# ---------------------------------------------------------------------------
# Fix 12 — ChromaDB initialisation with model-name verification
# ---------------------------------------------------------------------------

def init_chromadb() -> None:
    """
    Call once at FastAPI startup (lifespan).
    Creates / fetches the collection and validates the embedding model name.
    Raises ValueError if the stored model name does not match the config.
    """
    global _chroma_client, _collection, _embedder

    _chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    _embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)

    _collection = _chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"embedding_model": EMBEDDING_MODEL_NAME},
    )

    metadata = getattr(_collection, "metadata", {}) or {}
    stored_model = metadata.get("embedding_model")
    if stored_model and stored_model != EMBEDDING_MODEL_NAME:
        raise ValueError(
            f"ChromaDB collection '{COLLECTION_NAME}' was built with model "
            f"'{stored_model}' but current config uses '{EMBEDDING_MODEL_NAME}'. "
            "Re-seed the collection with the correct model before starting."
        )

    logger.info(
        "ChromaDB ready — collection '%s', model '%s', docs: %d",
        COLLECTION_NAME,
        EMBEDDING_MODEL_NAME,
        _collection.count(),
    )

# ---------------------------------------------------------------------------
# Fix 4 — Seeding helpers (upsert + SHA-256 ID)
# ---------------------------------------------------------------------------

def _doc_id(content: str) -> str:
    """Stable SHA-256 hash of document content used as ChromaDB document ID."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def _normalize_embedding(vec: Any) -> list[float]:
    """
    Accept both numpy arrays / tensors with .tolist() and plain Python lists.
    This prevents tests from failing when mocks return raw lists.
    """
    if hasattr(vec, "tolist"):
        vec = vec.tolist()
    return [float(x) for x in vec]

def seed_historical_events(events: list[dict]) -> int:
    """
    Idempotent upsert of historical disruption summaries into ChromaDB.

    Each event dict must have:
        text      (str)  — human-readable summary
        date      (str)  — ISO date
        corridor  (str)  — Hormuz | RedSea | Suez | Cape | Unknown
        severity  (str)  — low | medium | high
        outcome   (str)  — brief outcome description

    Returns the number of documents now in the collection.
    """
    if _collection is None or _embedder is None:
        raise RuntimeError("Call init_chromadb() before seed_historical_events().")

    documents: list[str] = []
    embeddings: list[list[float]] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    for ev in events:
        text = ev["text"]
        doc_id = _doc_id(text)
        embedding = _normalize_embedding(_embedder.encode(text))

        documents.append(text)
        embeddings.append(embedding)
        metadatas.append(
            {
                "date": ev.get("date", ""),
                "corridor": ev.get("corridor", "Unknown"),
                "severity": ev.get("severity", "medium"),
                "outcome": ev.get("outcome", ""),
            }
        )
        ids.append(doc_id)

    _collection.upsert(
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
        ids=ids,
    )

    total = _collection.count()
    logger.info(
        "ChromaDB seeded — %d docs total after upsert of %d events",
        total,
        len(events),
    )
    return total

# ---------------------------------------------------------------------------
# Embedding helper
# ---------------------------------------------------------------------------

def _embed(text: str) -> list[float]:
    if _embedder is None:
        raise RuntimeError("Call init_chromadb() first.")
    return _normalize_embedding(_embedder.encode(text))

# ---------------------------------------------------------------------------
# RAG — ChromaDB similarity search
# ---------------------------------------------------------------------------

def _query_similar_events(event_text: str, n_results: int = 3) -> list[dict]:
    """
    Returns top-n similar historical events with similarity scores.
    Each item: {text, metadata, similarity}
    """
    if _collection is None:
        raise RuntimeError("Call init_chromadb() first.")

    collection_count = _collection.count()
    if collection_count == 0:
        return []

    embedding = _embed(event_text)
    results = _collection.query(
        query_embeddings=[embedding],
        n_results=min(n_results, collection_count),
        include=["documents", "metadatas", "distances"],
    )

    similar = []
    for doc, meta, dist in zip(
        results.get("documents", [[]])[0],
        results.get("metadatas", [[]])[0],
        results.get("distances", [[]])[0],
    ):
        similarity = max(0.0, 1.0 - float(dist) / 2.0)
        similar.append(
            {
                "text": doc,
                "metadata": meta or {},
                "similarity": similarity,
            }
        )

    return similar

# ---------------------------------------------------------------------------
# Fix 6 — spaCy fallback NER
# ---------------------------------------------------------------------------

def _load_spacy() -> Any:
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_sm")
    return _nlp

def _spacy_fallback(event_text: str, similar_events: list[dict]) -> dict:
    """
    Minimal structured extraction using spaCy NER when Gemini is unavailable.
    Returns the same JSON schema as Gemini output but with extraction_method=fallback_ner.
    """
    nlp = _load_spacy()
    doc = nlp(event_text)

    locations = [ent.text for ent in doc.ents if ent.label_ in ("GPE", "LOC", "FAC")]
    orgs = [ent.text for ent in doc.ents if ent.label_ == "ORG"]

    corridor = "Unknown"
    text_lower = event_text.lower()
    for name in ("hormuz", "red sea", "bab-el-mandeb", "suez", "cape of good hope"):
        if name in text_lower:
            corridor = {
                "hormuz": "Hormuz",
                "red sea": "RedSea",
                "bab-el-mandeb": "RedSea",
                "suez": "Suez",
                "cape of good hope": "Cape",
            }[name]
            break

    return {
        "event_type": "Unknown",
        "location": locations[0] if locations else "Unknown",
        "corridor_affected": corridor,
        "severity": 5,
        "disruption_type": "Unknown",
        "similar_historical_events": [e["text"][:120] for e in similar_events],
        "key_entities": orgs[:3],
        "extraction_method": "fallback_ner",
        "confidence": 0.0,
    }

# ---------------------------------------------------------------------------
# Fix 6 — Gemini call with exponential back-off
# ---------------------------------------------------------------------------

_GEMINI_SCHEMA = {
    "event_type": str,
    "location": str,
    "corridor_affected": str,
    "severity": int,
    "disruption_type": str,
    "similar_historical_events": list,
    "key_entities": list,
    "extraction_method": str,
}

_REQUIRED_FIELDS = {
    "event_type",
    "location",
    "corridor_affected",
    "severity",
    "disruption_type",
    "similar_historical_events",
}

def _build_gemini_prompt(event_text: str, similar_events: list[dict]) -> str:
    context_block = "\n".join(
        f"- [{e['metadata'].get('date', '?')}] {e['text'][:200]}"
        for e in similar_events
    )
    return f"""You are an energy supply chain intelligence analyst.

Analyse the following verified geopolitical/maritime event and return ONLY valid JSON
matching the schema below. No preamble. No trailing text. No markdown.

SCHEMA:
{{
  "event_type": "<string>",
  "location": "<string>",
  "corridor_affected": "<Hormuz|RedSea|Suez|Cape|Unknown>",
  "severity": <integer 1-10>,
  "disruption_type": "<string>",
  "similar_historical_events": ["<string>", ...],
  "key_entities": ["<string>", ...],
  "extraction_method": "gemini_structured"
}}

HISTORICAL CONTEXT (top-3 similar past events from knowledge base):
{context_block}

EVENT TEXT:
{event_text}

Return JSON only."""

def _call_gemini(prompt: str) -> dict | None:
    """
    Returns parsed dict on success, None on all retries exhausted.
    Fix 6: 3 attempts with 1 s, 2 s back-off delays.
    """
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — skipping Gemini call.")
        return None

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)

    for attempt in range(MAX_RETRIES):
        try:
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            raw = response.text.strip()
            parsed = json.loads(raw)
            logger.debug("Gemini succeeded on attempt %d", attempt + 1)
            return parsed
        except Exception as exc:
            logger.warning("Gemini attempt %d failed: %s", attempt + 1, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)

    logger.error(
        "All %d Gemini attempts failed — activating spaCy fallback.",
        MAX_RETRIES,
    )
    return None

# ---------------------------------------------------------------------------
# LLM extraction score helper
# ---------------------------------------------------------------------------

def _llm_extraction_score(extracted: dict) -> float:
    """
    1.0 if all required fields are present and non-empty.
    Penalised by 1/len(REQUIRED_FIELDS) per missing or empty field.
    """
    penalty = 0.0
    step = 1.0 / len(_REQUIRED_FIELDS)

    for field in _REQUIRED_FIELDS:
        val = extracted.get(field)
        if val is None or val == "" or val == [] or val == "Unknown":
            penalty += step

    score = max(0.0, min(1.0, 1.0 - penalty))

    if abs(score) < 1e-9:
        return 0.0
    if abs(score - 1.0) < 1e-9:
        return 1.0
    return score

# ---------------------------------------------------------------------------
# Confidence formula
# ---------------------------------------------------------------------------

def _compute_confidence(llm_score: float, similar_events: list[dict]) -> float:
    """
    confidence = 0.4 × llm_extraction_score + 0.6 × max_rag_similarity_score
    """
    max_rag = max((e["similarity"] for e in similar_events), default=0.0)
    return round(0.4 * llm_score + 0.6 * max_rag, 4)

# ---------------------------------------------------------------------------
# Core extraction pipeline (single event)
# ---------------------------------------------------------------------------

def extract_intelligence(event: dict) -> dict:
    """
    Full Agent 2 pipeline for one verified event dict.

    Parameters
    ----------
    event : dict
        Must contain at minimum: event_id, event (text), corridor, stage, confidence

    Returns
    -------
    dict — enriched intelligence record ready for downstream agents.
    """
    event_text = event.get("event", "")
    event_id = event.get("event_id", str(uuid.uuid4()))

    similar_events = _query_similar_events(event_text, n_results=3)
    max_rag_sim = max((e["similarity"] for e in similar_events), default=0.0)

    prompt = _build_gemini_prompt(event_text, similar_events)
    extracted = _call_gemini(prompt)
    used_fallback = extracted is None

    if used_fallback:
        extracted = _spacy_fallback(event_text, similar_events)

    llm_score = _llm_extraction_score(extracted)
    confidence = _compute_confidence(llm_score, similar_events)

    if used_fallback:
        confidence = max(0.0, min(1.0, confidence))
    
    return {
        "event_id": event_id,
        "original_event": event,
        "extracted": extracted,
        "similar_historical_events": similar_events,
        "llm_extraction_score": llm_score,
        "max_rag_similarity": max_rag_sim,
        "confidence": confidence,
        "extraction_method": extracted.get("extraction_method", "unknown"),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

# ---------------------------------------------------------------------------
# Redis Stream helpers
# ---------------------------------------------------------------------------

def _get_redis() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)

def _ensure_consumer_group(r: redis.Redis) -> None:
    """Create the consumer group if it does not yet exist."""
    try:
        r.xgroup_create(STREAM_IN, CONSUMER_GROUP, id="0", mkstream=True)
        logger.info(
            "Consumer group '%s' created on stream '%s'.",
            CONSUMER_GROUP,
            STREAM_IN,
        )
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            pass
        else:
            raise

# ---------------------------------------------------------------------------
# Background agent loop
# ---------------------------------------------------------------------------

async def run_agent2() -> None:
    """
    Async background loop — call via asyncio.create_task(run_agent2())
    in FastAPI lifespan (after init_chromadb()).

    Reads from the events_verified Redis Stream using a consumer group
    so messages are never lost if this worker is temporarily busy.
    Fix 1 — xreadgroup, never subscribe/publish.
    """
    logger.info("Agent 2 starting — listening on stream '%s'.", STREAM_IN)
    r = _get_redis()
    _ensure_consumer_group(r)

    while True:
        try:
            messages = r.xreadgroup(
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
                        event = json.loads(fields.get("data", "{}"))
                        result = extract_intelligence(event)
                        logger.info(
                            "Agent 2 processed event %s — confidence=%.3f method=%s",
                            result["event_id"],
                            result["confidence"],
                            result["extraction_method"],
                        )
                        r.xack(STREAM_IN, CONSUMER_GROUP, msg_id)
                    except Exception as exc:
                        logger.error(
                            "Agent 2 failed to process message %s: %s",
                            msg_id,
                            exc,
                        )

            await asyncio.sleep(0)

        except Exception as exc:
            logger.error("Agent 2 stream error: %s — retrying in 5 s.", exc)
            await asyncio.sleep(5)

# ---------------------------------------------------------------------------
# FastAPI lifespan hook
# ---------------------------------------------------------------------------

def startup() -> None:
    """
    Call this inside your FastAPI lifespan context:

        from agents.agent2 import startup as agent2_startup
        agent2_startup()
        asyncio.create_task(run_agent2())
    """
    init_chromadb()
    genai.configure(api_key=GEMINI_API_KEY)
    logger.info("Agent 2 initialised.")