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
from db.postgres_queries import get_latest_price_history, insert_spr_schedule

load_dotenv()
logger = logging.getLogger(__name__)

HORIZON_DAYS: int = 30
MAX_DAILY_RELEASE_MBD: float = float(os.getenv("SPRMAXDAILYRELEASEMBD", os.getenv("SPR_MAX_DAILY_RELEASE_MBD", "0.5")))
SPR_RESERVE_FLOOR_PCT: float = float(os.getenv("SPRRESERVEFLOORPCT", os.getenv("SPR_RESERVE_FLOOR_PCT", "0.40")))
EMERGENCY_DAILY_CONSUMPTION_MBD: float = float(os.getenv("INDIA_DAILY_CONSUMPTION_MBD", "5.1"))

_redis_client: Optional[redis_lib.Redis] = None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(
            os.getenv("REDISURL", os.getenv("REDIS_URL", "redis://redis:6379/0")),
            decode_responses=True,
        )
    return _redis_client


def _extract_brent_price(raw: str) -> Optional[float]:
    try:
        data = json.loads(raw)
    except Exception:
        return None

    candidates = [
        data.get("brent_usd"),
        data.get("brent"),
        data.get("Brent"),
        data.get("price") if isinstance(data.get("brent"), dict) else None,
    ]
    brent_obj = data.get("brent")
    if isinstance(brent_obj, dict):
        candidates.append(brent_obj.get("price"))

    for value in candidates:
        try:
            price = float(value)
            if price > 0:
                return price
        except Exception:
            continue
    return None


def _get_spot_premium() -> float:
    """
    Price fallback chain per project intent:
    1. Redis priceslive
    2. Redis prices:live (legacy-tolerant)
    3. PostgreSQL latest pricehistory
    4. Env-configurable emergency fallback
    """
    try:
        r = _get_redis()
        for key in ("priceslive", "prices:live"):
            raw = r.get(key)
            if not raw:
                continue
            price = _extract_brent_price(raw)
            if price is not None:
                return price
    except Exception as exc:
        logger.warning("Redis Brent price read failed in Agent 5: %s", exc)

    try:
        row = get_latest_price_history()
        if row and row.get("brent_usd") is not None:
            price = float(row["brent_usd"])
            if price > 0:
                return price
    except Exception as exc:
        logger.warning("PostgreSQL Brent fallback failed in Agent 5: %s", exc)

    emergency = float(os.getenv("EMERGENCY_BRENT_FALLBACK_USD", "85.0"))
    logger.warning("Agent 5 using emergency Brent fallback: %.2f", emergency)
    return emergency


