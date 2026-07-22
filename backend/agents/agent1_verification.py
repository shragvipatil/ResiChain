# ============================================================
# ResiChain — Agent 1 Verification Layer
# Two-stage state machine: WATCH → CONFIRMED
# Consumes from events:raw, publishes to events:verified
# Fix 9: Event expiry for events older than 60 hours
#
# Day 11: hooks alerts.send_watch_email() on WATCH, and
# alerts.send_confirmed_sms() on CONFIRMED. Both are fire-and-forget —
# wrapped so an alert failure (missing users table, bad SMTP creds,
# unverified Twilio number, etc.) can NEVER break event verification
# itself. Alerts are a side effect of a state transition, not part of
# its correctness.
# ============================================================

import math
import logging
from datetime import datetime, timedelta

from db.redis_client import (
    consume_from_group,
    acknowledge_message,
    publish_verified_event,
)
from db.postgres_queries import insert_verified_event, insert_audit_event

logger = logging.getLogger(__name__)

# ---- Trust Score Configuration --------------------------
SOURCE_TRUST = {
    "UKMTO": 0.99,
    "GDELT": 0.71,
    "AlphaVantage_PriceAlert": 0.95,
    "EIA": 0.90,
    "ReliefWeb": 0.75,
}

# Day 19 (found by Person B, test_agent1_verification.py): UKMTO's entry
# used to also have "sanctions": 0.3, but _classify_event_type() checks
# "ukmto" in source BEFORE looking at headline content — so any event
# with source="UKMTO" is unconditionally classified "maritime", no
# matter what the headline says. That sanctions entry could never
# actually be looked up; removed rather than left as confusing dead
# config. This is intentional, not a bug to fix by reordering the
# classifier: UKMTO's whole mandate is maritime security advisories to
# mariners, so even a UKMTO notice that mentions sanctions is still
# structurally a maritime advisory, not a general sanctions-listing
# event the way a GDELT or EIA report might be — treating every UKMTO
# event as maritime-domain is the correct model, not an oversight.
DOMAIN_MULTIPLIERS = {
    "UKMTO": {
        "maritime": 1.0,
        "conflict": 0.8,
        "price": 0.2,
    },
    "GDELT": {
        "maritime": 0.7,
        "sanctions": 0.8,
        "conflict": 1.0,
        "price": 0.5,
    },
    "AlphaVantage_PriceAlert": {
        "maritime": 0.1,
        "sanctions": 0.2,
        "conflict": 0.1,
        "price": 1.0,
    },
    "EIA": {
        "maritime": 0.5,
        "sanctions": 0.6,
        "conflict": 0.5,
        "price": 1.0,
    },
}

WATCH_THRESHOLD = 0.45
CONFIRMED_THRESHOLD = 0.65
MIN_SOURCES_CONFIRMED = 2
DEDUP_WINDOW_MINUTES = 5
CORRIDOR_WINDOW_HOURS = 4
EVENT_EXPIRY_HOURS = 60

# ---- Active Events Store (in memory) --------------------
_active_corridor_events: dict = {}

# Tracks the last stage ("WATCH"/"CONFIRMED") actually published per
# corridor, so _evaluate_corridor_state can fire on genuine transitions
# only — see Fix for the alert/publish-spam bug below.
_last_published_stage: dict = {}


