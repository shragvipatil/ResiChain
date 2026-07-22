# ============================================================
# ResiChain — Agent 3: Corridor Risk Engine
# Consumes from events:verified Redis Stream
# Calculates corridor risk scores using 5 weighted factors
# Stores live risk vector in Redis risk:state
# Fix 8 applied: corridor_risk = min(1.0, raw_risk)
#
# UPDATED: swept all hardcoded Redis stream/key names to use the
# centralized constants/helpers from db/redis_client.py, per Person B's
# redis_client.py refactor. "agent3:last_run" and "brent:price:latest"
# are left as-is — both are single-file keys only Agent 3 itself ever
# reads/writes, so there's no cross-file naming risk to centralize.
# ============================================================

import json
import math
import logging
from datetime import datetime
from db.redis_client import get_redis, VERIFIED_EVENTS_STREAM, update_risk_state
from db.postgres_queries import get_connection

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    "military_incidents": 0.35,
    "conflict_escalation": 0.25,
    "sanctions_change":    0.25,
    "market_volatility":   0.10,
    "seasonal_risk":       0.05
}

SEASONAL_RISK_TABLE = {
    "Hormuz": {
        1: 0.1, 2: 0.1, 3: 0.1, 4: 0.1,
        5: 0.2, 6: 0.4, 7: 0.5, 8: 0.5,
        9: 0.3, 10: 0.1, 11: 0.1, 12: 0.1
    },
    "Red_Sea": {
        1: 0.2, 2: 0.2, 3: 0.2, 4: 0.2,
        5: 0.2, 6: 0.3, 7: 0.3, 8: 0.3,
        9: 0.2, 10: 0.2, 11: 0.2, 12: 0.2
    },
    "Suez": {
        1: 0.1, 2: 0.1, 3: 0.1, 4: 0.1,
        5: 0.1, 6: 0.1, 7: 0.1, 8: 0.1,
        9: 0.1, 10: 0.1, 11: 0.1, 12: 0.1
    },
    "Cape": {
        1: 0.1, 2: 0.1, 3: 0.1, 4: 0.2,
        5: 0.3, 6: 0.5, 7: 0.5, 8: 0.4,
        9: 0.3, 10: 0.2, 11: 0.1, 12: 0.1
    }
}

_current_weights = DEFAULT_WEIGHTS.copy()


async def run_agent3():
    """
    Main Agent 3 function.
    Called by APScheduler every 60 seconds.
    """
    logger.info("Agent 3: Starting risk calculation cycle...")

    try:
        r = await get_redis()
        results = await r.xread({VERIFIED_EVENTS_STREAM: "0"}, count=50)

        corridors_to_update = set()

        if results:
            for stream_name, messages in results:
                for msg_id, msg_data in messages:
                    event = json.loads(msg_data["data"])
                    corridor = event.get("corridor")
                    if corridor:
                        corridors_to_update.add(corridor)

        all_corridors = ["Hormuz", "Red_Sea", "Suez", "Cape"]
        risk_vector = {}

        for corridor in all_corridors:
            risk_score = await _calculate_corridor_risk(corridor)
            risk_vector[corridor] = round(risk_score, 4)

        risk_vector["updated_at"] = datetime.utcnow().isoformat()
        risk_vector["updated_corridors"] = list(corridors_to_update)

        # Uses the shared helper (RISK_STATE_KEY + RISK_CACHE_TTL_SECONDS
        # applied internally) instead of a raw setex call — same key,
        # same TTL, same behavior, just centralized.
        #
        # Demo freeze guard (see scripts/seed_demo_state.py): while the
        # demo:risk_freeze key exists, Agent 3 still computes the vector
        # and logs its heartbeat below, but does NOT overwrite risk:state.
        # Without this, the 60-second schedule wipes seeded/injected demo
        # values before the demo can use them — the exact race condition
        # that caused the two false "no blocked chokepoint" failures on
        # Day 12. The key auto-expires (30-min TTL), so normal operation
        # is untouched outside demo windows.
        frozen = await r.get("demo:risk_freeze")
        if frozen:
            logger.info(
                "Agent 3: demo:risk_freeze active — computed vector NOT "
                "written to risk:state (delete the key to resume live risk)"
            )
        else:
            await update_risk_state(risk_vector)

        # "agent3:last_run" is Agent 3's own key, not read anywhere else —
        # left as a local literal on purpose, per the single-file-key rule.
        await r.setex(
            "agent3:last_run",
            600,
            json.dumps({
                "timestamp": datetime.utcnow().isoformat(),
                "corridors_updated": list(corridors_to_update),
                "risk_vector": risk_vector
            })
        )

        logger.info(f"Agent 3: Risk vector updated: {risk_vector}")

        # Day 19 fix (found by Person B, test_agent3.py): _emit_risk_update
        # is a downstream notification side effect (WebSocket broadcast to
        # the dashboard) — not part of Agent 3's core job, which is
        # compute + persist the risk vector. It already has its own
        # internal try/except, but that protection vanishes the instant a
        # caller (a test, or a future refactor) mocks _emit_risk_update
        # directly: the mock replaces the whole function, internals
        # included, so an exception from a mocked side_effect propagates
        # straight past whatever guard used to live inside. You can't rely
        # on a callee's internal error handling to protect the caller once
        # the callee can be swapped out — the call site itself needs its
        # own guard. Wrapping it here means run_agent3() always returns
        # the already-computed, already-persisted risk_vector regardless
        # of what happens to the broadcast: a transient dashboard
        # disconnect should never make Agent 3 look like it crashed.
        try:
            await _emit_risk_update(risk_vector)
        except Exception as e:
            logger.error(f"Agent 3: risk update broadcast failed (non-fatal): {e}")

        return risk_vector

    except Exception as e:
        logger.error(f"Agent 3 error: {e}")
        return {}


