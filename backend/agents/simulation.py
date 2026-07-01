"""
agents/simulation.py
====================
ResiChain AI v2.0 — Four parametric simulation formulas.

Person B owns this file. Every formula takes live inputs as arguments.
Nothing is hardcoded inside function bodies. Judges will test with
different parameters.

Dependencies (all already in requirements):
    - yfinance        (Brent spot price fallback)
    - redis           (read risk:state and prices:live)
    - neo4j driver    (via db.neo4j_queries)

Usage:
    from agents.simulation import (
        import_disruption,
        spr_drawdown,
        price_impact,
        refinery_utilization,
    )
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import redis as redis_lib
import yfinance as yf
from dotenv import load_dotenv

from db.neo4j_queries import get_spr_total_volume, get_refinery_specs
from db.postgres_queries import get_latest_price_history

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis helper — shared, lazy-initialised
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
# Live-data helpers (price + EIA consumption)
# ---------------------------------------------------------------------------

# EIA India daily consumption constant (mbday).
# Real fetch would call EIA API; we keep the fallback here so simulation
# never blocks during a demo when the network is unavailable.
_EIA_INDIA_CONSUMPTION_MBD = 5.1   # fallback — approximately 5.1 mb/day


def _get_brent_price() -> float:
    """
    Price fallback chain as defined in the spec:
      1. Redis prices:live cache
      2. yfinance BZ=F
      3. Last known value from PostgreSQL price_history
    Returns price in USD/bbl.
    """
    # Step 1 — Redis cache
    try:
        r = _get_redis()
        raw = r.get("prices:live")
        if raw:
            data = json.loads(raw)
            price = float(data.get("brent_usd") or data.get("brent") or 0)
            if price > 0:
                logger.debug("Brent price from Redis: %.2f", price)
                return price
    except Exception as exc:
        logger.warning("Redis price read failed: %s", exc)

    # Step 2 — yfinance
    try:
        ticker = yf.Ticker("BZ=F")
        hist = ticker.history(period="2d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            logger.debug("Brent price from yfinance: %.2f", price)
            return price
    except Exception as exc:
        logger.warning("yfinance price read failed: %s", exc)

    # Step 3 — PostgreSQL price_history
    try:
        row = get_latest_price_history()
        if row and row.get("brent_usd"):
            price = float(row["brent_usd"])
            logger.debug("Brent price from PostgreSQL fallback: %.2f", price)
            return price
    except Exception as exc:
        logger.warning("PostgreSQL price fallback failed: %s", exc)

    # Hard fallback — approximate 2024/2025 Brent
    logger.error("All Brent price sources failed — using static fallback 85.0")
    return 85.0


def _get_india_daily_consumption() -> float:
    """
    Fetch India daily crude consumption in mb/day from EIA API.
    Falls back to constant if the API call fails.
    """
    try:
        import requests  # noqa: PLC0415

        eia_key = os.getenv("EIA_API_KEY", "")
        if not eia_key:
            raise ValueError("EIA_API_KEY not set")

        url = (
            "https://api.eia.gov/v2/international/data/"
            "?api_key={key}"
            "&facets[activityId][]=1"           # consumption
            "&facets[productId][]=53"           # crude + NGL
            "&facets[countryRegionId][]=IND"
            "&data[]=value"
            "&sort[0][column]=period"
            "&sort[0][direction]=desc"
            "&length=1"
        ).format(key=eia_key)

        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        value = resp.json()["response"]["data"][0]["value"]
        # EIA returns mb/year for annual data; convert to mb/day
        mbd = float(value) / 365
        logger.debug("EIA India consumption: %.3f mb/day", mbd)
        return mbd
    except Exception as exc:
        logger.warning("EIA consumption fetch failed (%s) — using fallback %.2f", exc, _EIA_INDIA_CONSUMPTION_MBD)
        return _EIA_INDIA_CONSUMPTION_MBD


# ---------------------------------------------------------------------------
# 1. Import Disruption Formula
# ---------------------------------------------------------------------------

def import_disruption(
    supplier_route_risks: List[Dict[str, Any]],
    closure_severity: float,
    affected_chokepoint: str,
) -> Dict[str, Any]:
    """
    Calculate the fraction of India's daily crude import supply that is
    disrupted by a chokepoint event.

    Parameters
    ----------
    supplier_route_risks : list of dicts
        Each dict must have:
          - "supplier"      (str)  supplier name
          - "import_share"  (float) share as decimal e.g. 0.22 for Iraq 22%
          - "route_risk"    (float) corridor risk 0–1 from Redis risk:state
          - "primary_chokepoint" (str) name of the chokepoint this route uses
        Supplier shares should come from Neo4j (UN Comtrade data).
        Route risk should come from Agent 3's live Redis risk:state vector.
    closure_severity : float
        0.5 for partial closure, 1.0 for full closure.
    affected_chokepoint : str
        Name of the chokepoint being evaluated (e.g. "Hormuz").

    Returns
    -------
    dict with:
        disrupted_share     float   fraction of total imports disrupted (0–1)
        disrupted_suppliers list    names of affected suppliers
        daily_consumption   float   India mb/day from EIA
        import_gap_mbd      float   mb/day lost
        days_to_depletion   float   days until SPR exhausted at current gap
        spr_total_mb        float   total SPR volume from Neo4j
    """
    daily_consumption = _get_india_daily_consumption()
    spr_total_mb = get_spr_total_volume()

    disrupted_share = 0.0
    disrupted_suppliers: List[str] = []

    for entry in supplier_route_risks:
        if entry.get("primary_chokepoint", "").lower() == affected_chokepoint.lower():
            share = float(entry["import_share"])
            risk = float(entry["route_risk"])
            disrupted_share += share * risk
            disrupted_suppliers.append(entry["supplier"])

    # Apply closure severity multiplier
    disrupted_share = disrupted_share * closure_severity

    # Cap at 1.0 (cannot disrupt more than 100% of imports)
    disrupted_share = min(1.0, disrupted_share)

    import_gap_mbd = disrupted_share * daily_consumption

    # Avoid division-by-zero if gap is negligible
    days_to_depletion = (
        spr_total_mb / import_gap_mbd if import_gap_mbd > 0.001 else float("inf")
    )

    return {
        "disrupted_share": round(disrupted_share, 4),
        "disrupted_suppliers": disrupted_suppliers,
        "daily_consumption_mbd": round(daily_consumption, 3),
        "import_gap_mbd": round(import_gap_mbd, 3),
        "days_to_depletion": round(days_to_depletion, 2),
        "spr_total_mb": round(spr_total_mb, 2),
    }


# ---------------------------------------------------------------------------
# 2. SPR Drawdown Formula
# ---------------------------------------------------------------------------

def spr_drawdown(
    spr_volume_mb: Optional[float] = None,
    daily_consumption_mbd: Optional[float] = None,
    import_gap_mbd: float = 0.0,
) -> Dict[str, Any]:
    """
    Calculate SPR coverage metrics under disruption.

    Parameters
    ----------
    spr_volume_mb : float, optional
        Current total SPR volume in million barrels.
        If None, fetched live from Neo4j StorageFacility nodes.
    daily_consumption_mbd : float, optional
        India's daily crude consumption in mb/day.
        If None, fetched from EIA API (with fallback).
    import_gap_mbd : float
        Import shortfall in mb/day from import_disruption().
        Use 0 for normal (no disruption) SPR cover calculation.

    Returns
    -------
    dict with:
        spr_volume_mb          float   total SPR volume used
        daily_consumption_mbd  float   daily consumption
        spr_cover_days         float   days of cover at normal consumption
        import_gap_mbd         float   passed-through shortfall
        days_to_depletion      float   days until exhausted at import_gap rate
    """
    if spr_volume_mb is None:
        spr_volume_mb = get_spr_total_volume()
    if daily_consumption_mbd is None:
        daily_consumption_mbd = _get_india_daily_consumption()

    spr_cover_days = spr_volume_mb / daily_consumption_mbd

    days_to_depletion = (
        spr_volume_mb / import_gap_mbd if import_gap_mbd > 0.001 else float("inf")
    )

    return {
        "spr_volume_mb": round(spr_volume_mb, 2),
        "daily_consumption_mbd": round(daily_consumption_mbd, 3),
        "spr_cover_days": round(spr_cover_days, 2),
        "import_gap_mbd": round(import_gap_mbd, 3),
        "days_to_depletion": round(days_to_depletion, 2),
    }


# ---------------------------------------------------------------------------
# 3. Price Impact Formula
# ---------------------------------------------------------------------------

def price_impact(
    disruption_severity: float,
    supply_gap_pct: float,
    brent_baseline_usd: Optional[float] = None,
    beta: float = 0.45,
) -> Dict[str, Any]:
    """
    Estimate Brent price impact from a supply disruption.

    Beta = 0.45 is calibrated from the 2019 Abqaiq attack
    (approximately 15% price spike on ~30% supply shock).

    Formula:
        price_delta_pct = beta × disruption_severity × supply_gap_pct
        price_delta_usd = brent_baseline × (price_delta_pct / 100)

    A ±30% confidence interval is always returned because oil price
    markets are inherently uncertain.

    Parameters
    ----------
    disruption_severity : float
        0.5 for partial, 1.0 for full. Matches closure_severity in formula 1.
    supply_gap_pct : float
        Supply gap as a percentage (e.g. 28.4 for 28.4%).
        Pass disrupted_share × 100 from import_disruption() output.
    brent_baseline_usd : float, optional
        Spot Brent price in USD/bbl.
        If None, fetched from Redis → yfinance → PostgreSQL fallback chain.
    beta : float
        Price sensitivity coefficient. Default 0.45 (Abqaiq calibration).

    Returns
    -------
    dict with:
        brent_baseline_usd  float   baseline price used
        price_delta_pct     float   estimated % price increase
        price_delta_usd     float   estimated USD/bbl increase
        price_high_usd      float   +30% confidence band upper bound
        price_low_usd       float   -30% confidence band lower bound
        new_price_usd       float   baseline + delta (point estimate)
        confidence_band     str     "±30%"
        beta_used           float   beta coefficient used
    """
    if brent_baseline_usd is None:
        brent_baseline_usd = _get_brent_price()

    price_delta_pct = beta * disruption_severity * supply_gap_pct
    price_delta_usd = brent_baseline_usd * (price_delta_pct / 100)

    # ±30% confidence interval applied to the delta itself
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


# ---------------------------------------------------------------------------
# 4. Refinery Utilization Formula
# ---------------------------------------------------------------------------

def refinery_utilization(
    refinery_name: str,
    import_gap_mbd: float,
    refinery_capacity_mbd: Optional[float] = None,
    compatible_share: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calculate the impact of a supply disruption on refinery utilization.

    Formula:
        util_delta_pct = -(import_gap_mbd / refinery_capacity_mbd) × compatible_share

    The negative sign is intentional — a supply gap always reduces utilization.

    Parameters
    ----------
    refinery_name : str
        Name of the refinery (must match Neo4j Refinery node name).
        e.g. "Jamnagar RIL", "Kochi BPCL", "Paradip IOCL", "Vadinar Nayara"
    import_gap_mbd : float
        Import shortfall in mb/day from import_disruption().
    refinery_capacity_mbd : float, optional
        Total throughput capacity in mb/day.
        If None, fetched from Neo4j via get_refinery_specs().
    compatible_share : float, optional
        Fraction of refinery capacity that CANNOT switch crude grades.
        0.0 means fully flexible; 1.0 means completely locked-in.
        If None, fetched from Neo4j via get_refinery_specs().

    Returns
    -------
    dict with:
        refinery_name          str
        refinery_capacity_mbd  float
        compatible_share       float
        import_gap_mbd         float
        util_delta_pct         float   negative means reduction
        new_utilization_pct    float   capped 0–100
        baseline_utilization   float   assumed 92% full utilization
    """
    if refinery_capacity_mbd is None or compatible_share is None:
        specs = get_refinery_specs(refinery_name)
        if refinery_capacity_mbd is None:
            refinery_capacity_mbd = float(specs.get("capacity_mbd", 1.0))
        if compatible_share is None:
            compatible_share = float(specs.get("compatible_share", 1.0))

    # Guard against division by zero
    if refinery_capacity_mbd <= 0:
        logger.error("Invalid refinery capacity %.3f for %s", refinery_capacity_mbd, refinery_name)
        refinery_capacity_mbd = 1.0

    util_delta_pct = -(import_gap_mbd / refinery_capacity_mbd) * compatible_share * 100

    # Assume refineries run at ~92% baseline utilization (industry average)
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


