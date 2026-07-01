# ============================================================
# ResiChain — Agent 5: SPR Linear Programming Optimizer
# Uses scipy.optimize.linprog to find optimal drawdown schedule
# Fix 5: Returns fallback schedule if LP is infeasible
# All solved schedules stored in PostgreSQL spr_schedules
# ============================================================

import json
import logging
import uuid
from datetime import datetime
import numpy as np
from scipy.optimize import linprog
from db.redis_client import get_redis
from db.postgres import get_db_pool

logger = logging.getLogger(__name__)

# ---- Constants ------------------------------------------
HORIZON_DAYS = 30
MAX_DAILY_RELEASE_MBD = 0.5       # Physical release capacity
MIN_RESERVE_FRACTION = 0.4        # Must keep 40% of SPR
DEFAULT_SPR_MB = 38.0             # Total SPR volume (PPAC data)
DEFAULT_CONSUMPTION_MBD = 5.1     # India daily consumption (EIA)


async def run_agent5(
    import_gap_mbd: float = None,
    scenario_id: str = None
) -> dict:
    """
    Main Agent 5 function.
    Solves LP to find optimal 30-day SPR drawdown schedule.

    Called by Agent 4 (compound detection) when crisis confirmed.
    Also callable directly for testing.

    Returns dict with schedule and metadata.
    Fix 5: Never returns empty — always returns fallback if infeasible.
    """
    if scenario_id is None:
        scenario_id = f"spr_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    logger.info(f"Agent 5: Starting SPR optimization for scenario {scenario_id}")

    # ---- Get inputs from live data ----------------------
    spr_mb = await _get_spr_volume()
    consumption_mbd = DEFAULT_CONSUMPTION_MBD
    spot_premium_pct = await _get_spot_premium()

    # Use provided import gap or calculate from risk state
    if import_gap_mbd is None:
        import_gap_mbd = await _estimate_import_gap()

    logger.info(
        f"Agent 5 inputs: SPR={spr_mb}mb, "
        f"consumption={consumption_mbd}mbd, "
        f"gap={import_gap_mbd}mbd, "
        f"premium={spot_premium_pct}%"
    )

    # ---- Solve LP ---------------------------------------
    result = _solve_lp(
        spr_mb=spr_mb,
        consumption_mbd=consumption_mbd,
        import_gap_mbd=import_gap_mbd,
        spot_premium_pct=spot_premium_pct
    )

    # ---- Store to PostgreSQL ----------------------------
    await _store_schedule(result, scenario_id, {
        "spr_mb": spr_mb,
        "consumption_mbd": consumption_mbd,
        "import_gap_mbd": import_gap_mbd,
        "spot_premium_pct": spot_premium_pct
    })

    # ---- Cache in Redis for Agent 8 ---------------------
    r = await get_redis()
    await r.setex(
        f"spr:schedule:{scenario_id}",
        3600,
        json.dumps(result)
    )
    await r.setex(
        "spr:schedule:latest",
        3600,
        json.dumps(result)
    )

    # ---- Update agent run log ---------------------------
    await r.setex(
        "agent5:last_run",
        600,
        json.dumps({
            "timestamp": datetime.utcnow().isoformat(),
            "scenario_id": scenario_id,
            "status": result["status"],
            "confidence": result["confidence"]
        })
    )

    logger.info(
        f"Agent 5: Done. Status={result['status']}, "
        f"confidence={result['confidence']}"
    )
    return result


