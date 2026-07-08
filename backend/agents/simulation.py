from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import redis as redis_lib
import yfinance as yf
from dotenv import load_dotenv

from db.neo4j_queries import get_refinery_specs, get_spr_total_volume
from db.postgres_queries import get_latest_price_history

load_dotenv()
logger = logging.getLogger(__name__)

PRICES_LIVE_KEY = os.getenv("PRICES_LIVE_KEY", "prices:live")

_redis_client: Optional[redis_lib.Redis] = None

_EIA_INDIA_CONSUMPTION_MBD = 5.1
_FALLBACK_BRENT_USD = 85.0


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
    return _redis_client


def _get_brent_price() -> float:
    try:
        r = _get_redis()
        raw = r.get(PRICES_LIVE_KEY)
        if raw:
            data = json.loads(raw)
            brent_value = data.get("brent_usd")

            if brent_value is None:
                brent_node = data.get("brent")
                if isinstance(brent_node, dict):
                    brent_value = brent_node.get("price")
                else:
                    brent_value = brent_node

            price = float(brent_value or 0)
            if price > 0:
                return price
    except Exception as exc:
        logger.warning("Redis price read failed: %s", exc)

    try:
        ticker = yf.Ticker("BZ=F")
        hist = ticker.history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as exc:
        logger.warning("yfinance price read failed: %s", exc)

    try:
        row = get_latest_price_history()
        if row and row.get("brent_usd"):
            return float(row["brent_usd"])
    except Exception as exc:
        logger.warning("PostgreSQL price fallback failed: %s", exc)

    logger.error("All Brent price sources failed — using fallback %.2f", _FALLBACK_BRENT_USD)
    return _FALLBACK_BRENT_USD


def _get_india_daily_consumption() -> float:
    try:
        import requests

        eia_key = os.getenv("EIA_API_KEY", "")
        if not eia_key:
            raise ValueError("EIA_API_KEY not set")

        url = (
            "https://api.eia.gov/v2/international/data/"
            "?api_key={key}"
            "&facets[activityId][]=1"
            "&facets[productId][]=53"
            "&facets[countryRegionId][]=IND"
            "&data[]=value"
            "&sort[0][column]=period"
            "&sort[0][direction]=desc"
            "&length=1"
        ).format(key=eia_key)

        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        value = resp.json()["response"]["data"][0]["value"]
        return float(value) / 365
    except Exception as exc:
        logger.warning(
            "EIA consumption fetch failed (%s) — using fallback %.2f",
            exc,
            _EIA_INDIA_CONSUMPTION_MBD,
        )
        return _EIA_INDIA_CONSUMPTION_MBD


def compute_compound_severity(chokepoint_severities: Dict[str, float]) -> float:
    """
    Compound risk formula for multiple simultaneous chokepoint closures.

    Each chokepoint's severity represents the fraction of its route
    capacity that is disrupted (e.g. 0.82 = 82% closed). The compound
    formula treats each chokepoint's "surviving fraction" (1 - severity)
    as independent, and the overall compound severity is:

        compound_severity = 1 - product(1 - severity_i for each chokepoint)

    Example: Hormuz at 0.82, Red Sea at 0.87
        surviving_a = 1 - 0.82 = 0.18
        surviving_b = 1 - 0.87 = 0.13
        compound_severity = 1 - (0.18 * 0.13) = 1 - 0.0234 = 0.9766
    """
    if not chokepoint_severities:
        return 0.0

    surviving_product = 1.0
    for severity in chokepoint_severities.values():
        surviving_product *= (1.0 - float(severity))

    compound_severity = 1.0 - surviving_product
    return round(compound_severity, 4)


