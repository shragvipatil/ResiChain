# ============================================================
# ResiChain — Agent 3: Corridor Risk Engine
# Consumes from events:verified Redis Stream
# Calculates corridor risk scores using 5 weighted factors
# Stores live risk vector in Redis risk:state
# Fix 8 applied: corridor_risk = min(1.0, raw_risk)
# ============================================================

import json
import math
import logging
from datetime import datetime, timedelta
from db.redis_client import get_redis, publish_verified_event
from db.postgres import get_db_pool

logger = logging.getLogger(__name__)

# ---- Risk Factor Weights (must sum to 1.0) ---------------
DEFAULT_WEIGHTS = {
    "military_incidents": 0.35,
    "conflict_escalation": 0.25,
    "sanctions_change":    0.25,
    "market_volatility":   0.10,
    "seasonal_risk":       0.05
}

# ---- Seasonal Risk Table --------------------------------
# Pre-computed month × corridor lookup
# Based on historical AIS delay patterns
# Hormuz: summer fog season (Jun-Sep)
# Cape: southern hemisphere winter storms (Jun-Aug)
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

# ---- In-memory weights store ----------------------------
# Gets updated when analyst changes weights via dashboard
_current_weights = DEFAULT_WEIGHTS.copy()


async def run_agent3():
    """
    Main Agent 3 function.
    Called by APScheduler every 60 seconds.
    Also triggered directly when a CONFIRMED event arrives.

    Flow:
    1. Read new verified events from events:verified stream
    2. For each corridor with new events — recalculate risk
    3. Store updated risk vector in Redis
    4. Emit WebSocket update to dashboard
    """
    logger.info("Agent 3: Starting risk calculation cycle...")

    try:
        # Read latest verified events from Redis Stream
        r = await get_redis()
        results = await r.xread(
            {"events:verified": "0"},
            count=50
        )

        corridors_to_update = set()

        if results:
            for stream_name, messages in results:
                for msg_id, msg_data in messages:
                    event = json.loads(msg_data["data"])
                    corridor = event.get("corridor")
                    if corridor:
                        corridors_to_update.add(corridor)

        # Always recalculate all corridors to apply decay
        all_corridors = ["Hormuz", "Red_Sea", "Suez", "Cape"]
        risk_vector = {}

        for corridor in all_corridors:
            risk_score = await _calculate_corridor_risk(corridor)
            risk_vector[corridor] = round(risk_score, 4)

        # Add metadata
        risk_vector["updated_at"] = datetime.utcnow().isoformat()
        risk_vector["updated_corridors"] = list(corridors_to_update)

        # Store in Redis with 5 minute TTL
        await r.setex(
            "risk:state",
            300,
            json.dumps(risk_vector)
        )

        # Store run timestamp
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

        # Emit WebSocket update to dashboard
        await _emit_risk_update(risk_vector)

        return risk_vector

    except Exception as e:
        logger.error(f"Agent 3 error: {e}")
        return {}


async def _calculate_corridor_risk(corridor: str) -> float:
    """
    Calculates risk score for a single corridor.

    Formula:
    corridor_risk = Σ(weight_i × factor_score_i)
                    × temporal_decay
                    × baseline_adjustment

    Fix 8: Result is capped at 1.0
    """
    weights = _current_weights

    # Score each of the 5 factors
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

    # Weighted sum
    weighted_sum = sum(
        weights[factor] * score
        for factor, score in factor_scores.items()
    )

    # Temporal decay based on days since last event
    days_since = await _get_days_since_last_event(corridor)
    temporal_decay = math.exp(-0.1 * days_since)

    # Baseline adjustment (sanctions + seasonal)
    baseline_adjustment = 1 + (
        factor_scores["sanctions_change"] * 0.1 +
        factor_scores["seasonal_risk"] * 0.05
    )

    # Final formula
    raw_risk = weighted_sum * temporal_decay * baseline_adjustment

    # Fix 8 — CRITICAL: cap at 1.0
    corridor_risk = min(1.0, raw_risk)

    # Calculate confidence (1 - coefficient of variation)
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
        f"confidence={confidence:.4f} "
        f"decay={temporal_decay:.4f}"
    )

    return corridor_risk


# ---- Five Factor Scoring Functions ----------------------

async def _score_military_incidents(corridor: str) -> float:
    """
    Factor 1: Military incidents/attacks (weight 35%)
    Scored from UKMTO advisories and GDELT event codes 19-20.
    Returns 0.0 to 1.0
    """
    try:
        r = await get_redis()

        # Check recent verified events for this corridor
        results = await r.xrevrange(
            "events:verified",
            count=20
        )

        score = 0.0
        for msg_id, msg_data in results:
            event = json.loads(msg_data["data"])
            if event.get("corridor") != corridor:
                continue

            sources = event.get("sources_confirming", [])
            severity = event.get("max_severity", 0)

            # UKMTO advisory = high military signal
            if "UKMTO" in sources:
                score = max(score, severity / 10.0 * 1.0)

            # GDELT conflict event codes
            if "GDELT" in sources:
                score = max(score, severity / 10.0 * 0.8)

        return min(1.0, score)

    except Exception as e:
        logger.error(f"Military incident scoring error: {e}")
        return 0.0