def _solve_lp(
    spr_mb: float,
    consumption_mbd: float,
    import_gap_mbd: float,
    spot_premium_pct: float
) -> dict:
    """
    Solves the LP optimization problem.

    Decision variables: x[i] = daily SPR release on day i (mbd)
    for i in 0..29 (30 days)

    Objective: minimize total cost
    = Σ(spr_depletion_cost × x[i]) + Σ(spot_premium_cost × gap_remaining[i])

    Constraints:
    1. x[i] <= MAX_DAILY_RELEASE_MBD (physical capacity)
    2. x[i] >= 0 (can't put oil back)
    3. Σ(x[i]) <= 0.6 × spr_mb (keep 40% reserve)
    4. x[i] >= import_gap_mbd - available_imports (meet daily demand)

    Fix 5: Returns fallback if result.status == 2 (infeasible)
    """
    n = HORIZON_DAYS

    # Cost coefficients
    # SPR depletion cost increases as reserve depletes
    spr_depletion_cost = 1.0
    spot_premium_cost = spot_premium_pct / 100.0

    # Objective: minimize weighted sum
    # Higher spot premium = more incentive to use SPR early
    c = np.array([
        spr_depletion_cost + spot_premium_cost * (1 - i/n)
        for i in range(n)
    ])

    # Inequality constraints: A_ub @ x <= b_ub
    A_ub = []
    b_ub = []

    # Constraint 1: Each day's release <= max capacity
    for i in range(n):
        row = [0.0] * n
        row[i] = 1.0
        A_ub.append(row)
        b_ub.append(MAX_DAILY_RELEASE_MBD)

    # Constraint 2: Total release <= 60% of SPR
    max_total_release = 0.6 * spr_mb
    A_ub.append([1.0] * n)
    b_ub.append(max_total_release)

    A_ub = np.array(A_ub)
    b_ub = np.array(b_ub)

    # Bounds: each day's release between 0 and max
    # Also must cover the import gap each day
    min_daily = max(0.0, import_gap_mbd - (consumption_mbd - import_gap_mbd))
    bounds = [(min_daily, MAX_DAILY_RELEASE_MBD) for _ in range(n)]

    # Solve
    result = linprog(
        c,
        A_ub=A_ub,
        b_ub=b_ub,
        bounds=bounds,
        method="highs"
    )

    # ---- Fix 5: Handle infeasibility --------------------
    if result.status == 2:
        logger.warning(
            "Agent 5: LP infeasible — supply gap exceeds capacity. "
            "Returning fallback schedule."
        )
        return _get_fallback_schedule(
            spr_mb, consumption_mbd, import_gap_mbd, spot_premium_pct
        )

    # ---- Build output from successful solve -------------
    daily_schedule = result.x.tolist()
    total_release = sum(daily_schedule)
    avg_daily = total_release / n
    spr_remaining = spr_mb - total_release
    days_cover_after = spr_remaining / consumption_mbd if consumption_mbd > 0 else 0

    # Confidence: based on how much headroom we have
    headroom_fraction = (max_total_release - total_release) / max_total_release
    confidence = min(0.95, 0.5 + headroom_fraction * 0.5)

    return {
        "status": "optimal",
        "confidence": round(confidence, 4),
        "critical_warning": None,
        "scenario": {
            "daily_schedule_mbd": [round(x, 4) for x in daily_schedule],
            "avg_daily_drawdown_mbd": round(avg_daily, 4),
            "total_release_mb": round(total_release, 2),
            "duration_days": n,
            "spr_start_mb": spr_mb,
            "spr_end_mb": round(spr_remaining, 2),
            "days_cover_after": round(days_cover_after, 1),
            "import_gap_covered_pct": round(
                min(100, (avg_daily / import_gap_mbd * 100))
                if import_gap_mbd > 0 else 100,
                1
            )
        },
        "inputs": {
            "import_gap_mbd": import_gap_mbd,
            "spr_total_mb": spr_mb,
            "daily_consumption_mbd": consumption_mbd,
            "spot_premium_pct": spot_premium_pct,
            "horizon_days": n
        },
        "generated_at": datetime.utcnow().isoformat()
    }


