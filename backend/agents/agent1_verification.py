# ============================================================
# ResiChain — Agent 1 Verification Layer
# Two-stage state machine: WATCH → CONFIRMED
# Consumes from events:raw, publishes to events:verified
# Fix 9: Event expiry for events older than 60 hours
# ============================================================

import json
import math
import logging
from datetime import datetime, timedelta
from db.redis_client import (
    get_redis,
    consume_from_group,
    acknowledge_message,
    publish_verified_event
)
from db.postgres import get_db_pool

logger = logging.getLogger(__name__)

# ---- Trust Score Configuration --------------------------
# Source trust scores (how reliable is each source)
SOURCE_TRUST = {
    "UKMTO": 0.99,
    "GDELT": 0.71,
    "AlphaVantage_PriceAlert": 0.95,
    "EIA": 0.90,
    "ReliefWeb": 0.75
}

# Domain multipliers (how relevant is this source for this event type)
DOMAIN_MULTIPLIERS = {
    "UKMTO": {
        "maritime": 1.0,   # UKMTO is perfect for maritime events
        "sanctions": 0.3,  # UKMTO is not great for sanctions info
        "conflict": 0.8,
        "price": 0.2
    },
    "GDELT": {
        "maritime": 0.7,
        "sanctions": 0.8,
        "conflict": 1.0,   # GDELT is great for conflict events
        "price": 0.5
    },
    "AlphaVantage_PriceAlert": {
        "maritime": 0.1,
        "sanctions": 0.2,
        "conflict": 0.1,
        "price": 1.0       # Perfect for price events
    },
    "EIA": {
        "maritime": 0.5,
        "sanctions": 0.6,
        "conflict": 0.5,
        "price": 1.0
    }
}

# Thresholds
WATCH_THRESHOLD = 0.45
CONFIRMED_THRESHOLD = 0.65
MIN_SOURCES_CONFIRMED = 2
DEDUP_WINDOW_MINUTES = 5
CORRIDOR_WINDOW_HOURS = 4
EVENT_EXPIRY_HOURS = 60


# ---- Active Events Store (in memory) --------------------
# Format: {corridor: [{source, confidence, timestamp, ...}]}
_active_corridor_events: dict = {}


async def run_verification_cycle():
    """
    Main verification loop.
    Called by APScheduler every 30 seconds.
    
    Flow:
    1. Read new events from events:raw consumer group
    2. Calculate weighted confidence score
    3. Check for deduplication
    4. Update corridor state
    5. Determine WATCH or CONFIRMED
    6. Publish to events:verified if threshold crossed
    """
    messages = await consume_from_group("verifier_1", count=20)

    if not messages:
        return

    logger.info(f"Verification: Processing {len(messages)} new events")

    for msg_id, event in messages:
        try:
            await _process_event(event)
            await acknowledge_message(msg_id)
        except Exception as e:
            logger.error(f"Verification error for message {msg_id}: {e}")


async def _process_event(event: dict):
    """Processes a single raw event through the verification pipeline."""
    source = event.get("source", "UNKNOWN")
    corridor = event.get("corridor", "Unknown")
    severity = event.get("severity", 1)
    timestamp_str = event.get("timestamp", datetime.utcnow().isoformat())

    # Parse timestamp
    try:
        if "T" in timestamp_str:
            event_time = datetime.fromisoformat(
                timestamp_str.replace("Z", "")
            )
        else:
            event_time = datetime.utcnow()
    except Exception:
        event_time = datetime.utcnow()

    # Calculate recency decay: e^(-0.05 × hours_since_event)
    hours_since = (datetime.utcnow() - event_time).total_seconds() / 3600
    recency_decay = math.exp(-0.05 * hours_since)

    # Get trust score and domain multiplier
    trust_score = SOURCE_TRUST.get(source, 0.5)
    event_type = _classify_event_type(event)
    source_multipliers = DOMAIN_MULTIPLIERS.get(source, {})
    domain_multiplier = source_multipliers.get(event_type, 0.5)

    # Calculate this source's weighted confidence contribution
    weighted_confidence = trust_score * domain_multiplier * recency_decay

    # Store in active corridor events
    if corridor not in _active_corridor_events:
        _active_corridor_events[corridor] = []

    # Deduplication: check if same source reported same corridor recently
    dedup_cutoff = datetime.utcnow() - timedelta(minutes=DEDUP_WINDOW_MINUTES)
    recent_same_source = [
        e for e in _active_corridor_events[corridor]
        if e["source"] == source
        and e["event_time"] > dedup_cutoff
    ]

    if recent_same_source:
        # Update existing entry instead of adding duplicate
        recent_same_source[0]["confidence"] = max(
            recent_same_source[0]["confidence"],
            weighted_confidence
        )
        recent_same_source[0]["severity"] = max(
            recent_same_source[0]["severity"],
            severity
        )
        logger.debug(f"Dedup: Updated existing {source} entry for {corridor}")
    else:
        # Add new entry
        _active_corridor_events[corridor].append({
            "source": source,
            "confidence": weighted_confidence,
            "severity": severity,
            "event_time": event_time,
            "raw_event": event
        })

    # Now evaluate corridor state
    await _evaluate_corridor_state(corridor)