def import_disruption(
    supplier_route_risks: List[Dict[str, Any]],
    chokepoint_severities: Dict[str, float],
) -> Dict[str, Any]:
    daily_consumption = _get_india_daily_consumption()
    spr_total_mb = get_spr_total_volume()

    affected_lookup = {cp.strip().lower() for cp in chokepoint_severities.keys()}
    compound_severity = compute_compound_severity(chokepoint_severities)

    disrupted_share = 0.0
    disrupted_suppliers: List[str] = []

    for entry in supplier_route_risks:
        chokepoint = str(entry.get("primary_chokepoint", "")).strip().lower()
        if chokepoint in affected_lookup:
            share = float(entry["import_share"])
            risk = float(entry["route_risk"])
            disrupted_share += share * risk * compound_severity
            disrupted_suppliers.append(entry["supplier"])

    disrupted_share = min(1.0, disrupted_share)
    import_gap_mbd = disrupted_share * daily_consumption
    days_to_depletion = spr_total_mb / import_gap_mbd if import_gap_mbd > 0.001 else float("inf")

    return {
        "disrupted_share": round(disrupted_share, 4),
        "disrupted_suppliers": disrupted_suppliers,
        "daily_consumption_mbd": round(daily_consumption, 3),
        "import_gap_mbd": round(import_gap_mbd, 3),
        "days_to_depletion": round(days_to_depletion, 2),
        "spr_total_mb": round(spr_total_mb, 2),
    }


def spr_drawdown(
    spr_volume_mb: Optional[float] = None,
    daily_consumption_mbd: Optional[float] = None,
    import_gap_mbd: float = 0.0,
) -> Dict[str, Any]:
    if spr_volume_mb is None:
        spr_volume_mb = get_spr_total_volume()
    if daily_consumption_mbd is None:
        daily_consumption_mbd = _get_india_daily_consumption()

    spr_cover_days = spr_volume_mb / daily_consumption_mbd
    days_to_depletion = spr_volume_mb / import_gap_mbd if import_gap_mbd > 0.001 else float("inf")

    return {
        "spr_volume_mb": round(spr_volume_mb, 2),
        "daily_consumption_mbd": round(daily_consumption_mbd, 3),
        "spr_cover_days": round(spr_cover_days, 2),
        "import_gap_mbd": round(import_gap_mbd, 3),
        "days_to_depletion": round(days_to_depletion, 2),
    }


def price_impact(
    disruption_severity: float,
    supply_gap_pct: float,
    brent_baseline_usd: Optional[float] = None,
    beta: float = 0.45,
) -> Dict[str, Any]:
    if brent_baseline_usd is None:
        brent_baseline_usd = _get_brent_price()

    price_delta_pct = beta * disruption_severity * supply_gap_pct
    price_delta_usd = brent_baseline_usd * (price_delta_pct / 100)

    confidence_margin = 0.30
    price_high_usd = brent_baseline_usd + price_delta_usd * (1 + confidence_margin)
    price_low_usd = brent_baseline_usd + price_delta_usd * (1 - confidence_margin)

    return {
        "brent_baseline_usd": round(brent_baseline_usd, 2),
        "price_delta_pct": round(price_delta_pct, 2),
        "price_delta_usd": round(price_delta_usd, 2),
        "new_price_usd": round(brent_baseline_usd + price_delta_usd, 2),
        "price_high_usd": round(price_high_usd, 2),
        "price_low_usd": round(price_low_usd, 2),
        "confidence_band": "±30%",
        "beta_used": beta,
    }