async def _score_conflict_escalation(corridor: str) -> float:
    """
    Factor 2: Conflict escalation signal (weight 25%)
    Based on multiple sources confirming same corridor.
    More sources = higher escalation signal.
    Returns 0.0 to 1.0
    """
    try:
        r = await get_redis()
        results = await r.xrevrange("events:verified", count=20)

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

        # More independent sources = higher escalation signal
        source_multiplier = min(1.0, max_source_count / 3.0)
        return min(1.0, max_confidence * source_multiplier)

    except Exception as e:
        logger.error(f"Conflict escalation scoring error: {e}")
        return 0.0


async def _score_sanctions_change(corridor: str) -> float:
    """
    Factor 3: Active sanctions change (weight 25%)
    Based on new OFAC SDN entries today vs yesterday.
    Returns 0.0 to 1.0
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Count entries added in last 24 hours
            new_entries = await conn.fetchval("""
                SELECT COUNT(*) FROM ofac_sanctions
                WHERE updated_at >= NOW() - INTERVAL '24 hours'
            """)

        if new_entries is None:
            return 0.1

        # Normalize: 0 new entries = 0.1 baseline,
        # 100+ new entries = 1.0
        score = min(1.0, 0.1 + (new_entries / 100.0))
        return score

    except Exception as e:
        logger.error(f"Sanctions scoring error: {e}")
        return 0.1


async def _score_market_volatility() -> float:
    """
    Factor 4: Market volatility (weight 10%)
    From Brent price change cached in Redis.
    Returns 0.0 to 1.0
    """
    try:
        r = await get_redis()
        data = await r.get("brent:price:latest")

        if not data:
            return 0.1

        price_data = json.loads(data)
        change_pct = abs(price_data.get("change_pct", 0))

        # 5% move = 0.5 score, 10%+ move = 1.0 score
        score = min(1.0, change_pct / 10.0)
        return score

    except Exception as e:
        logger.error(f"Market volatility scoring error: {e}")
        return 0.1


def _score_seasonal_risk(corridor: str) -> float:
    """
    Factor 5: Seasonal risk (weight 5%)
    From pre-computed month × corridor lookup table.
    Returns 0.0 to 1.0
    """
    current_month = datetime.utcnow().month
    corridor_table = SEASONAL_RISK_TABLE.get(corridor, {})
    return corridor_table.get(current_month, 0.1)


async def _get_days_since_last_event(corridor: str) -> float:
    """
    Returns days since last verified event for this corridor.
    Used for temporal decay calculation.
    If no recent events, returns 7 days (low decay baseline).
    """
    try:
        r = await get_redis()
        results = await r.xrevrange("events:verified", count=50)

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

        return 7.0  # Default: 7 days ago = significant decay

    except Exception as e:
        return 7.0


# ---- WebSocket Broadcast --------------------------------
async def _emit_risk_update(risk_vector: dict):
    """
    Broadcasts risk update to all connected dashboard clients.
    Person C's WebSocket hook receives this and updates UI.
    """
    try:
        from main import broadcast_to_dashboard
        await broadcast_to_dashboard("risk_update", {
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


def _determine_system_mode(risk_vector: dict) -> str:
    """Determines overall system mode from risk scores."""
    crisis_threshold = 0.65
    watch_threshold = 0.45

    scores = [
        v for k, v in risk_vector.items()
        if k not in ["updated_at", "updated_corridors"]
        and isinstance(v, (int, float))
    ]

    if not scores:
        return "NORMAL"

    max_score = max(scores)

    if max_score >= crisis_threshold:
        return "CRISIS"
    elif max_score >= watch_threshold:
        return "WATCH"
    return "NORMAL"


# ---- Weight Management ----------------------------------
async def update_risk_weights(new_weights: dict) -> dict:
    """
    Updates risk factor weights and immediately recalculates
    the risk vector with the new weights.
    Called by PATCH /api/risk-weights endpoint.
    """
    global _current_weights
    _current_weights = new_weights

    # Save to PostgreSQL for persistence
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO agent_runs
                (agent_name, status, output_summary)
                VALUES ('Agent3_WeightUpdate', 'complete', $1)
            """, json.dumps(new_weights))
    except Exception as e:
        logger.error(f"Weight save error: {e}")

    # Immediately recalculate with new weights
    new_risk_vector = await run_agent3()
    logger.info(f"Agent 3: Weights updated and risk recalculated")
    return new_risk_vector 