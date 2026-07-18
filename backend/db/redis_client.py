# ============================================================
# ResiChain — Redis Client
# Central Redis boundary for streams, keys, consumer groups, TTLs
# ============================================================

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as aioredis
import redis.exceptions as _rex
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Canonical Redis names
# -------------------------------------------------------------------

RAW_EVENTS_STREAM = os.getenv("RAW_EVENTS_STREAM", "events:raw")
VERIFIED_EVENTS_STREAM = os.getenv("VERIFIED_EVENTS_STREAM", "events:verified")

RISK_STATE_KEY = os.getenv("RISK_STATE_KEY", "risk:state")
VESSELS_LIVE_KEY = os.getenv("VESSELS_LIVE_KEY", "vessels:live")
PRICES_LIVE_KEY = os.getenv("PRICES_LIVE_KEY", "prices:live")

VERIFICATION_GROUP = os.getenv("VERIFICATION_GROUP", "verification_group")

RISK_CACHE_TTL_SECONDS = int(os.getenv("RISK_CACHE_TTL_SECONDS", "300"))
VESSELS_LIVE_TTL_SECONDS = int(os.getenv("VESSELS_LIVE_TTL_SECONDS", "360"))
PRICES_LIVE_TTL_SECONDS = int(os.getenv("PRICES_LIVE_TTL_SECONDS", "360"))

RAW_EVENTS_MAXLEN = int(os.getenv("RAW_EVENTS_MAXLEN", "1000"))
VERIFIED_EVENTS_MAXLEN = int(os.getenv("VERIFIED_EVENTS_MAXLEN", "500"))

DEFAULT_RISK_STATE: Dict[str, float] = {
    "Hormuz": 0.34,
    "Red_Sea": 0.41,
    "Suez": 0.18,
    "Cape": 0.05,
}

_redis_client: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """Returns the global Redis async client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379"),
            decode_responses=True,
            # Day 17 resilience: transparently survive a momentary Redis drop
            # instead of surfacing an unhandled ConnectionError to the caller.
            #   - health_check_interval: ping the socket every 30s so a
            #     half-dead connection is detected and replaced before use.
            #   - socket_keepalive: OS-level keepalive on the TCP socket.
            #   - retry_on_timeout + retry: re-issue a command up to 3 times
            #     with exponential backoff on a connection/timeout error.
            # A brief Redis blip during the demo now recovers silently.
            health_check_interval=30,
            socket_keepalive=True,
            retry_on_timeout=True,
            retry=Retry(ExponentialBackoff(cap=2, base=0.1), retries=3),
            retry_on_error=[_rex.ConnectionError, _rex.TimeoutError],
        )
    return _redis_client


async def init_redis_streams() -> None:
    """
    Called on FastAPI startup.
    Verifies Redis connectivity, initializes default risk state,
    and ensures the verification consumer group exists.
    """
    r = await get_redis()
    await r.ping()

    await r.setex(
        RISK_STATE_KEY,
        RISK_CACHE_TTL_SECONDS,
        json.dumps(DEFAULT_RISK_STATE),
    )

    await setup_consumer_group()

    logger.info(
        "Redis initialised (raw_stream=%s, verified_stream=%s, risk_key=%s, group=%s)",
        RAW_EVENTS_STREAM,
        VERIFIED_EVENTS_STREAM,
        RISK_STATE_KEY,
        VERIFICATION_GROUP,
    )


# -------------------------------------------------------------------
# Streams
# -------------------------------------------------------------------

async def publish_event(event: dict) -> str:
    """
    Publishes an event to the raw events stream.
    Fix 1: Uses Redis Streams (xadd), not pub/sub.
    """
    r = await get_redis()
    entry_id = await r.xadd(
        RAW_EVENTS_STREAM,
        {"data": json.dumps(event)},
        maxlen=RAW_EVENTS_MAXLEN,
    )
    logger.info("Event published to %s. ID: %s", RAW_EVENTS_STREAM, entry_id)
    return entry_id


async def publish_verified_event(event: dict) -> str:
    """
    Publishes a WATCH or CONFIRMED event to the verified events stream.
    """
    r = await get_redis()
    entry_id = await r.xadd(
        VERIFIED_EVENTS_STREAM,
        {"data": json.dumps(event)},
        maxlen=VERIFIED_EVENTS_MAXLEN,
    )
    logger.info("Verified event published to %s. ID: %s", VERIFIED_EVENTS_STREAM, entry_id)
    return entry_id


async def consume_events(last_id: str = "0") -> List[Tuple[str, Dict[str, Any]]]:
    """
    Reads new events from the raw events stream since last_id.
    Returns list of (message_id, event_dict) tuples.
    """
    r = await get_redis()
    results = await r.xread(
        {RAW_EVENTS_STREAM: last_id},
        count=50,
        block=1000,
    )

    events: List[Tuple[str, Dict[str, Any]]] = []
    if results:
        for _stream_name, messages in results:
            for msg_id, msg_data in messages:
                event = json.loads(msg_data["data"])
                events.append((msg_id, event))
    return events


async def setup_consumer_group() -> None:
    """
    Creates the verification consumer group for the raw events stream.
    Consumer groups ensure messages are not lost if a consumer is busy.
    """
    r = await get_redis()
    try:
        await r.xgroup_create(
            RAW_EVENTS_STREAM,
            VERIFICATION_GROUP,
            id="0",
            mkstream=True,
        )
        logger.info(
            "Consumer group '%s' created on stream '%s'",
            VERIFICATION_GROUP,
            RAW_EVENTS_STREAM,
        )
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            logger.info(
                "Consumer group '%s' already exists on '%s' — skipping",
                VERIFICATION_GROUP,
                RAW_EVENTS_STREAM,
            )
        else:
            logger.error("Consumer group setup error: %s", exc)


async def consume_from_group(
    consumer_name: str,
    count: int = 10,
) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Reads messages from the raw events stream using the verification consumer group.
    Returns list of (message_id, event_dict) tuples.
    """
    r = await get_redis()
    try:
        results = await r.xreadgroup(
            groupname=VERIFICATION_GROUP,
            consumername=consumer_name,
            streams={RAW_EVENTS_STREAM: ">"},
            count=count,
            block=1000,
        )

        messages: List[Tuple[str, Dict[str, Any]]] = []
        if results:
            for _stream_name, msgs in results:
                for msg_id, msg_data in msgs:
                    event = json.loads(msg_data["data"])
                    messages.append((msg_id, event))
        return messages
    except Exception as exc:
        logger.error("Consumer group read error: %s", exc)
        return []