def refinery_utilization(
    refinery_name: str,
    import_gap_mbd: float,
    refinery_capacity_mbd: Optional[float] = None,
    compatible_share: Optional[float] = None,
) -> Dict[str, Any]:
    if refinery_capacity_mbd is None or compatible_share is None:
        specs = get_refinery_specs(refinery_name)
        if refinery_capacity_mbd is None:
            refinery_capacity_mbd = float(specs.get("capacity_mbd", 1.0))
        if compatible_share is None:
            compatible_share = float(specs.get("compatible_share", 1.0))

    if refinery_capacity_mbd <= 0:
        logger.error("Invalid refinery capacity %.3f for %s", refinery_capacity_mbd, refinery_name)
        refinery_capacity_mbd = 1.0

    util_delta_pct = -(import_gap_mbd / refinery_capacity_mbd) * compatible_share * 100
    baseline_utilization = 92.0
    new_utilization_pct = max(0.0, min(100.0, baseline_utilization + util_delta_pct))

    return {
        "refinery_name": refinery_name,
        "refinery_capacity_mbd": round(refinery_capacity_mbd, 3),
        "compatible_share": round(compatible_share, 4),
        "import_gap_mbd": round(import_gap_mbd, 3),
        "util_delta_pct": round(util_delta_pct, 2),
        "new_utilization_pct": round(new_utilization_pct, 2),
        "baseline_utilization_pct": baseline_utilization,
    }


def run_all(
    supplier_route_risks: List[Dict[str, Any]],
    closure_severity: Any,
    affected_chokepoint: Any,
    refinery_names: Optional[List[str]] = None,
    brent_baseline_usd: Optional[float] = None,
    beta: float = 0.45,
) -> Dict[str, Any]:
    """
    Supports both single-chokepoint and compound multi-chokepoint calls:

    Single (legacy):
        run_all(risks, closure_severity=1.0, affected_chokepoint="Strait of Hormuz")

    Compound:
        run_all(
            risks,
            closure_severity={"Strait of Hormuz": 0.82, "Bab-el-Mandeb": 0.87},
            affected_chokepoint=["Strait of Hormuz", "Bab-el-Mandeb"],
        )
        # OR pass closure_severity as a single float applied to all chokepoints
        # in the affected_chokepoint list.
    """
    import datetime

    if refinery_names is None:
        refinery_names = [
            "Jamnagar RIL",
            "Vadinar Nayara",
            "Kochi BPCL",
            "Paradip IOCL",
        ]

    # Normalize affected_chokepoint into a list
    if isinstance(affected_chokepoint, str):
        chokepoint_list = [affected_chokepoint]
    else:
        chokepoint_list = list(affected_chokepoint)

    # Normalize closure_severity into a per-chokepoint dict
    if isinstance(closure_severity, dict):
        chokepoint_severities = {cp: float(closure_severity.get(cp, 0.0)) for cp in chokepoint_list}
    elif isinstance(closure_severity, (list, tuple)):
        chokepoint_severities = {cp: float(sev) for cp, sev in zip(chokepoint_list, closure_severity)}
    else:
        chokepoint_severities = {cp: float(closure_severity) for cp in chokepoint_list}

    compound_severity = compute_compound_severity(chokepoint_severities)

    disruption_result = import_disruption(
        supplier_route_risks=supplier_route_risks,
        chokepoint_severities=chokepoint_severities,
    )

    import_gap_mbd = disruption_result["import_gap_mbd"]
    disrupted_share = disruption_result["disrupted_share"]

    spr_result = spr_drawdown(import_gap_mbd=import_gap_mbd)

    price_result = price_impact(
        disruption_severity=compound_severity,
        supply_gap_pct=disrupted_share * 100,
        brent_baseline_usd=brent_baseline_usd,
        beta=beta,
    )

    refinery_results = []
    for name in refinery_names:
        try:
            refinery_results.append(
                refinery_utilization(
                    refinery_name=name,
                    import_gap_mbd=import_gap_mbd,
                )
            )
        except Exception as exc:
            logger.warning("Refinery utilization failed for %s: %s", name, exc)
            refinery_results.append({"refinery_name": name, "error": str(exc)})

    return {
        "disruption": disruption_result,
        "spr": spr_result,
        "price": price_result,
        "refineries": refinery_results,
        "meta": {
            "affected_chokepoints": chokepoint_list,
            "chokepoint_severities": chokepoint_severities,
            "compound_severity": compound_severity,
            "beta": beta,
            "simulated_at": datetime.datetime.utcnow().isoformat() + "Z",
        },
    }