async def _evaluate_corridor_state(corridor: str):
    """
    Evaluates current state for a corridor.
    Determines if WATCH or CONFIRMED threshold is crossed.
    """
    events = _active_corridor_events.get(corridor, [])
    if not events:
        return

    # Only consider events within the 4-hour window
    cutoff = datetime.utcnow() - timedelta(hours=CORRIDOR_WINDOW_HOURS)
    recent_events = [e for e in events if e["event_time"] > cutoff]

    if not recent_events:
        return

    # Calculate combined confidence score
    # Formula: Σ(trust × domain_mult × recency_decay) / n_sources
    total_confidence = sum(e["confidence"] for e in recent_events)
    n_sources = len(set(e["source"] for e in recent_events))
    avg_confidence = total_confidence / max(n_sources, 1)

    # Cap at 1.0 (Fix 8 from flaws analysis)
    avg_confidence = min(1.0, avg_confidence)

    unique_sources = list(set(e["source"] for e in recent_events))
    max_severity = max(e["severity"] for e in recent_events)

    logger.info(
        f"Corridor {corridor}: confidence={avg_confidence:.3f}, "
        f"sources={unique_sources}, severity={max_severity}"
    )

    # Determine stage
    if n_sources >= MIN_SOURCES_CONFIRMED and avg_confidence >= CONFIRMED_THRESHOLD:
        stage = "CONFIRMED"
    elif avg_confidence >= WATCH_THRESHOLD or max_severity >= 5:
        stage = "WATCH"
    else:
        stage = "MONITOR"
        return  # Don't publish MONITOR events

    # Build verified event
    verified_event = {
        "corridor": corridor,
        "stage": stage,
        "confidence": round(avg_confidence, 4),
        "sources_confirming": unique_sources,
        "source_count": n_sources,
        "max_severity": max_severity,
        "timestamp": datetime.utcnow().isoformat(),
        "evidence": [
            {
                "source": e["source"],
                "confidence": round(e["confidence"], 4),
                "severity": e["severity"]
            }
            for e in recent_events
        ]
    }

    # Publish to events:verified stream
    await publish_verified_event(verified_event)

    # Write to PostgreSQL audit table
    await _write_verified_event_to_db(verified_event)

    logger.info(
        f"VERIFIED EVENT: {corridor} → {stage} "
        f"(confidence={avg_confidence:.3f}, "
        f"sources={unique_sources})"
    )


def _classify_event_type(event: dict) -> str:
    """Classifies event into a domain type for multiplier lookup."""
    source = event.get("source", "").lower()
    headline = event.get("headline", "").lower()

    if "ukmto" in source or "maritime" in headline or "vessel" in headline:
        return "maritime"
    if "sanction" in headline or "ofac" in source:
        return "sanctions"
    if "price" in source or "brent" in headline:
        return "price"
    return "conflict"


async def _write_verified_event_to_db(event: dict):
    """Writes verified event to PostgreSQL for audit trail."""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO verified_events
                (corridor, stage, confidence, sources_confirming,
                 source_count, max_severity, evidence, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            """,
                event["corridor"],
                event["stage"],
                event["confidence"],
                json.dumps(event["sources_confirming"]),
                event["source_count"],
                event["max_severity"],
                json.dumps(event.get("evidence", []))
            )
    except Exception as e:
        logger.error(f"DB write error for verified event: {e}")


# ---- Fix 9: Event Expiry --------------------------------
async def run_event_expiry():
    """
    Hourly job that expires old events.
    Fix 9: Events with recency decay below 0.05 get archived.
    0.05 threshold = approximately 60 hours after event.
    
    Prevents old events accumulating and degrading score accuracy.
    """
    logger.info("Event expiry: Checking for expired events...")
    expiry_cutoff = datetime.utcnow() - timedelta(hours=EVENT_EXPIRY_HOURS)
    expired_count = 0

    for corridor in list(_active_corridor_events.keys()):
        active = []
        expired = []

        for event in _active_corridor_events[corridor]:
            if event["event_time"] < expiry_cutoff:
                expired.append(event)
            else:
                active.append(event)

        if expired:
            # Archive expired events to PostgreSQL
            await _archive_expired_events(corridor, expired)
            expired_count += len(expired)

        _active_corridor_events[corridor] = active

        # Clean up empty corridors
        if not _active_corridor_events[corridor]:
            del _active_corridor_events[corridor]

    logger.info(f"Event expiry: Archived {expired_count} expired events")


async def _archive_expired_events(corridor: str, events: list):
    """Archives expired events to PostgreSQL audit_events table."""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            for event in events:
                await conn.execute("""
                    INSERT INTO audit_events
                    (corridor, source, confidence, severity,
                     event_time, archived_at, raw_event)
                    VALUES ($1, $2, $3, $4, $5, NOW(), $6)
                """,
                    corridor,
                    event["source"],
                    event["confidence"],
                    event["severity"],
                    event["event_time"],
                    json.dumps(event.get("raw_event", {}))
                )
    except Exception as e:
        logger.error(f"Archive error: {e}") 