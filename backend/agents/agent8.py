from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents.agent5 import run_agent5
from agents.agent6 import run_agent6, CHOKEPOINT_SHORT_TO_FULL
from agents.simulation import run_all as run_simulation
from db.postgres_queries import insert_playbook
from db.redis_client import get_redis


logger = logging.getLogger(__name__)

DEFAULT_DAILY_CONSUMPTION_MBD = 5.1
SPR_HORIZON_DAYS = 30

_FULL_TO_SHORT = {full: short for short, full in CHOKEPOINT_SHORT_TO_FULL.items()}


async def run_agent8(
    affected_chokepoints: List[str],
    closure_severity: Any = 1.0,
    signal_detected_at: Optional[datetime] = None,
    refinery_names: Optional[List[str]] = None,
    brent_baseline_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Agent 8 — Playbook Orchestrator.

    Root-cause-fixed version:
    - Injects the active scenario into risk:state NON-DESTRUCTIVELY
      (saves original value, restores it after Agent 6 runs) so the
      live risk-monitoring feed is never permanently overwritten.
    - Derives supplier_route_risks from Neo4j so simulation's
      import-gap math reflects the real disrupted suppliers, not an
      empty placeholder.
    - Relies on price_history being seeded so a cold Redis price cache
      doesn't cascade into the emergency Brent fallback.
    - Supports compound multi-chokepoint scenarios (e.g. Hormuz + Red
      Sea simultaneously) by threading affected_chokepoints as a list
      end-to-end. closure_severity accepts either a single float
      (applied uniformly to all chokepoints) or a Dict[str, float]
      (per-chokepoint severity, required for accurate compound math).
    """
    logger.info("Agent 8: starting playbook generation for %s", affected_chokepoints)

    if signal_detected_at is None:
        signal_detected_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------
    # Step 1 — Derive disrupted suppliers from Neo4j (root-cause fix
    # for simulation receiving an empty supplier_route_risks list)
    # ------------------------------------------------------------
    supplier_route_risks = _build_supplier_route_risks(affected_chokepoints, closure_severity)

    # Normalize closure_severity into a per-chokepoint dict before
    # passing to simulation.run_all() — this is the fix for the
    # TypeError caused by double-wrapping an already-dict severity.
    if isinstance(closure_severity, dict):
        normalized_severity = closure_severity
    else:
        normalized_severity = {cp: closure_severity for cp in affected_chokepoints}

    simulation_result = run_simulation(
        supplier_route_risks=supplier_route_risks,
        closure_severity=normalized_severity,
        affected_chokepoint=affected_chokepoints,
        refinery_names=refinery_names,
        brent_baseline_usd=brent_baseline_usd,
    )

    # ------------------------------------------------------------
    # Step 2 — Non-destructive risk:state injection, then Agent 6
    # ------------------------------------------------------------
    original_risk_state = await _snapshot_risk_state()
    await _inject_scenario_risk_state(affected_chokepoints, normalized_severity, original_risk_state)

    try:
        procurement_result = await run_agent6(playbook_id=None)
    except Exception as exc:
        logger.error("Agent 8: Agent 6 failed: %s", exc)
        procurement_result = _empty_procurement_result()
    finally:
        # Always restore the live feed's original values, even on failure.
        await _restore_risk_state(original_risk_state)

    approved_volume_mbd = sum(
        opt.get("adjusted_volume_mbd", 0.0)
        for opt in procurement_result.get("ranked_options", [])
    )
    approved_cargoes_mbd = [round(approved_volume_mbd, 4)] * SPR_HORIZON_DAYS

    # ------------------------------------------------------------
    # Step 3 — Agent 5 (SPR feasibility) — synchronous, no await
    # ------------------------------------------------------------
    try:
        spr_state = run_agent5({
            "approved_cargoes_mbd": approved_cargoes_mbd,
            "playbook_id": None,
        })
        spr_result = spr_state.get("spr_schedule", {})
    except Exception as exc:
        logger.error("Agent 8: Agent 5 failed: %s", exc)
        spr_result = _empty_spr_result()

    # ------------------------------------------------------------
    # Step 4 — Build views
    # ------------------------------------------------------------
    ministry_view = _build_ministry_view(
        affected_chokepoints=affected_chokepoints,
        closure_severity=closure_severity,
        simulation_result=simulation_result,
        spr_result=spr_result,
        procurement_result=procurement_result,
    )
    procurement_view = _build_procurement_view(procurement_result)
    refinery_view = _build_refinery_view(simulation_result)

    overall_confidence = _combine_confidence(spr_result.get("confidence", 0.0), procurement_result)
    playbook_generated_at = datetime.now(timezone.utc)
    signal_to_playbook_seconds = int((playbook_generated_at - signal_detected_at).total_seconds())
    status = _determine_status(spr_result, procurement_result)

    inputs = {
        "affected_chokepoints": affected_chokepoints,
        "closure_severity": closure_severity,
        "approved_volume_mbd": round(approved_volume_mbd, 4),
        "supplier_route_risks": supplier_route_risks,
        "simulation": simulation_result.get("meta", {}),
        "spr_inputs_used": spr_result.get("inputs_used", {}),
    }

    # ------------------------------------------------------------
    # Step 5 — Persist
    # ------------------------------------------------------------
    try:
        playbook_id = insert_playbook(
            signal_detected_at=signal_detected_at,
            playbook_generated_at=playbook_generated_at,
            signal_to_playbook_seconds=signal_to_playbook_seconds,
            status=status,
            ministry_view=ministry_view,
            procurement_view=procurement_view,
            refinery_view=refinery_view,
            confidence=overall_confidence,
            inputs=inputs,
        )
    except Exception as exc:
        logger.error("Agent 8: insert_playbook failed: %s", exc)
        playbook_id = None

    logger.info(
        "Agent 8: playbook %s generated in %ss (status=%s, confidence=%.2f)",
        playbook_id, signal_to_playbook_seconds, status, overall_confidence,
    )

    return {
        "playbook_id": str(playbook_id) if playbook_id else None,
        "status": status,
        "confidence": overall_confidence,
        "signal_to_playbook_seconds": signal_to_playbook_seconds,
        "ministry_view": ministry_view,
        "procurement_view": procurement_view,
        "refinery_view": refinery_view,
        "spr_result": spr_result,
        "simulation_result": simulation_result,
        "generated_at": playbook_generated_at.isoformat(),
    }


# ------------------------------------------------------------
# Scenario state injection (non-destructive)
# ------------------------------------------------------------


async def _snapshot_risk_state() -> Optional[Dict[str, Any]]:
    try:
        r = await get_redis()
        raw = await r.get("risk:state")
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.error("Agent 8: failed to snapshot risk:state: %s", exc)
        return None


async def _inject_scenario_risk_state(
    affected_chokepoints: List[str],
    closure_severity: Any,
    original_state: Optional[Dict[str, Any]],
) -> None:
    """
    Overlays the active scenario ON TOP of the live feed's snapshot so
    Agent 6 sees the correct severity for every affected chokepoint
    while other corridors keep their real ambient values. Restored
    immediately after Agent 6 runs — see _restore_risk_state.

    closure_severity may be a single float (applied uniformly) or a
    Dict[str, float] (per-chokepoint severity).
    """
    scenario_state = dict(original_state) if original_state else {}

    if isinstance(closure_severity, dict):
        severity_map = closure_severity
    else:
        severity_map = {cp: closure_severity for cp in affected_chokepoints}

    for cp in affected_chokepoints:
        short_name = _FULL_TO_SHORT.get(cp, cp)
        scenario_state[short_name] = float(severity_map.get(cp, 0.0))

    try:
        r = await get_redis()
        await r.set("risk:state", json.dumps(scenario_state))
        logger.info("Agent 8: injected scenario risk:state for %s", affected_chokepoints)
    except Exception as exc:
        logger.error("Agent 8: failed to inject risk:state: %s", exc)


async def _restore_risk_state(original_state: Optional[Dict[str, Any]]) -> None:
    try:
        r = await get_redis()
        if original_state is not None:
            await r.set("risk:state", json.dumps(original_state))
            logger.info("Agent 8: restored original risk:state")
        else:
            await r.delete("risk:state")
            logger.info("Agent 8: risk:state had no prior value — deleted override")
    except Exception as exc:
        logger.error("Agent 8: failed to restore risk:state: %s", exc)


# ------------------------------------------------------------
# Supplier-risk derivation (root-cause fix for simulation)
# ------------------------------------------------------------


def _build_supplier_route_risks(affected_chokepoints: List[str], closure_severity: Any) -> list:
    """
    Build the exact contract expected by simulation.import_disruption():
    [
        {
            "supplier": str,
            "primary_chokepoint": str,
            "import_share": float,
            "route_risk": float,
        },
        ...
    ]

    Supports compound multi-chokepoint scenarios: a supplier is
    disrupted if its MODELED route intersects ANY of the affected
    chokepoints. Suppliers with no route data at all in the KG are
    never treated as disrupted (root-cause fix for the false-positive
    USA/Venezuela inflation bug).
    """
    from db.neo4j_queries import get_surviving_routes, get_supplier_route_chokepoints, get_supplier_current_share

    try:
        surviving = get_surviving_routes(affected_chokepoints)
        surviving_suppliers = {r["supplier"] for r in surviving}
    except Exception as exc:
        logger.error("Agent 8: get_surviving_routes failed: %s", exc)
        return []

    try:
        route_map = get_supplier_route_chokepoints()
    except Exception as exc:
        logger.error("Agent 8: get_supplier_route_chokepoints failed: %s", exc)
        return []

    affected_set = {_FULL_TO_SHORT.get(cp, cp) for cp in affected_chokepoints}
    affected_set |= set(affected_chokepoints)

    disrupted_suppliers = {
        supplier
        for supplier, chokepoints in route_map.items()
        if set(chokepoints) & affected_set
        and supplier not in surviving_suppliers
    }

    supplier_route_risks = []
    for supplier in sorted(disrupted_suppliers):
        try:
            import_share = float(get_supplier_current_share(supplier))
        except Exception as exc:
            logger.warning(
                "Agent 8: get_supplier_current_share failed for %s: %s; using 0.0",
                supplier,
                exc,
            )
            import_share = 0.0

        hit_chokepoints = set(route_map[supplier]) & affected_set
        primary_chokepoint = next(iter(hit_chokepoints))

        supplier_route_risks.append({
            "supplier": supplier,
            "primary_chokepoint": primary_chokepoint,
            "import_share": import_share,
            "route_risk": 1.0,
        })

    return supplier_route_risks


# ------------------------------------------------------------
# View builders
# ------------------------------------------------------------


def _build_ministry_view(affected_chokepoints, closure_severity, simulation_result, spr_result, procurement_result):
    disruption = simulation_result.get("disruption", {})
    price = simulation_result.get("price", {})

    posture = "ACTIVATE_SPR_AND_PROCUREMENT"
    if procurement_result.get("approved_count", 0) == 0 and not spr_result.get("feasible", False):
        posture = "ESCALATE_EMERGENCY_RATIONING"
    elif procurement_result.get("approved_count", 0) > 0 and spr_result.get("feasible", False):
        posture = "MONITOR_AND_EXECUTE"

    chokepoint_display = ", ".join(affected_chokepoints)

    return {
        "headline": f"Contingency playbook activated for {chokepoint_display} disruption",
        "affected_chokepoints": affected_chokepoints,
        "closure_severity": closure_severity,
        "import_gap_mbd": disruption.get("import_gap_mbd"),
        "disrupted_share": disruption.get("disrupted_share"),
        "disrupted_suppliers": disruption.get("disrupted_suppliers", []),
        "spr_feasible": spr_result.get("feasible", False),
        "spr_remaining_mb": spr_result.get("spr_remaining_mb"),
        "estimated_new_brent_usd": price.get("new_price_usd"),
        "price_delta_pct": price.get("price_delta_pct"),
        "recommended_posture": posture,
        "critical_warning": spr_result.get("critical_warning"),
    }


def _build_procurement_view(procurement_result):
    ranked = procurement_result.get("ranked_options", [])
    blocked_trace = [r for r in procurement_result.get("full_rejection_trace", []) if r.get("status") == "BLOCKED"]

    return {
        "evaluated_count": procurement_result.get("evaluated_count", 0),
        "approved_count": procurement_result.get("approved_count", 0),
        "partial_count": procurement_result.get("partial_count", 0),
        "blocked_count": procurement_result.get("blocked_count", 0),
        "top_options": ranked[:5],
        "blocked_summary": [
            {"supplier": b.get("supplier"), "rule": (b.get("reason") or {}).get("rule")}
            for b in blocked_trace[:10]
        ],
        "blocked_chokepoints": procurement_result.get("blocked_chokepoints", []),
    }


def _build_refinery_view(simulation_result):
    refineries = simulation_result.get("refineries", [])
    valid = [r for r in refineries if "error" not in r]

    highest_risk = None
    max_loss = 0.0

    for r in valid:
        loss = abs(float(r.get("util_delta_pct", 0.0) or 0.0))
        if loss > max_loss:
            max_loss = loss
            highest_risk = r.get("refinery_name")

    return {
        "refineries": refineries,
        "max_utilization_loss_pct": round(max_loss, 2),
        "highest_risk_refinery": highest_risk,
    }


def _combine_confidence(spr_confidence, procurement_result):
    total = procurement_result.get("evaluated_count", 0)
    approved = procurement_result.get("approved_count", 0)
    procurement_confidence = (approved / total) if total > 0 else 0.0
    return round((spr_confidence + procurement_confidence) / 2, 4)


def _determine_status(spr_result, procurement_result):
    if not spr_result.get("feasible", False) and procurement_result.get("approved_count", 0) == 0:
        return "CRITICAL"
    if not spr_result.get("feasible", False) or procurement_result.get("blocked_count", 0) > procurement_result.get("approved_count", 0):
        return "DEGRADED"
    return "NOMINAL"


def _empty_procurement_result():
    return {
        "evaluated_count": 0, "approved_count": 0, "partial_count": 0, "blocked_count": 0,
        "ranked_options": [], "full_rejection_trace": [], "blocked_chokepoints": [],
        "duration_ms": 0, "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _empty_spr_result():
    return {
        "feasible": False, "daily_drawdown_schedule_mbd": [], "total_drawdown_mb": 0.0,
        "spr_remaining_mb": 0.0, "confidence": 0.0, "critical_warning": "SPR module failed to run",
        "record_id": None, "horizon_days": SPR_HORIZON_DAYS, "inputs_used": {},
    }