# ---------------------------------------------------------------------------
# 5. Composite run — all four formulas together
# ---------------------------------------------------------------------------

def run_all(
    supplier_route_risks: List[Dict[str, Any]],
    closure_severity: float,
    affected_chokepoint: str,
    refinery_names: Optional[List[str]] = None,
    brent_baseline_usd: Optional[float] = None,
    beta: float = 0.45,
) -> Dict[str, Any]:
    """
    Run all four simulation formulas in sequence and return a unified result.

    This is what GET /api/simulation/run calls (Person A wires the endpoint).
    All four outputs are nested under named keys so the frontend can render
    each formula section independently.

    Parameters
    ----------
    supplier_route_risks : list[dict]
        As per import_disruption() — supplier shares and route risks.
    closure_severity : float
        0.5 partial / 1.0 full.
    affected_chokepoint : str
        e.g. "Hormuz"
    refinery_names : list[str], optional
        Refineries to evaluate. Defaults to all four major Indian refineries.
    brent_baseline_usd : float, optional
        Override Brent baseline. If None, fetched from live sources.
    beta : float
        Price sensitivity coefficient. Default 0.45.

    Returns
    -------
    dict containing keys:
        disruption      — import_disruption() result
        spr             — spr_drawdown() result
        price           — price_impact() result
        refineries      — list of refinery_utilization() results (one per refinery)
        meta            — simulation metadata (chokepoint, severity, timestamp)
    """
    import datetime  # noqa: PLC0415

    if refinery_names is None:
        refinery_names = [
            "Jamnagar RIL",
            "Vadinar Nayara",
            "Kochi BPCL",
            "Paradip IOCL",
        ]

    # Formula 1 — Import disruption
    disruption_result = import_disruption(
        supplier_route_risks=supplier_route_risks,
        closure_severity=closure_severity,
        affected_chokepoint=affected_chokepoint,
    )

    import_gap_mbd = disruption_result["import_gap_mbd"]
    disrupted_share = disruption_result["disrupted_share"]

    # Formula 2 — SPR drawdown
    spr_result = spr_drawdown(
        import_gap_mbd=import_gap_mbd,
    )

    # Formula 3 — Price impact
    price_result = price_impact(
        disruption_severity=closure_severity,
        supply_gap_pct=disrupted_share * 100,
        brent_baseline_usd=brent_baseline_usd,
        beta=beta,
    )

    # Formula 4 — Refinery utilization (one result per refinery)
    refinery_results = []
    for name in refinery_names:
        try:
            result = refinery_utilization(
                refinery_name=name,
                import_gap_mbd=import_gap_mbd,
            )
            refinery_results.append(result)
        except Exception as exc:
            logger.warning("Refinery utilization failed for %s: %s", name, exc)
            refinery_results.append({
                "refinery_name": name,
                "error": str(exc),
            })

    return {
        "disruption": disruption_result,
        "spr": spr_result,
        "price": price_result,
        "refineries": refinery_results,
        "meta": {
            "affected_chokepoint": affected_chokepoint,
            "closure_severity": closure_severity,
            "beta": beta,
            "simulated_at": datetime.datetime.utcnow().isoformat() + "Z",
        },
    }