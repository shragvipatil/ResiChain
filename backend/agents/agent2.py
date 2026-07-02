"""
backend/agents/agent2.py

Agent 2 — Intelligence Extraction RAG-Enhanced

Consumes verified events from Redis Stream `eventsverified`,
retrieves similar historical disruption summaries from ChromaDB,
calls Gemini in structured output mode, and falls back to spaCy
after exponential backoff failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import google.generativeai as genai
import redis
import spacy

from db.chroma_client import init_chroma, query_similar

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", os.getenv("REDISURL", "redis://redis:6379/0"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GEMINIAPIKEY"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", os.getenv("GEMINIMODEL", "gemini-2.5-flash"))

STREAM_IN = "eventsverified"
CONSUMER_GROUP = "agent2consumers"
CONSUMER_NAME = os.getenv("AGENT2_CONSUMER_NAME", "agent2-worker")

MAX_RETRIES = 3
POLL_BLOCK_MS = 5000
REDIS_SOCKET_TIMEOUT_SECS = max(10, (POLL_BLOCK_MS // 1000) + 5)

_gemini_model: Any = None
_nlp: Any = None

_REQUIRED_FIELDS = {
    "event_type",
    "location",
    "corridor_affected",
    "severity",
    "disruption_type",
    "similar_historical_events",
}


def startup() -> None:
    global _gemini_model

    init_chroma()

    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel(GEMINI_MODEL)
        logger.info("Agent 2 Gemini initialized model=%s", GEMINI_MODEL)
    else:
        logger.warning("Gemini API key missing. Agent 2 will use fallback if needed.")


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
    if _gemini_model is None:
        return None

    for attempt in range(MAX_RETRIES):
        try:
            response = _gemini_model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
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


async def run_agent2() -> None:
    logger.info("Agent 2 started stream=%s group=%s consumer=%s", STREAM_IN, CONSUMER_GROUP, CONSUMER_NAME)

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
                        raw = fields.get("data", "{}")
                        event = json.loads(raw)

                        result = await extract_intelligence(event)

                        logger.info(
                            "Agent 2 processed event_id=%s confidence=%.4f",
                            result["event_id"],
                            result["confidence"],
                        )

                        # Optional downstream stream if Person A wires later:
                        # r.xadd("agent2outputs", {"data": json.dumps(result)})

                        r.xack(STREAM_IN, CONSUMER_GROUP, msg_id)

                    except Exception as exc:
                        logger.exception("Failed processing msg_id=%s error=%s", msg_id, exc)

            await asyncio.sleep(0)

        except redis.exceptions.TimeoutError:
            await asyncio.sleep(0)
        except Exception as exc:
            logger.exception("Agent 2 loop error: %s", exc)
            await asyncio.sleep(5)