def _get_india_consumption() -> float:
    """
    Prefer live EIA; tolerate both EIAAPIKEY and EIA_API_KEY env names.
    """
    try:
        import requests

        key = os.getenv("EIAAPIKEY", os.getenv("EIA_API_KEY", ""))
        if not key:
            raise ValueError("EIA API key not set")

        url = (
            "https://api.eia.gov/v2/international/data/"
            f"?api_key={key}"
            "&facets[activityId][]=1"
            "&facets[productId][]=53"
            "&facets[countryRegionId][]=IND"
            "&data[]=value"
            "&sort[0][column]=period"
            "&sort[0][direction]=desc"
            "&length=1"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        rows = resp.json().get("response", {}).get("data", [])
        if not rows:
            raise ValueError("No EIA consumption rows returned")
        annual_value = float(rows[0]["value"])
        if annual_value <= 0:
            raise ValueError("EIA returned non-positive consumption")
        return annual_value / 365.0
    except Exception as exc:
        logger.warning(
            "EIA consumption fetch failed in Agent 5: %s — using emergency fallback %.2f",
            exc,
            EMERGENCY_DAILY_CONSUMPTION_MBD,
        )
        return EMERGENCY_DAILY_CONSUMPTION_MBD


def _persist_schedule(
    *,
    playbook_id: Optional[UUID],
    feasible: bool,
    schedule: List[float],
    confidence: float,
    spr_remaining_mb: float,
    infeasibility_warning: Optional[str],
    inputs_used: Dict[str, Any],
) -> Any:
    try:
        return insert_spr_schedule(
            playbook_id=playbook_id,
            feasible=feasible,
            daily_drawdown_schedule=schedule,
            confidence=confidence,
            spr_remaining_mb=spr_remaining_mb,
            infeasibility_warning=infeasibility_warning,
            inputs_used=inputs_used,
        )
    except TypeError:
        return insert_spr_schedule(
            playbook_id=playbook_id,
            feasible=feasible,
            daily_drawdown_schedule=schedule,
            confidence=confidence,
            spr_remaining_mb=spr_remaining_mb,
            infeasibility_warning=infeasibility_warning,
        )


def _infeasibility_fallback(
    spr_total_mb: float,
    playbook_id: Optional[UUID],
    inputs_used: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    schedule = [round(MAX_DAILY_RELEASE_MBD, 4)] * HORIZON_DAYS
    total_drawdown = round(sum(schedule), 4)
    spr_remaining = round(max(0.0, spr_total_mb - total_drawdown), 2)
    warning = (
        "Supply gap exceeds combined SPR and import capacity. "
        "Emergency rationing required."
    )

    audit_inputs = dict(inputs_used or {})
    audit_inputs["fallback_triggered"] = True

    record_id = _persist_schedule(
        playbook_id=playbook_id,
        feasible=False,
        schedule=schedule,
        confidence=0.0,
        spr_remaining_mb=spr_remaining,
        infeasibility_warning=warning,
        inputs_used=audit_inputs,
    )

    return {
        "feasible": False,
        "daily_drawdown_schedule_mbd": schedule,
        "total_drawdown_mb": total_drawdown,
        "spr_remaining_mb": spr_remaining,
        "confidence": 0.0,
        "critical_warning": warning,
        "record_id": str(record_id) if record_id is not None else None,
        "horizon_days": HORIZON_DAYS,
        "inputs_used": audit_inputs,
    }


def solve_spr_schedule(
    available_imports_mbd: Optional[List[float]] = None,
    spr_total_mb: Optional[float] = None,
    daily_consumption_mbd: Optional[float] = None,
    spot_premium_usd: Optional[float] = None,
    playbook_id: Optional[UUID] = None,
    approved_cargo_schedule: Optional[List[float]] = None,
) -> Dict[str, Any]:
    if spr_total_mb is None:
        spr_total_mb = float(get_spr_total_volume())
    if daily_consumption_mbd is None:
        daily_consumption_mbd = float(_get_india_consumption())
    if spot_premium_usd is None:
        spot_premium_usd = float(_get_spot_premium())

    if approved_cargo_schedule is not None:
        imports = list(approved_cargo_schedule)
    elif available_imports_mbd is not None:
        imports = list(available_imports_mbd)
    else:
        imports = [0.0] * HORIZON_DAYS

    if len(imports) < HORIZON_DAYS:
        fill = imports[-1] if imports else 0.0
        imports.extend([fill] * (HORIZON_DAYS - len(imports)))
    imports = [float(x) for x in imports[:HORIZON_DAYS]]

    n = HORIZON_DAYS
    demand = [float(daily_consumption_mbd)] * n
    max_spr_release = (1.0 - SPR_RESERVE_FLOOR_PCT) * float(spr_total_mb)
    total_imports = sum(imports)
    total_demand = sum(demand)
    total_max_release = min(max_spr_release, MAX_DAILY_RELEASE_MBD * n)
    daily_gaps = [max(0.0, demand[t] - imports[t]) for t in range(n)]
    total_gap = sum(daily_gaps)

    inputs_used = {
        "spr_total_mb": round(float(spr_total_mb), 4),
        "daily_consumption_mbd": round(float(daily_consumption_mbd), 4),
        "spot_premium_usd": round(float(spot_premium_usd), 4),
        "max_daily_release_mbd": float(MAX_DAILY_RELEASE_MBD),
        "reserve_floor_pct": float(SPR_RESERVE_FLOOR_PCT),
        "total_imports_mb": round(float(total_imports), 4),
        "total_demand_mb": round(float(total_demand), 4),
        "total_gap_mb": round(float(total_gap), 4),
        "used_approved_cargoes": approved_cargo_schedule is not None,
        "imports_mbd": [round(x, 4) for x in imports],
    }

    if total_gap > total_max_release + 1e-9:
        logger.warning(
            "Agent 5 infeasible pre-check: total gap %.3f > max releasable %.3f",
            total_gap,
            total_max_release,
        )
        return _infeasibility_fallback(
            spr_total_mb=float(spr_total_mb),
            playbook_id=playbook_id,
            inputs_used=inputs_used,
        )

    premium_weight = max(0.0, float(spot_premium_usd) / 100.0)
    time_weight = np.array([1.0 + (t * 0.002) for t in range(n)], dtype=float)
    c = (1.0 + premium_weight) * time_weight

    A_ub_rows = []
    b_ub_rows = []

    for t in range(n):
        row = np.zeros(n)
        row[t] = 1.0
        A_ub_rows.append(row)
        b_ub_rows.append(MAX_DAILY_RELEASE_MBD)

    A_ub_rows.append(np.ones(n))
    b_ub_rows.append(max_spr_release)

    for t in range(n):
        row = np.zeros(n)
        row[t] = -1.0
        A_ub_rows.append(row)
        b_ub_rows.append(-daily_gaps[t])

    A_ub = np.array(A_ub_rows, dtype=float)
    b_ub = np.array(b_ub_rows, dtype=float)
    bounds = [(0.0, MAX_DAILY_RELEASE_MBD)] * n

    result = linprog(
        c=c,
        A_ub=A_ub,
        b_ub=b_ub,
        bounds=bounds,
        method="highs",
    )

    if result.status == 2 or result.x is None:
        return _infeasibility_fallback(
            spr_total_mb=float(spr_total_mb),
            playbook_id=playbook_id,
            inputs_used=inputs_used,
        )

    schedule = [round(float(x), 4) for x in result.x]
    total_drawdown = round(sum(schedule), 4)
    spr_remaining = round(max(0.0, float(spr_total_mb) - total_drawdown), 2)

    status_confidence_map = {0: 1.0, 1: 0.6, 3: 0.3, 4: 0.5}
    confidence = float(status_confidence_map.get(result.status, 0.5))

    record_id = _persist_schedule(
        playbook_id=playbook_id,
        feasible=True,
        schedule=schedule,
        confidence=confidence,
        spr_remaining_mb=spr_remaining,
        infeasibility_warning=None,
        inputs_used=inputs_used,
    )

    return {
        "feasible": True,
        "daily_drawdown_schedule_mbd": schedule,
        "total_drawdown_mb": total_drawdown,
        "spr_remaining_mb": spr_remaining,
        "confidence": confidence,
        "critical_warning": None,
        "record_id": str(record_id) if record_id is not None else None,
        "horizon_days": HORIZON_DAYS,
        "inputs_used": inputs_used,
        "lp_status": int(result.status),
        "lp_message": result.message,
    }


def run_agent5(state: Dict[str, Any]) -> Dict[str, Any]:
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