async def acknowledge_message(message_id: str) -> None:
    """
    Marks a message as processed in the verification consumer group.
    """
    r = await get_redis()
    await r.xack(RAW_EVENTS_STREAM, VERIFICATION_GROUP, message_id)


# -------------------------------------------------------------------
# Risk state cache
# -------------------------------------------------------------------

async def update_risk_state(risk_vector: dict) -> None:
    """
    Saves the latest corridor risk scores to Redis cache.
    TTL is refreshed on every Agent 3 update.
    """
    r = await get_redis()
    await r.setex(
        RISK_STATE_KEY,
        RISK_CACHE_TTL_SECONDS,
        json.dumps(risk_vector),
    )
    logger.info("Risk state updated in %s: %s", RISK_STATE_KEY, risk_vector)


async def get_risk_state() -> Dict[str, Any]:
    """
    Returns the current corridor risk scores from cache.
    If cache is expired, returns safe default values.
    """
    r = await get_redis()
    data = await r.get(RISK_STATE_KEY)
    if data:
        return json.loads(data)

    return {
        "Hormuz": 0.0,
        "Red_Sea": 0.0,
        "Suez": 0.0,
        "Cape": 0.0,
        "cache_expired": True,
    }


# -------------------------------------------------------------------
# Live vessels / prices cache helpers
# -------------------------------------------------------------------

async def set_vessels_live(payload: dict) -> None:
    r = await get_redis()
    await r.setex(
        VESSELS_LIVE_KEY,
        VESSELS_LIVE_TTL_SECONDS,
        json.dumps(payload),
    )
    logger.info("Updated %s", VESSELS_LIVE_KEY)


async def get_vessels_live() -> Dict[str, Any]:
    r = await get_redis()
    data = await r.get(VESSELS_LIVE_KEY)
    return json.loads(data) if data else {}


async def set_prices_live(payload: dict) -> None:
    r = await get_redis()
    await r.setex(
        PRICES_LIVE_KEY,
        PRICES_LIVE_TTL_SECONDS,
        json.dumps(payload),
    )
    logger.info("Updated %s", PRICES_LIVE_KEY)


async def get_prices_live() -> Dict[str, Any]:
    r = await get_redis()
    data = await r.get(PRICES_LIVE_KEY)
    return json.loads(data) if data else {}


# -------------------------------------------------------------------
# JWT blacklist
# -------------------------------------------------------------------

async def blacklist_token(jti: str, expires_in_seconds: int) -> None:
    """
    Adds a JWT token ID to the blacklist on logout.
    """
    r = await get_redis()
    await r.setex(f"blacklist:{jti}", expires_in_seconds, "revoked")


async def is_token_blacklisted(jti: str) -> bool:
    """
    Checks if a JWT token has been revoked.
    """
    r = await get_redis()
    return (await r.exists(f"blacklist:{jti}")) > 0 