async def run_verification_cycle():
    """
    Main verification loop.
    Called by APScheduler every 30 seconds.
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

    try:
        if "T" in timestamp_str:
            event_time = datetime.fromisoformat(timestamp_str.replace("Z", ""))
        else:
            event_time = datetime.utcnow()
    except Exception:
        event_time = datetime.utcnow()

    hours_since = (datetime.utcnow() - event_time).total_seconds() / 3600
    recency_decay = math.exp(-0.05 * hours_since)

    trust_score = SOURCE_TRUST.get(source, 0.5)
    event_type = _classify_event_type(event)
    source_multipliers = DOMAIN_MULTIPLIERS.get(source, {})
    domain_multiplier = source_multipliers.get(event_type, 0.5)

    weighted_confidence = trust_score * domain_multiplier * recency_decay

    if corridor not in _active_corridor_events:
        _active_corridor_events[corridor] = []

    dedup_cutoff = datetime.utcnow() - timedelta(minutes=DEDUP_WINDOW_MINUTES)
    recent_same_source = [
        e for e in _active_corridor_events[corridor]
        if e["source"] == source and e["event_time"] > dedup_cutoff
    ]

    if recent_same_source:
        recent_same_source[0]["confidence"] = max(
            recent_same_source[0]["confidence"],
            weighted_confidence,
        )
        recent_same_source[0]["severity"] = max(
            recent_same_source[0]["severity"],
            severity,
        )
        logger.debug(f"Dedup: Updated existing {source} entry for {corridor}")
    else:
        _active_corridor_events[corridor].append({
            "source": source,
            "confidence": weighted_confidence,
            "severity": severity,
            "event_time": event_time,
            "raw_event": event,
        })

    await _evaluate_corridor_state(corridor)


async def _evaluate_corridor_state(corridor: str):
    """
    Evaluates current state for a corridor.
    Determines if WATCH or CONFIRMED threshold is crossed.

    Day 11: fires the corresponding alert on a genuine stage
    transition — WATCH gets an email, CONFIRMED gets an SMS (spec:
    "CONFIRMED state transition only, not WATCH"). Both calls are
    wrapped in try/except so an alerting failure never blocks
    publishing the verified event itself.
    """
    events = _active_corridor_events.get(corridor, [])
    if not events:
        return

    cutoff = datetime.utcnow() - timedelta(hours=CORRIDOR_WINDOW_HOURS)
    recent_events = [e for e in events if e["event_time"] > cutoff]

    if not recent_events:
        return

    total_confidence = sum(e["confidence"] for e in recent_events)
    n_sources = len(set(e["source"] for e in recent_events))
    avg_confidence = total_confidence / max(n_sources, 1)
    avg_confidence = min(1.0, avg_confidence)

    unique_sources = list(set(e["source"] for e in recent_events))
    max_severity = max(e["severity"] for e in recent_events)

    logger.info(
        f"Corridor {corridor}: confidence={avg_confidence:.3f}, "
        f"sources={unique_sources}, severity={max_severity}"
    )

    if n_sources >= MIN_SOURCES_CONFIRMED and avg_confidence >= CONFIRMED_THRESHOLD:
        stage = "CONFIRMED"
    elif avg_confidence >= WATCH_THRESHOLD or max_severity >= 5:
        stage = "WATCH"
    else:
        # Dropped back below WATCH — forget the remembered stage so that if
        # this corridor escalates again later, it's treated as a fresh
        # transition rather than "no change".
        _last_published_stage.pop(corridor, None)
        return

    if _last_published_stage.get(corridor) == stage:
        # Same stage as last time we published for this corridor — this
        # evaluation was triggered by another corroborating event, not a
        # genuine transition. Skip the duplicate publish/alert.
        logger.debug(
            f"Corridor {corridor}: still {stage}, no transition — "
            f"skipping duplicate publish/alert"
        )
        return
    _last_published_stage[corridor] = stage

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
                "severity": e["severity"],
            }
            for e in recent_events
        ],
    }

    await publish_verified_event(verified_event)
    await _write_verified_event_to_db(verified_event)

    logger.info(
        f"VERIFIED EVENT: {corridor} → {stage} "
        f"(confidence={avg_confidence:.3f}, sources={unique_sources})"
    )

    # ---- Day 11 alert hooks ----
    # Fire-and-forget: an alerting failure (missing users table,
    # SMTP/Twilio misconfiguration, etc.) must never affect the
    # verification pipeline's own correctness or retry logic.
    if stage == "WATCH":
        try:
            from services.alerts import send_watch_email
            await send_watch_email(
                corridor=corridor,
                risk_score=avg_confidence,
                source_count=n_sources,
                timestamp=verified_event["timestamp"],
            )
        except Exception as e:
            logger.error(f"Alert hook failed for WATCH ({corridor}): {e}")
    elif stage == "CONFIRMED":
        try:
            from services.alerts import send_confirmed_sms
            await send_confirmed_sms(
                corridor=corridor,
                risk_score=avg_confidence,
                playbook_status="pending_generation",
            )
        except Exception as e:
            logger.error(f"Alert hook failed for CONFIRMED ({corridor}): {e}")

        # Day 20 fix: CONFIRMEDALERT was never broadcast to the
        # dashboard at all — only the SMS side effect existed. The
        # frontend switches on this exact string (per CLAUDE.md /
        # AppContext.tsx, confirmed by Person B) to show the live
        # signal -> risk -> procurement -> playbook chain starting.
        # Separate try/except from the SMS call above, same reasoning
        # as Agent 3's _emit_risk_update fix: a WebSocket hiccup must
        # never block or get entangled with the SMS alert or the
        # verification pipeline's own correctness.
        try:
            from main import broadcast_to_dashboard
            await broadcast_to_dashboard("CONFIRMEDALERT", {
                "corridor": corridor,
                "confidence": round(avg_confidence, 4),
                "sources_confirming": unique_sources,
                "timestamp": verified_event["timestamp"],
            })
        except Exception as e:
            logger.error(f"CONFIRMEDALERT broadcast failed for {corridor}: {e}")


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
        insert_verified_event(
            event_json=event,
            corridor=event["corridor"],
            stage=event["stage"],
            confidence=event["confidence"],
        )
    except Exception as e:
        logger.error(f"DB write error for verified event: {e}")


async def run_event_expiry():
    """
    Hourly job that expires old events.
    Fix 9: Events with recency decay below 0.05 get archived.
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
            await _archive_expired_events(corridor, expired)
            expired_count += len(expired)

        _active_corridor_events[corridor] = active

        if not _active_corridor_events[corridor]:
            del _active_corridor_events[corridor]

    logger.info(f"Event expiry: Archived {expired_count} expired events")


async def _archive_expired_events(corridor: str, events: list):
    """Archives expired events to PostgreSQL audit_events table."""
    try:
        for event in events:
            insert_audit_event({
                "event_id": None,
                "source": event["source"],
                "corridor": corridor,
                "stage": "EXPIRED",
                "confidence": event["confidence"],
                "verified_at": event["event_time"],
                "archived_at": datetime.utcnow(),
            })
    except Exception as e:
        logger.error(f"Archive error: {e}")