async def _calculate_corridor_risk(corridor: str) -> float:
    """
    Calculates risk score for a single corridor.
    Fix 8: Result is capped at 1.0
    """
    weights = _current_weights

    f1 = await _score_military_incidents(corridor)
    f2 = await _score_conflict_escalation(corridor)
    f3 = await _score_sanctions_change(corridor)
    f4 = await _score_market_volatility()
    f5 = _score_seasonal_risk(corridor)

    factor_scores = {
        "military_incidents": f1,
        "conflict_escalation": f2,
        "sanctions_change": f3,
        "market_volatility": f4,
        "seasonal_risk": f5
    }

    logger.debug(f"Agent 3 factors for {corridor}: {factor_scores}")

    weighted_sum = sum(
        weights[factor] * score
        for factor, score in factor_scores.items()
    )

    days_since = await _get_days_since_last_event(corridor)
    temporal_decay = math.exp(-0.1 * days_since)

    baseline_adjustment = 1 + (
        factor_scores["sanctions_change"] * 0.1 +
        factor_scores["seasonal_risk"] * 0.05
    )

    raw_risk = weighted_sum * temporal_decay * baseline_adjustment
    corridor_risk = min(1.0, raw_risk)

    scores = list(factor_scores.values())
    mean_score = sum(scores) / len(scores)
    if mean_score > 0:
        std_dev = math.sqrt(
            sum((s - mean_score) ** 2 for s in scores) / len(scores)
        )
        cv = std_dev / mean_score
        confidence = max(0.0, min(1.0, 1 - cv))
    else:
        confidence = 0.5

    logger.debug(
        f"Agent 3: {corridor} risk={corridor_risk:.4f} "
        f"confidence={confidence:.4f} decay={temporal_decay:.4f}"
    )

    return corridor_risk


async def _score_military_incidents(corridor: str) -> float:
    try:
        r = await get_redis()
        results = await r.xrevrange(VERIFIED_EVENTS_STREAM, count=20)

        score = 0.0
        for msg_id, msg_data in results:
            event = json.loads(msg_data["data"])
            if event.get("corridor") != corridor:
                continue

            sources = event.get("sources_confirming", [])
            severity = event.get("max_severity", 0)

            if "UKMTO" in sources:
                score = max(score, severity / 10.0 * 1.0)
            if "GDELT" in sources:
                score = max(score, severity / 10.0 * 0.8)

        return min(1.0, score)

    except Exception as e:
        logger.error(f"Military incident scoring error: {e}")
        return 0.0


