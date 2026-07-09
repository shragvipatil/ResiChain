from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import redis as redis_lib
import yfinance as yf
from dotenv import load_dotenv

from db.neo4j_queries import get_refinery_specs, get_spr_total_volume, get_refinery_disrupted_share
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

    logger.error("All Brent price sources failed \u2014 using fallback %.2f", _FALLBACK_BRENT_USD)
    return _FALLBACK_BRENT_USD


def _get_india_daily_consumption() -> float:
    """
    EIA International API's Consumption series (activityId=2,
    productId=54 'Refined petroleum products') is confirmed empty
    for India \u2014 all rows return value='--' (uninitialized), verified
    via direct API inspection on 2026-07-08. Additionally, EIA's
    International dataset is structurally annual/low-frequency
    projection data, not a live daily feed, so even a populated
    series would not satisfy a genuinely "live daily" requirement.
    Using the documented constant directly. See docs/api-verification.md.
    """
    logger.info(
        "EIA India consumption series unavailable (documented limitation) \u2014 using %.2f mbd",
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

    disrupted_share = 0.0
    disrupted_suppliers: List[str] = []

    for entry in supplier_route_risks:
        chokepoint = str(entry.get("primary_chokepoint", "")).strip().lower()
        if chokepoint in affected_lookup:
            share = float(entry["import_share"])
            risk = float(entry["route_risk"])
            # Fix (Person B, Day 12 compound-scenario verification): risk
            # here is already 1.0 for every supplier _build_supplier_route_risks
            # sends us — it means "this supplier has literally zero surviving
            # route," a deterministic fact, not a probability. Multiplying by
            # compound_severity (Agent 4's "at least one corridor fails"
            # probability) double-discounts a fact we already know happened,
            # and answers a different question than the one being asked here.
            # (Confirmed: this was pulling disrupted_share from 0.281 — which
            # matches the ~28.4% spec target almost exactly — down to 0.2744.)
            disrupted_share += share * risk
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
        "confidence_band": "\u00b130%",
        "beta_used": beta,
    }


def refinery_utilization(
    refinery_name: str,
    import_gap_mbd: float,
    refinery_disrupted_weight: float = 1.0,
    refinery_capacity_mbd: Optional[float] = None,
    compatible_share: Optional[float] = None,
) -> Dict[str, Any]:
    """
    refinery_disrupted_weight: the fraction (0.0-1.0) of the NATIONAL
    import_gap_mbd that this specific refinery actually absorbs, based
    on which disrupted suppliers' crude grades are COMPATIBLE_WITH it
    (derived from the Neo4j PRODUCES / COMPATIBLE_WITH graph in run_all()).
    Defaults to 1.0 for backward compatibility with direct/legacy callers
    that don't pass a weight (previous behavior: full national gap hits
    this one refinery).
    """
    if refinery_capacity_mbd is None or compatible_share is None:
        specs = get_refinery_specs(refinery_name)
        if refinery_capacity_mbd is None:
            refinery_capacity_mbd = float(specs.get("capacity_mbd", 1.0))
        if compatible_share is None:
            compatible_share = float(specs.get("compatible_share", 1.0))

    if refinery_capacity_mbd <= 0:
        logger.error("Invalid refinery capacity %.3f for %s", refinery_capacity_mbd, refinery_name)
        refinery_capacity_mbd = 1.0

    refinery_gap_mbd = import_gap_mbd * refinery_disrupted_weight

    util_delta_pct = -(refinery_gap_mbd / refinery_capacity_mbd) * compatible_share * 100
    baseline_utilization = 92.0
    new_utilization_pct = max(0.0, min(100.0, baseline_utilization + util_delta_pct))

    return {
        "refinery_name": refinery_name,
        "refinery_capacity_mbd": round(refinery_capacity_mbd, 3),
        "compatible_share": round(compatible_share, 4),
        "import_gap_mbd": round(import_gap_mbd, 3),
        "refinery_disrupted_weight": round(refinery_disrupted_weight, 4),
        "refinery_gap_mbd": round(refinery_gap_mbd, 3),
        "util_delta_pct": round(util_delta_pct, 2),
        "new_utilization_pct": round(new_utilization_pct, 2),
        "baseline_utilization_pct": baseline_utilization,
    }


