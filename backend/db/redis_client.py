# ============================================================
# ResiChain — Redis Client
# Two namespaces:
#   events:raw  → Redis Stream (Fix 1 — persistent, not pub/sub)
#   risk:state  → Regular key with TTL (live risk score cache)
# ============================================================

import redis.asyncio as aioredis
import os
import json
import logging

logger = logging.getLogger(__name__)

_redis_client = None  # Global Redis client

async def get_redis():
    """Returns the global Redis async client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379"),
            decode_responses=True
        )
    return _redis_client

async def init_redis_streams():
    """
    Called on FastAPI startup.
    Creates the Redis Stream for events if it doesn't exist.
    """
    r = await get_redis()

    # Create the events stream if it doesn't exist
    # Redis Streams are auto-created on first xadd
    # But we verify connection here
    await r.ping()

    # Initialise risk state with default values
    default_risk_state = {
        "Hormuz": 0.34,
        "Red_Sea": 0.41,
        "Suez": 0.18,
        "Cape": 0.05
    }

    ttl = int(os.getenv("RISK_CACHE_TTL_SECONDS", 300))
    await r.setex(
        "risk:state",
        ttl,
        json.dumps(default_risk_state)
    )

    logger.info("Redis streams initialised. Default risk state set.")

# ---- Stream: Publish Event (Agent 1 uses this) --------------
async def publish_event(event: dict) -> str:
    """
    Publishes a verified event to the Redis Stream.
    Returns the stream entry ID.
    
    Fix 1: Using xadd (Stream) instead of publish (pub/sub)
    Events are persistent — agents won't miss them if busy.
    """
    r = await get_redis()
    entry_id = await r.xadd(
        "events:raw",
        {"data": json.dumps(event)},
        maxlen=1000  # Keep last 1000 events max
    )
    logger.info(f"Event published to stream. ID: {entry_id}")
    return entry_id

# ---- Stream: Consume Events (Agents 2+3 use this) -----------
async def consume_events(last_id: str = "0") -> list:
    """
    Reads new events from the stream since last_id.
    Returns list of (id, event_dict) tuples.
    
    Agents call this with their last processed ID
    so they never miss an event even if they were busy.
    """
    r = await get_redis()
    results = await r.xread(
        {"events:raw": last_id},
        count=50,
        block=1000  # Wait 1 second for new events
    )

    events = []
    if results:
        for stream_name, messages in results:
            for msg_id, msg_data in messages:
                event = json.loads(msg_data["data"])
                events.append((msg_id, event))
    return events

# ---- Cache: Update Risk State (Agent 3 uses this) -----------
async def update_risk_state(risk_vector: dict):
    """
    Saves the latest corridor risk scores to Redis cache.
    Dashboard reads this for live risk display.
    TTL = 5 minutes (refreshed every Agent 3 run)
    
    risk_vector example:
    {"Hormuz": 0.82, "Red_Sea": 0.87, "Suez": 0.41, "Cape": 0.12}
    """
    r = await get_redis()
    ttl = int(os.getenv("RISK_CACHE_TTL_SECONDS", 300))
    await r.setex("risk:state", ttl, json.dumps(risk_vector))
    logger.info(f"Risk state updated: {risk_vector}")

# ---- Cache: Get Risk State (Dashboard + Agents use this) ----
async def get_risk_state() -> dict:
    """
    Returns the current corridor risk scores from cache.
    If cache is expired, returns safe default values.
    """
    r = await get_redis()
    data = await r.get("risk:state")
    if data:
        return json.loads(data)
    # Cache expired — return safe defaults
    return {
        "Hormuz": 0.0,
        "Red_Sea": 0.0,
        "Suez": 0.0,
        "Cape": 0.0,
        "cache_expired": True
    }

# ---- Token Blacklist (JWT logout fix) -----------------------
async def blacklist_token(jti: str, expires_in_seconds: int):
    """
    Adds a JWT token ID to the blacklist on logout.
    Fix for JWT not being invalidated on logout.
    """
    r = await get_redis()
    await r.setex(f"blacklist:{jti}", expires_in_seconds, "revoked")

async def is_token_blacklisted(jti: str) -> bool:
    """Checks if a JWT token has been revoked."""
    r = await get_redis()
    return await r.exists(f"blacklist:{jti}") > 0 