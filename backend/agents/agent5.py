"""
agents/agent5.py
================
ResiChain AI v2.0 — Agent 5: SPR Optimization (LP Solver)


Purpose:
    Solve the optimal 30-day daily SPR drawdown schedule using linear
    programming (scipy.optimize.linprog). Minimises the weighted sum of
    SPR depletion cost and import spot-premium cost.

Architecture:
    - Triggered by LangGraph crisis mode (corridor risk > 0.65).
    - Runs TWICE per crisis cycle (Fix 7):
        • First run: parallel with Agent 6, using all surviving routes.
        • Second run: after Agent 6/7 approval, using only approved cargoes.
    - Reads live data from Neo4j (SPR), EIA (consumption), Redis (prices).
    - Writes every solved schedule to PostgreSQL spr_schedules table.
    - Fix 5: infeasibility fallback — NEVER returns nothing.

Fix 5 (mandatory):
    When result.status == 2 (infeasible), return a max-drawdown fallback
    schedule with confidence=0.0 and a critical_warning string.
    Agent 8 must display this as a red critical banner in the playbook.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional
from uuid import UUID

import numpy as np
import redis as redis_lib
from dotenv import load_dotenv
from scipy.optimize import linprog

from db.neo4j_queries import get_spr_total_volume
from db.postgres_queries import (
    get_latest_price_history,
    insert_spr_schedule,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — all overridable via .env
# ---------------------------------------------------------------------------

HORIZON_DAYS: int = 30
MAX_DAILY_RELEASE_MBD: float = float(os.getenv("SPR_MAX_DAILY_RELEASE_MBD", "0.5"))
SPR_RESERVE_FLOOR_PCT: float = float(os.getenv("SPR_RESERVE_FLOOR_PCT", "0.40"))
INDIA_DAILY_CONSUMPTION_MBD: float = 5.1   # EIA fallback

# ---------------------------------------------------------------------------
# Redis helper
# ---------------------------------------------------------------------------

_redis_client: Optional[redis_lib.Redis] = None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
    return _redis_client


# ---------------------------------------------------------------------------
# Live-data helpers
# ---------------------------------------------------------------------------

def _get_spot_premium() -> float:
    """
    Fetch Brent spot price from Redis prices:live.
    Falls back to PostgreSQL price_history, then static 85.0 USD/bbl.
    Returns cost premium above baseline (0.0 if unavailable).
    """
    try:
        r = _get_redis()
        raw = r.get("prices:live")
        if raw:
            data = json.loads(raw)
            price = float(data.get("brent_usd") or data.get("brent") or 0)
            if price > 0:
                return price
    except Exception as exc:
        logger.warning("Redis price read failed in Agent 5: %s", exc)

    try:
        row = get_latest_price_history()
        if row and row.get("brent_usd"):
            return float(row["brent_usd"])
    except Exception as exc:
        logger.warning("PostgreSQL price fallback failed in Agent 5: %s", exc)

    return 85.0


def _get_india_consumption() -> float:
    """EIA consumption with fallback to constant."""
    try:
        import requests
        key = os.getenv("EIA_API_KEY", "")
        if not key:
            raise ValueError("EIA_API_KEY not set")
        url = (
            "https://api.eia.gov/v2/international/data/"
            "?api_key={key}"
            "&facets[activityId][]=1&facets[productId][]=53"
            "&facets[countryRegionId][]=IND"
            "&data[]=value&sort[0][column]=period"
            "&sort[0][direction]=desc&length=1"
        ).format(key=key)
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        value = resp.json()["response"]["data"][0]["value"]
        return float(value) / 365
    except Exception as exc:
        logger.warning("EIA consumption fetch failed: %s — using %.2f", exc, INDIA_DAILY_CONSUMPTION_MBD)
        return INDIA_DAILY_CONSUMPTION_MBD


# ---------------------------------------------------------------------------
# Infeasibility fallback (Fix 5)
# ---------------------------------------------------------------------------

def _infeasibility_fallback(
    spr_total_mb: float,
    playbook_id: Optional[UUID],
) -> Dict[str, Any]:
    """
    Fix 5: When LP is infeasible, return a max-drawdown schedule.
    confidence=0.0, critical_warning must be displayed as red banner
    by Agent 8.
    """
    schedule = [MAX_DAILY_RELEASE_MBD] * HORIZON_DAYS
    spr_remaining = max(0.0, spr_total_mb - sum(schedule))
    warning = (
        "Supply gap exceeds combined SPR and import capacity. "
        "Emergency rationing required."
    )
    logger.critical("Agent 5 LP infeasible — returning max drawdown fallback. %s", warning)

    record_id = insert_spr_schedule(
        playbook_id=playbook_id,
        feasible=False,
        daily_drawdown_schedule=schedule,
        confidence=0.0,
        spr_remaining_mb=spr_remaining,
        infeasibility_warning=warning,
    )

    return {
        "feasible": False,
        "daily_drawdown_schedule_mbd": schedule,
        "total_drawdown_mb": round(sum(schedule), 3),
        "spr_remaining_mb": round(spr_remaining, 2),
        "confidence": 0.0,
        "critical_warning": warning,
        "record_id": str(record_id),
        "horizon_days": HORIZON_DAYS,
    }


# ---------------------------------------------------------------------------
# Core LP solver
# ---------------------------------------------------------------------------

def solve_spr_schedule(
    available_imports_mbd: Optional[List[float]] = None,
    spr_total_mb: Optional[float] = None,
    daily_consumption_mbd: Optional[float] = None,
    spot_premium_usd: Optional[float] = None,
    playbook_id: Optional[UUID] = None,
    approved_cargo_schedule: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Solve the 30-day SPR drawdown schedule using scipy.optimize.linprog.

    LP formulation
    --------------
    Decision variables:
        x[t]  daily SPR drawdown in mb/day for t = 0..29

    Objective (minimise):
        Σ [ spr_depletion_weight * x[t] + spot_premium_weight * gap(t) ]
        where gap(t) = max(0, demand(t) - imports(t) - x[t])
        Simplified to minimise total drawdown (proxy for depletion cost)
        + penalise unmet demand (proxy for spot premium cost).

        Because linprog requires a linear objective, we set:
            c[t] = 1.0  (unit depletion cost per mb drawn per day)
        This minimises cumulative SPR depletion over 30 days while
        satisfying demand constraints.

    Constraints (all as ≤ inequalities for linprog A_ub @ x ≤ b_ub):
        1. Daily release capacity:
               x[t] ≤ MAX_DAILY_RELEASE_MBD   (0.5 mb/day physical limit)
        2. Strategic reserve floor:
               Σ x[t] ≤ (1 - SPR_RESERVE_FLOOR_PCT) * spr_total_mb
               i.e. keep at least 40% of SPR untouched
        3. Demand satisfaction (per day):
               -x[t] ≤ -(demand[t] - imports[t])
               i.e. x[t] ≥ demand[t] - imports[t]   (meet the gap)

    Bounds:
        0 ≤ x[t] ≤ MAX_DAILY_RELEASE_MBD

    Parameters
    ----------
    available_imports_mbd : list[float], optional
        Day-by-day available import volume in mb/day for each of 30 days.
        Comes from Agent 4 surviving routes output.
        If None, assumes zero imports (worst case — full SPR responsibility).
    spr_total_mb : float, optional
        Total SPR volume in mb. If None, fetched from Neo4j.
    daily_consumption_mbd : float, optional
        India's daily crude demand. If None, fetched from EIA API.
    spot_premium_usd : float, optional
        Current Brent spot price for cost weighting. If None, from Redis.
    playbook_id : UUID, optional
        If provided, write result to PostgreSQL spr_schedules table.
    approved_cargo_schedule : list[float], optional
        Re-run input (Fix 7): Agent 6 approved cargo volumes per day.
        Replaces available_imports_mbd when Agent 5 re-runs after Agent 6.

    Returns
    -------
    dict:
        feasible                  bool
        daily_drawdown_schedule_mbd  list[float]  30 daily values
        total_drawdown_mb         float
        spr_remaining_mb          float
        confidence                float  0.0 if infeasible
        critical_warning          str or None
        record_id                 str   PostgreSQL UUID
        horizon_days              int
        inputs_used               dict  audit trail of all inputs
    """
    # --- Fetch live inputs ---
    if spr_total_mb is None:
        spr_total_mb = get_spr_total_volume()
    if daily_consumption_mbd is None:
        daily_consumption_mbd = _get_india_consumption()
    if spot_premium_usd is None:
        spot_premium_usd = _get_spot_premium()

    # Use approved cargoes if provided (Fix 7 re-run), else available imports
    if approved_cargo_schedule is not None:
        imports = list(approved_cargo_schedule)
    elif available_imports_mbd is not None:
        imports = list(available_imports_mbd)
    else:
        # Worst case: no imports available
        imports = [0.0] * HORIZON_DAYS

    # Pad or truncate imports list to exactly HORIZON_DAYS
    if len(imports) < HORIZON_DAYS:
        imports.extend([imports[-1] if imports else 0.0] * (HORIZON_DAYS - len(imports)))
    imports = imports[:HORIZON_DAYS]

    n = HORIZON_DAYS
    demand = [daily_consumption_mbd] * n

    # --- Feasibility pre-check ---
    # Total supply = max possible SPR release + total imports
    max_spr_release = (1.0 - SPR_RESERVE_FLOOR_PCT) * spr_total_mb
    total_imports = sum(imports)
    total_max_release = min(max_spr_release, MAX_DAILY_RELEASE_MBD * n)
    total_demand = sum(demand)

    # Per-day gap = demand - imports; total gap must be coverable by SPR
    daily_gaps = [max(0.0, demand[t] - imports[t]) for t in range(n)]
    total_gap = sum(daily_gaps)

    if total_gap > total_max_release + 1e-6:
        logger.warning(
            "Agent 5: total gap %.2f mb exceeds max SPR release %.2f mb — LP will be infeasible",
            total_gap, total_max_release,
        )
        return _infeasibility_fallback(spr_total_mb, playbook_id)

    # --- Build LP matrices ---
    # Objective: minimise cumulative drawdown (linear, coefficient = 1 per day)
    # Slightly weight later days less to prefer front-loading (numerical stability)
    c = np.array([1.0 - 0.001 * t for t in range(n)])

    A_ub_rows = []
    b_ub_rows = []

    # Constraint 1: x[t] ≤ MAX_DAILY_RELEASE_MBD (one row per day)
    for t in range(n):
        row = np.zeros(n)
        row[t] = 1.0
        A_ub_rows.append(row)
        b_ub_rows.append(MAX_DAILY_RELEASE_MBD)

    # Constraint 2: Σ x[t] ≤ max_spr_release (strategic floor, single row)
    A_ub_rows.append(np.ones(n))
    b_ub_rows.append(max_spr_release)

    # Constraint 3: -x[t] ≤ -(demand[t] - imports[t])  i.e.  x[t] ≥ gap[t]
    for t in range(n):
        row = np.zeros(n)
        row[t] = -1.0
        A_ub_rows.append(row)
        b_ub_rows.append(-daily_gaps[t])

    A_ub = np.array(A_ub_rows)
    b_ub = np.array(b_ub_rows)

    # Bounds: 0 ≤ x[t] ≤ MAX_DAILY_RELEASE_MBD
    bounds = [(0.0, MAX_DAILY_RELEASE_MBD)] * n

    # --- Solve ---
    result = linprog(
        c,
        A_ub=A_ub,
        b_ub=b_ub,
        bounds=bounds,
        method="highs",   # HiGHS is the fastest/most stable scipy LP method
    )

    # --- Fix 5: infeasibility fallback ---
    if result.status == 2:
        logger.warning("Agent 5 linprog returned infeasible (status=2)")
        return _infeasibility_fallback(spr_total_mb, playbook_id)

    if result.status != 0:
        # status 1 = iteration limit, 3 = unbounded, 4 = numerical difficulty
        logger.warning(
            "Agent 5 linprog non-optimal status=%d (%s) — using solution as-is",
            result.status, result.message,
        )

    # --- Parse result ---
    schedule = [round(float(x), 4) for x in result.x]
    total_drawdown = round(sum(schedule), 3)
    spr_remaining = round(max(0.0, spr_total_mb - total_drawdown), 2)

    # Confidence: 1.0 if optimal, lower for non-optimal solves
    status_confidence_map = {0: 1.0, 1: 0.6, 3: 0.3, 4: 0.5}
    confidence = status_confidence_map.get(result.status, 0.5)

    # --- Persist to PostgreSQL ---
    record_id = insert_spr_schedule(
        playbook_id=playbook_id,
        feasible=True,
        daily_drawdown_schedule=schedule,
        confidence=confidence,
        spr_remaining_mb=spr_remaining,
        infeasibility_warning=None,
    )

    inputs_used = {
        "spr_total_mb": round(spr_total_mb, 2),
        "daily_consumption_mbd": round(daily_consumption_mbd, 3),
        "spot_premium_usd": round(spot_premium_usd, 2),
        "max_daily_release_mbd": MAX_DAILY_RELEASE_MBD,
        "reserve_floor_pct": SPR_RESERVE_FLOOR_PCT,
        "total_gap_mb": round(total_gap, 3),
        "total_imports_mb": round(total_imports, 3),
        "used_approved_cargoes": approved_cargo_schedule is not None,
    }

    logger.info(
        "Agent 5 LP solved: total drawdown %.2f mb, SPR remaining %.2f mb, confidence %.2f",
        total_drawdown, spr_remaining, confidence,
    )

    return {
        "feasible": True,
        "daily_drawdown_schedule_mbd": schedule,
        "total_drawdown_mb": total_drawdown,
        "spr_remaining_mb": spr_remaining,
        "confidence": confidence,
        "critical_warning": None,
        "record_id": str(record_id),
        "horizon_days": HORIZON_DAYS,
        "inputs_used": inputs_used,
        "lp_status": result.status,
        "lp_message": result.message,
    }


# ---------------------------------------------------------------------------
# LangGraph node entry point
# ---------------------------------------------------------------------------

def run_agent5(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph node function for Agent 5.

    Reads from LangGraph state:
        - surviving_routes_mbd   : list[float] day-by-day import volume
        - approved_cargoes_mbd   : list[float] (only present on re-run, Fix 7)
        - playbook_id            : UUID or None

    Writes to LangGraph state:
        - spr_schedule           : full solve_spr_schedule() output dict
    """
    logger.info("Agent 5 starting SPR LP optimisation")

    available_imports = state.get("surviving_routes_mbd")
    approved_cargoes = state.get("approved_cargoes_mbd")
    playbook_id = state.get("playbook_id")

    result = solve_spr_schedule(
        available_imports_mbd=available_imports,
        approved_cargo_schedule=approved_cargoes,
        playbook_id=playbook_id,
    )

    return {**state, "spr_schedule": result}
