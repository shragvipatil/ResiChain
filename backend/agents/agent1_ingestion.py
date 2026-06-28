# ============================================================
# ResiChain — Agent 1: Event Ingestion Orchestrator
# Coordinates all 4 data source clients
# Two-stage verification: WATCH → CONFIRMED
# ============================================================

import logging
import json
from datetime import datetime
from db.redis_client import get_redis, consume_events
from agents.clients.gdelt_client import fetch_gdelt_events
from agents.clients.ukmto_client import fetch_ukmto_alerts
from agents.clients.ofac_client import download_and_store_ofac
from agents.clients.alphavantage_client import fetch_brent_price_alert

logger = logging.getLogger(__name__)

# Two-stage verification thresholds
WATCH_THRESHOLD = 0.45
CONFIRMED_THRESHOLD = 0.65

# Minimum sources to confirm an event
MIN_SOURCES_FOR_CONFIRMED = 2


async def run_agent1_poll():
    """
    Main Agent 1 polling function.
    Called by APScheduler every 5 minutes.
    
    Flow:
    1. Poll all sources simultaneously
    2. Aggregate events by corridor
    3. Apply two-stage verification
    4. Update system mode in Redis
    """
    logger.info("Agent 1: Starting poll cycle...")
    start_time = datetime.utcnow()

    try:
        # Poll all sources
        gdelt_events = await fetch_gdelt_events()
        ukmto_events = await fetch_ukmto_alerts()
        price_alert = await fetch_brent_price_alert()

        all_events = gdelt_events + ukmto_events
        if price_alert.get("alert_triggered"):
            all_events.append(price_alert)

        # Group events by corridor
        corridor_events = {}
        for event in all_events:
            corridor = event.get("corridor", "Unknown")
            if corridor not in corridor_events:
                corridor_events[corridor] = []
            corridor_events[corridor].append(event)

        # Two-stage verification per corridor
        redis = await get_redis()
        alerts = []

        for corridor, events in corridor_events.items():
            sources = set(e.get("source", "") for e in events)
            source_count = len(sources)
            max_severity = max(e.get("severity", 0) for e in events)
            avg_confidence = sum(
                e.get("raw_confidence", 0.5) for e in events
            ) / len(events)

            # Determine stage
            if source_count >= MIN_SOURCES_FOR_CONFIRMED and avg_confidence >= CONFIRMED_THRESHOLD:
                stage = "CONFIRMED"
            elif avg_confidence >= WATCH_THRESHOLD or max_severity >= 5:
                stage = "WATCH"
            else:
                stage = "MONITOR"

            alert = {
                "corridor": corridor,
                "stage": stage,
                "source_count": source_count,
                "sources": list(sources),
                "max_severity": max_severity,
                "avg_confidence": round(avg_confidence, 3),
                "event_count": len(events),
                "timestamp": datetime.utcnow().isoformat()
            }
            alerts.append(alert)

            logger.info(
                f"Agent 1: {corridor} → {stage} "
                f"({source_count} sources, severity {max_severity})"
            )

        # Update system mode
        system_mode = _determine_system_mode(alerts)
        await redis.setex(
            "system:mode",
            300,
            system_mode
        )

        # Log run to Redis
        duration_ms = int(
            (datetime.utcnow() - start_time).total_seconds() * 1000
        )
        await redis.setex(
            "agent1:last_run",
            600,
            json.dumps({
                "timestamp": start_time.isoformat(),
                "duration_ms": duration_ms,
                "events_found": len(all_events),
                "corridors_active": len(corridor_events),
                "system_mode": system_mode
            })
        )

        logger.info(
            f"Agent 1: Poll complete. "
            f"{len(all_events)} events, mode={system_mode}, "
            f"{duration_ms}ms"
        )
        return alerts

    except Exception as e:
        logger.error(f"Agent 1 poll error: {e}")
        return []


def _determine_system_mode(alerts: list) -> str:
    """
    Determines overall system mode from corridor alerts.
    NORMAL → WATCH → CONFIRMED → CRISIS
    """
    if not alerts:
        return "NORMAL"

    stages = [a["stage"] for a in alerts]

    if "CONFIRMED" in stages:
        return "CRISIS"
    elif "WATCH" in stages:
        return "WATCH"
    else:
        return "NORMAL"


async def run_agent1_demo_inject(corridor: str = "Hormuz", severity: int = 8):
    """
    DEMO USE ONLY.
    Injects a fake crisis event to trigger the demo flow.
    Called at Minute 2 of the demo presentation.
    """
    from db.redis_client import publish_event

    # First event — GDELT signal
    await publish_event({
        "source": "GDELT",
        "headline": "Iran threatens closure of Strait of Hormuz following US sanctions escalation",
        "corridor": corridor,
        "severity": severity,
        "raw_confidence": 0.78,
        "timestamp": datetime.utcnow().isoformat(),
        "demo": True
    })

    logger.info(f"Demo: Injected GDELT event for {corridor}")

    # Second event — UKMTO confirmation (10 seconds later)
    import asyncio
    await asyncio.sleep(10)

    await publish_event({
        "source": "UKMTO",
        "headline": "Maritime security advisory: increased naval activity near Strait of Hormuz",
        "corridor": corridor,
        "severity": severity - 1,
        "raw_confidence": 0.99,
        "timestamp": datetime.utcnow().isoformat(),
        "demo": True
    })

    logger.info(f"Demo: Injected UKMTO confirmation for {corridor} — CONFIRMED state triggered") 