def _compute_refinery_weights(
    refinery_names: List[str],
    disrupted_suppliers: List[str],
) -> Dict[str, float]:
    """
    Weight = this refinery's share of the NATIONAL disrupted-import gap it
    would actually absorb.

    Bug fixed here (Person B, Day 12 compound-scenario verification): the
    previous version normalized weights to sum to 1.0 across only the
    refineries modeled in this KG (4 of them, combined capacity ~2.26 mbd).
    But import_gap_mbd is a NATIONAL figure, computed against India's full
    daily consumption (~5.1 mbd) — these 4 refineries represent less than
    half of that. Forcing weights to sum to 1.0 made them absorb the ENTIRE
    national gap between just the four of them, overloading each one far
    past a realistic level (confirmed: Jamnagar's util_delta_pct came out
    to -57.2%, roughly 5-8x the ~-7% to -11% expected for this scenario).

    Fix: weights now sum to (modeled capacity / national daily consumption)
    instead of 1.0, so the modeled refineries absorb only the fraction of
    the national gap proportional to how much of the national refining
    market they actually represent, distributed among themselves by their
    own capacity share as before. This does not fully close the gap to the
    spec's -7%/-11% target on its own — see docs/fixes_applied.md for the
    remaining calibration question (how much of India's real refining
    capacity these 4 demo refineries are meant to represent).
    """
    if not disrupted_suppliers:
        return {name: 0.0 for name in refinery_names}

    try:
        raw = get_refinery_disrupted_share(disrupted_suppliers)
    except Exception as exc:
        logger.warning("get_refinery_disrupted_share failed (%s) — falling back to equal weighting", exc)
        equal_weight = 1.0 / len(refinery_names) if refinery_names else 0.0
        return {name: equal_weight for name in refinery_names}

    eligible = {name for name in refinery_names if raw.get(name, {}).get("compatible_grade_count", 0)}
    if not eligible:
        logger.warning("No refinery eligible for disrupted suppliers %s — falling back to equal weighting", disrupted_suppliers)
        eligible = set(refinery_names)

    capacities = {}
    for name in eligible:
        specs = get_refinery_specs(name)
        capacities[name] = float(specs.get("capacity_mbd", 0.0) or 0.0)

    total_capacity = sum(capacities.values())
    if total_capacity <= 0:
        equal_weight = 1.0 / len(eligible)
        return {name: (equal_weight if name in eligible else 0.0) for name in refinery_names}

    national_daily_consumption = _get_india_daily_consumption()
    modeled_share_of_national = (
        min(1.0, total_capacity / national_daily_consumption)
        if national_daily_consumption > 0 else 1.0
    )

    return {
        name: (
            (capacities.get(name, 0.0) / total_capacity) * modeled_share_of_national
            if name in eligible else 0.0
        )
        for name in refinery_names
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

    Refinery utilization fix: instead of applying the full NATIONAL
    import_gap_mbd to every individual refinery's own capacity (which
    mathematically guarantees overshoot on any refinery smaller than the
    national gap), each refinery now absorbs only its proportional share
    of the gap based on which disrupted suppliers' crude grades it can
    actually process (PRODUCES -> COMPATIBLE_WITH graph traversal).
    """
    import datetime

    if refinery_names is None:
        refinery_names = [
            "Jamnagar RIL",
            "Vadinar Nayara",
            "Kochi BPCL",
            "Paradip IOCL",
        ]

    if isinstance(affected_chokepoint, str):
        chokepoint_list = [affected_chokepoint]
    else:
        chokepoint_list = list(affected_chokepoint)

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
    disrupted_suppliers = disruption_result["disrupted_suppliers"]

    spr_result = spr_drawdown(import_gap_mbd=import_gap_mbd)

    price_result = price_impact(
        disruption_severity=compound_severity,
        supply_gap_pct=disrupted_share * 100,
        brent_baseline_usd=brent_baseline_usd,
        beta=beta,
    )

    refinery_weights = _compute_refinery_weights(refinery_names, disrupted_suppliers)

    refinery_results = []
    for name in refinery_names:
        try:
            refinery_results.append(
                refinery_utilization(
                    refinery_name=name,
                    import_gap_mbd=import_gap_mbd,
                    refinery_disrupted_weight=refinery_weights.get(name, 0.0),
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
            "refinery_weights": {k: round(v, 4) for k, v in refinery_weights.items()},
            "simulated_at": datetime.datetime.utcnow().isoformat() + "Z",
        },
    }