def _get_fallback_schedule(
    spr_mb: float,
    consumption_mbd: float,
    import_gap_mbd: float,
    spot_premium_pct: float
) -> dict:
    """
    Fix 5: Returns maximum release fallback when LP is infeasible.
    This means supply gap exceeds what SPR + imports can cover.
    Agent 8 displays this as a red critical banner in the playbook.
    """
    n = HORIZON_DAYS
    max_release = MAX_DAILY_RELEASE_MBD
    total_release = max_release * n
    spr_remaining = max(0, spr_mb - total_release)
    days_cover_after = spr_remaining / consumption_mbd if consumption_mbd > 0 else 0

    return {
        "status": "fallback_infeasible",
        "confidence": 0.0,
        "critical_warning": (
            "CRITICAL: Supply gap exceeds combined SPR and import capacity. "
            "Emergency rationing required. "
            f"Import gap ({import_gap_mbd:.2f} mbd) cannot be covered by "
            f"maximum SPR release ({max_release:.2f} mbd/day). "
            "Immediate government intervention required."
        ),
        "scenario": {
            "daily_schedule_mbd": [max_release] * n,
            "avg_daily_drawdown_mbd": max_release,
            "total_release_mb": round(total_release, 2),
            "duration_days": n,
            "spr_start_mb": spr_mb,
            "spr_end_mb": round(spr_remaining, 2),
            "days_cover_after": round(days_cover_after, 1),
            "import_gap_covered_pct": round(
                (max_release / import_gap_mbd * 100)
                if import_gap_mbd > 0 else 0,
                1
            )
        },
        "inputs": {
            "import_gap_mbd": import_gap_mbd,
            "spr_total_mb": spr_mb,
            "daily_consumption_mbd": consumption_mbd,
            "spot_premium_pct": spot_premium_pct,
            "horizon_days": n
        },
        "generated_at": datetime.utcnow().isoformat()
    }


# ---- Data Fetching Helpers ------------------------------
async def _get_spr_volume() -> float:
    """
    Gets current SPR volume from Neo4j.
    Falls back to PPAC default (38mb) if Neo4j unavailable.
    """
    try:
        from db.neo4j_client import get_neo4j_driver
        driver = await get_neo4j_driver()
        async with driver.session() as session:
            result = await session.run("""
                MATCH (sf:StorageFacility {type: 'SPR'})
                RETURN sum(sf.capacity_mb) as total_spr
            """)
            record = await result.single()
            if record and record["total_spr"]:
                return float(record["total_spr"])
    except Exception as e:
        logger.warning(f"Neo4j SPR fetch failed: {e}. Using default.")
    return DEFAULT_SPR_MB


async def _get_spot_premium() -> float:
    """Gets current spot premium from Redis price cache."""
    try:
        r = await get_redis()
        data = await r.get("brent:price:latest")
        if data:
            price_info = json.loads(data)
            change_pct = abs(price_info.get("change_pct", 0))
            return min(50.0, change_pct * 3)
    except Exception:
        pass
    return 8.0  # Default 8% spot premium during crisis


async def _estimate_import_gap() -> float:
    """
    Estimates import gap from current corridor risk scores.
    Higher risk = more disruption = larger gap.
    """
    try:
        r = await get_redis()
        data = await r.get("risk:state")
        if data:
            risk = json.loads(data)
            # Weighted by how much oil passes through each corridor
            hormuz_share = 0.48  # 48% of India's imports via Hormuz
            red_sea_share = 0.15
            hormuz_risk = risk.get("Hormuz", 0.3)
            red_sea_risk = risk.get("Red_Sea", 0.3)
            disrupted_fraction = (
                hormuz_share * hormuz_risk +
                red_sea_share * red_sea_risk
            )
            return round(disrupted_fraction * DEFAULT_CONSUMPTION_MBD, 3)
    except Exception:
        pass
    return 1.2  # Default: moderate disruption


async def _store_schedule(
    schedule: dict,
    scenario_id: str,
    inputs: dict
):
    """Stores SPR schedule to PostgreSQL for audit trail."""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            scenario = schedule.get("scenario", {})
            await conn.execute("""
                INSERT INTO spr_schedules
                (scenario_id, status, confidence, critical_warning,
                 daily_drawdown_mbd, duration_days, total_release_mb,
                 spr_start_mb, daily_consumption_mbd, import_gap_mbd,
                 spot_premium_pct, schedule_json, input_params)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """,
                scenario_id,
                schedule.get("status"),
                schedule.get("confidence"),
                schedule.get("critical_warning"),
                scenario.get("avg_daily_drawdown_mbd"),
                scenario.get("duration_days"),
                scenario.get("total_release_mb"),
                scenario.get("spr_start_mb"),
                inputs.get("consumption_mbd"),
                inputs.get("import_gap_mbd"),
                inputs.get("spot_premium_pct"),
                json.dumps(scenario),
                json.dumps(inputs)
            )
    except Exception as e:
        logger.error(f"SPR schedule store error: {e}") 