async def _score_conflict_escalation(corridor: str) -> float:
    try:
        r = await get_redis()
        results = await r.xrevrange(VERIFIED_EVENTS_STREAM, count=20)

        max_source_count = 0
        max_confidence = 0.0

        for msg_id, msg_data in results:
            event = json.loads(msg_data["data"])
            if event.get("corridor") != corridor:
                continue
            source_count = event.get("source_count", 0)
            confidence = event.get("confidence", 0.0)
            max_source_count = max(max_source_count, source_count)
            max_confidence = max(max_confidence, confidence)

        source_multiplier = min(1.0, max_source_count / 3.0)
        return min(1.0, max_confidence * source_multiplier)

    except Exception as e:
        logger.error(f"Conflict escalation scoring error: {e}")
        return 0.0


async def _score_sanctions_change(corridor: str) -> float:
    """
    Factor 3: Active sanctions change (weight 25%)
    Counts new OFAC entries in last 24 hours from ofac_sdn table.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) AS count FROM ofac_sdn
                    WHERE last_refreshed_at >= NOW() - INTERVAL '24 hours'
                """)
                row = cur.fetchone()
                new_entries = row["count"] if row else 0

        score = min(1.0, 0.1 + (new_entries / 100.0))
        return score

    except Exception as e:
        logger.error(f"Sanctions scoring error: {e}")
        return 0.1


async def _score_market_volatility() -> float:
    try:
        r = await get_redis()
        data = await r.get("brent:price:latest")

        if not data:
            return 0.1

        price_data = json.loads(data)
        change_pct = abs(price_data.get("change_pct", 0))
        score = min(1.0, change_pct / 10.0)
        return score

    except Exception as e:
        logger.error(f"Market volatility scoring error: {e}")
        return 0.1


def _score_seasonal_risk(corridor: str) -> float:
    current_month = datetime.utcnow().month
    corridor_table = SEASONAL_RISK_TABLE.get(corridor, {})
    return corridor_table.get(current_month, 0.1)


async def _get_days_since_last_event(corridor: str) -> float:
    try:
        r = await get_redis()
        results = await r.xrevrange(VERIFIED_EVENTS_STREAM, count=50)

        for msg_id, msg_data in results:
            event = json.loads(msg_data["data"])
            if event.get("corridor") == corridor:
                timestamp_str = event.get("timestamp", "")
                if timestamp_str:
                    event_time = datetime.fromisoformat(
                        timestamp_str.replace("Z", "")
                    )
                    delta = datetime.utcnow() - event_time
                    return delta.total_seconds() / 86400

        return 7.0

    except Exception:
        return 7.0


async def _emit_risk_update(risk_vector: dict):
    try:
        from main import broadcast_to_dashboard
        await broadcast_to_dashboard("RISK_STATE_UPDATED", {
            "corridors": {
                k: v for k, v in risk_vector.items()
                if k not in ["updated_at", "updated_corridors"]
            },
            "updated_at": risk_vector.get("updated_at"),
            "system_mode": _determine_system_mode(risk_vector)
        })
        logger.info("Agent 3: WebSocket risk update broadcast sent")
    except Exception as e:
        logger.error(f"WebSocket broadcast error: {e}")


def _is_numeric_score(value) -> bool:
    """
    True for real int/float values, EXCLUDING bool (bool is a subclass
    of int in Python — isinstance(True, (int, float)) is True). Found
    by Person B: a boolean metadata marker in risk:state was silently
    passing the old isinstance(v, (int, float)) filter as if it were a
    real corridor risk score.
    """
    return type(value) in (int, float)


def _determine_system_mode(risk_vector: dict) -> str:
    scores = [
        v for k, v in risk_vector.items()
        if k not in ["updated_at", "updated_corridors"]
        and _is_numeric_score(v)
    ]
    if not scores:
        return "NORMAL"
    max_score = max(scores)
    if max_score >= 0.65:
        return "CRISIS"
    elif max_score >= 0.45:
        return "WATCH"
    return "NORMAL"


async def update_risk_weights(new_weights: dict) -> dict:
    global _current_weights
    _current_weights = new_weights

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO audit_events
                    (event_id, source, corridor, stage, confidence, verified_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (
                    None,
                    "Agent3_WeightUpdate",
                    "SYSTEM",
                    "CONFIG",
                    1.0,
                ))
    except Exception as e:
        logger.error(f"Weight save error: {e}")

    new_risk_vector = await run_agent3()
    logger.info("Agent 3: Weights updated and risk recalculated")
    return new_risk_vector 