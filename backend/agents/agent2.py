"""
backend/agents/agent2.py

Agent 2 — Intelligence Extraction RAG-Enhanced

Consumes verified events from Redis Stream `events:verified`,
retrieves similar historical disruption summaries from ChromaDB,
calls Gemini in structured output mode, and falls back to spaCy
after exponential backoff failures.

UPDATED (this revision):
  1. Migrated google-generativeai (deprecated, no longer receiving
     updates/fixes) to google-genai. Old SDK used a global genai.configure()
     + stateful GenerativeModel object; new SDK uses a Client object with
     no global config. See: https://ai.google.dev/gemini-api/docs/migrate
  2. Gemini calls now use the new SDK's native async interface
     (client.aio.models.generate_content) instead of calling a sync method
     directly inside an async function — that was blocking the entire
     FastAPI event loop for the duration of every Gemini network call.
  3. Redis's xreadgroup/xack (also sync, also called unguarded inside the
     async loop) now run via asyncio.to_thread — same fix, since the
     `redis` package here is the sync client, not redis.asyncio.
  4. Stream name corrected to "events:verified" (was "eventsverified") —
     confirmed via agent3_risk_engine.py's own working code, which reads
     from "events:verified" successfully. The old name meant this consumer
     was listening on a stream nothing publishes to.
  5. Consumer group name corrected to "agent2_consumers" (was
     "agent2consumers") — matches the exact name specified in the Day 9
     task spec.

If any of these four corrections turn out to conflict with other changes
in flight elsewhere, flag it — these were fixed based on direct evidence
(the Day 9 spec text and agent3_risk_engine.py's working code), not a
guess, but worth confirming nothing else now depends on the old names.
"""

from __future__ import annotations

import asyncio
import ast
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from google import genai
from google.genai import types as genai_types
import redis
import spacy

from db.chroma_client import init_chroma, query_similar

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", os.getenv("REDISURL", "redis://redis:6379/0"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GEMINIAPIKEY"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", os.getenv("GEMINIMODEL", "gemini-2.5-flash"))

# Corrected per Day 9 spec and to match what Agent 1's verification layer
# actually publishes to (confirmed working in agent3_risk_engine.py).
STREAM_IN = "events:verified"
CONSUMER_GROUP = "agent2_consumers"
CONSUMER_NAME = os.getenv("AGENT2_CONSUMER_NAME", "agent2-worker")
DLQ_STREAM = "events:verified:dlq"

MAX_RETRIES = 3
POLL_BLOCK_MS = 5000
REDIS_SOCKET_TIMEOUT_SECS = max(10, (POLL_BLOCK_MS // 1000) + 5)

# Renamed from _gemini_model (GenerativeModel instance, old SDK) to
# _gemini_client (Client instance, new SDK) — the model name is now
# passed per-call instead of being bound to a stateful object.
_gemini_client: Any = None
_nlp: Any = None

_REQUIRED_FIELDS = {
    "event_type",
    "location",
    "corridor_affected",
    "severity",
    "disruption_type",
    "similar_historical_events",
}


def init_chromadb() -> None:
    init_chroma()
    logger.info("Agent 2 ChromaDB initialized")


def _init_gemini_client() -> None:
    """
    New SDK: no global genai.configure(). A Client is created once and
    reused — it exposes both sync (client.models) and async
    (client.aio.models) interfaces. We use the async interface throughout
    since this whole agent runs inside FastAPI's event loop.
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


def _load_spacy():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_sm")
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


async def _call_gemini(prompt: str) -> dict | None:
    if _gemini_client is None:
        return None

    for attempt in range(MAX_RETRIES):
        try:
            # New SDK's async interface (client.aio) — genuinely
            # non-blocking, unlike the old SDK's generate_content()
            # which was sync-only and had to be run directly (blocking
            # the event loop) or wrapped in asyncio.to_thread.
            response = await _gemini_client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            return json.loads(response.text.strip())
        except Exception as exc:
            logger.warning("Gemini attempt %d failed: %s", attempt + 1, exc)
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)

    return None


def _llm_extraction_score(extracted: dict) -> float:
    penalty = 0.0
    penalty_step = 1.0 / len(_REQUIRED_FIELDS)

    for field in _REQUIRED_FIELDS:
        value = extracted.get(field)
        if value in (None, "", [], "Unknown"):
            penalty += penalty_step

    return round(max(0.0, min(1.0, 1.0 - penalty)), 4)


def _confidence(extracted: dict, similar_events: list[dict]) -> tuple[float, float, float]:
    llm_score = _llm_extraction_score(extracted)
    max_rag_similarity = max((item["similarity"] for item in similar_events), default=0.0)
    confidence = round((0.4 * llm_score) + (0.6 * max_rag_similarity), 4)
    return confidence, llm_score, max_rag_similarity


async def extract_intelligence(event: dict) -> dict:
    event_text = event.get("event", "") or event.get("description", "")
    event_id = event.get("eventid") or event.get("event_id") or str(uuid.uuid4())

    if not event_text:
        raise ValueError("Missing event text in payload.")

    similar_events = query_similar(event_text, n_results=3)

    prompt = _build_prompt(event_text, similar_events)
    extracted = await _call_gemini(prompt)

    if extracted is None:
        extracted = _spacy_fallback(event_text, similar_events)

    confidence, llm_score, max_rag_similarity = _confidence(extracted, similar_events)

    return {
        "event_id": event_id,
        "original_event": event,
        "extracted": extracted,
        "similar_historical_events": similar_events,
        "llm_extraction_score": llm_score,
        "max_rag_similarity": max_rag_similarity,
        "confidence": confidence,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


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
        STREAM_IN,
        CONSUMER_GROUP,
        CONSUMER_NAME,
    )

    r = _get_redis()
    # One-time startup call — fine to leave sync, not part of the
    # recurring hot loop that was freezing the event loop.
    _ensure_consumer_group(r)

    while True:
        try:
            # xreadgroup is a SYNC/blocking call (this is the `redis`
            # package, not redis.asyncio). Calling it directly inside this
            # async loop with no await/to_thread was freezing the entire
            # FastAPI event loop for up to POLL_BLOCK_MS (5s) every single
            # cycle — confirmed by steadily growing APScheduler delay
            # warnings in production logs. asyncio.to_thread offloads it
            # to a worker thread instead.
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
                        result = await extract_intelligence(event)

                        logger.info(
                            "Agent 2 processed event_id=%s confidence=%.4f",
                            result["event_id"],
                            result["confidence"],
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
                                "Moved malformed/failed message to DLQ stream=%s msg_id=%s",
                                DLQ_STREAM,
                                msg_id,
                            )
                        except Exception as dlq_exc:
                            logger.exception(
                                "Failed sending msg_id=%s to DLQ error=%s",
                                msg_id,
                                dlq_exc,
                            )

            await asyncio.sleep(0)

        except redis.exceptions.TimeoutError:
            await asyncio.sleep(0)
        except Exception as exc:
            logger.exception("Agent 2 loop error: %s", exc)
            await asyncio.sleep(5)