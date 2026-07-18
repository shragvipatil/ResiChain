# ============================================================
# ResiChain — Agent 6: Procurement Orchestrator
# Finds surviving supply routes, builds procurement candidates,
# scores them, and runs the full batch through Agent 7's constraint
# checks in one call. Produces a ranked list + full audit trail.
#
# UPDATED to match Person B's confirmed Agent 7 batch contract:
#   validate_batch(candidates: list, playbook_id) -> list of results
#   - Agent 6 builds the FULL candidate list first, then calls
#     validate_batch() ONCE — no more per-candidate loop calling the
#     validator. Fix 10's sequential diversification tracking, batch
#     confidence-sorting, and Postgres audit logging all live inside
#     the validator now (real Agent 7, or the fallback wrapper below).
#   - Agent 6 merges each validation result back onto its own candidate
#     data (by option_id) to build the final output, since Agent 7's
#     result doesn't carry Agent-6-only fields like route/arrival_port/
#     avg_transit_days.
#   - Agent 6 no longer calls insert_procurement_evaluation() itself —
#     see _fallback_validate_batch() for why that's still safe even
#     when Agent 7 isn't available.
#
# Chokepoint naming and route-name-parsing fixes (from earlier) unchanged.
# ============================================================

import os 
import asyncio
import json
import logging
from datetime import datetime
from db.redis_client import get_redis
from db.neo4j_queries import (
    get_surviving_routes,
    get_all_supplier_grades,
    get_contract_headroom,
    get_port_specs,
)


logger = logging.getLogger(__name__)


RISK_SURVIVAL_THRESHOLD = float(os.getenv("ROUTE_SURVIVAL_THRESHOLD", "0.40"))
# Day 18: reads from .env (ROUTE_SURVIVAL_THRESHOLD) instead of a hardcoded
# constant, and is deliberately DIFFERENT from Agent 4's CRISIS_THRESHOLD
# (0.65) — this is the procurement-caution threshold ("don't route new
# cargo here"), not the crisis/compound-detection threshold ("this
# corridor is in crisis"). Aligned with Person B, see fixes_applied.md. 


def _is_numeric_score(value) -> bool:
    """
    True for real int/float values, EXCLUDING bool.

    Bug (found by Person B): bool is a subclass of int in Python, so
    isinstance(True, (int, float)) is True. A boolean metadata marker
    key sitting alongside real corridor risk scores in risk:state would
    silently pass the old filter and get treated as a numeric risk
    value (observed: a scenario-override boolean leaked into
    blocked_chokepoints as if it were a real corridor). This helper
    excludes bool explicitly.
    """
    return type(value) in (int, float)


# Port -> Refinery mapping (matches Day 1 Neo4j seed)
PORT_TO_REFINERY = {
    "Vadinar": "Jamnagar RIL",
    "Sikka":   "Jamnagar RIL",
    "Paradip": "Paradip IOCL",
    "Kochi":   "Kochi BPCL",
}


# Supplier -> primary export/departure terminal.
# Reference lookup (demo-scope) — not pulled from a live API.
DEPARTURE_PORT_BY_SUPPLIER = {
    "Saudi Arabia": "Ras Tanura",
    "Iraq":         "Basra Oil Terminal",
    "UAE":          "Fujairah",
    "Russia":       "Novorossiysk",
    "USA":          "Corpus Christi",
    "Kuwait":       "Mina Al-Ahmadi",
    "Venezuela":    "Jose Terminal",
    "Iran":         "Kharg Island",
}


# Vessel class DWT thresholds — MUST match agents/agent7.py's
# VESSEL_CLASS_MAX_DWT exactly, since Agent 6 picks a class here and
# Agent 7 checks that same class's required DWT against the arrival
# port's capacity downstream. If these ever drift apart, Agent 6 could
# assign a class Agent 7 always rejects (or vice versa) — same failure
# shape as the earlier chokepoint-naming bug, just one layer up.
VESSEL_CLASS_MAX_DWT = {
    "VLCC": 320_000,
    "Suezmax": 160_000,
    "Aframax": 120_000,
}
DEFAULT_VESSEL_CLASS = "Suezmax"


# Short chokepoint name (Redis risk:state / route names) -> full
# Chokepoint.name as seeded in Neo4j. Confirmed against the actual live
# graph (Person B, seed_knowledge_graph.py): "Strait of Hormuz",
# "Suez Canal", "Cape of Good Hope", "Bab-el-Mandeb" for Red_Sea.
CHOKEPOINT_SHORT_TO_FULL = {
    "Hormuz": "Strait of Hormuz",
    "Suez": "Suez Canal",
    "Cape": "Cape of Good Hope",
    "Red_Sea": "Bab-el-Mandeb",
}


DEFAULT_DAILY_CONSUMPTION_MBD = 5.1



async def run_agent6(playbook_id=None) -> dict:
    """
    Main Agent 6 entry point.

    Flow:
    1. Read current corridor risk from Redis, determine blocked chokepoints
    2. Query Neo4j for surviving routes
    3. Build a procurement candidate per surviving supplier
    4. Score each candidate (route availability, grade compat, price, contract headroom)
    5. Validate the FULL batch in one call through Agent 7 (or fallback),
       then merge validation results back onto candidate data by option_id
    6. Return ranked list of APPROVED / PARTIAL options + full rejection trace

    NOTE: Route-node schema repair and per-candidate Postgres logging are
    NOT done here — Person B's seed script owns the former, and the
    validator (real or fallback) owns the latter.
    """
    logger.info("Agent 6: Starting procurement evaluation cycle...")
    start_time = datetime.utcnow()

    # Step 1 — determine blocked chokepoints from live risk state (short
    # names), then translate to full Neo4j Chokepoint.name values.
    blocked_chokepoints_short = await _get_blocked_chokepoints()
    blocked_chokepoints = [
        CHOKEPOINT_SHORT_TO_FULL.get(name, name) for name in blocked_chokepoints_short
    ]
    logger.info(
        f"Agent 6: Blocked chokepoints (short) = {blocked_chokepoints_short} "
        f"-> (full) = {blocked_chokepoints}"
    )

    # Step 2 — surviving routes from Neo4j
    try:
        surviving_routes = get_surviving_routes(blocked_chokepoints)
    except Exception as e:
        logger.error(f"Agent 6: get_surviving_routes failed: {e}")
        surviving_routes = []

    if not surviving_routes:
        logger.warning(
            "Agent 6: No surviving routes returned. Either every corridor is "
            "blocked, or the Knowledge Graph has no Route data yet."
        )

    # Step 3 — build candidates (one per unique supplier)
    candidates = await _build_candidates(surviving_routes, blocked_chokepoints_short)
    logger.info(f"Agent 6: Built {len(candidates)} procurement candidates")

    # Step 4 — validate the FULL batch in one call (Fix 10 sequential
    # diversification lives entirely inside the validator now — Agent 6
    # never loops calling it per-candidate).
    batch_validator = await _get_batch_validator()
    validation_results = await batch_validator(candidates, playbook_id) if candidates else []

    # Step 5 — merge each validation result back onto its original
    # candidate (indexed by option_id), since Agent 7's result doesn't
    # carry Agent-6-only fields (route, arrival_port, avg_transit_days).
    validation_by_option_id = {r["option_id"]: r for r in validation_results}

    results = []
    for candidate in candidates:
        option_id = candidate["option_id"]
        validation = validation_by_option_id.get(option_id)

        if validation is None:
            # Should never happen if the validator returns exactly one
            # result per input candidate — logged loudly since it means
            # a candidate silently vanished inside the validator.
            logger.error(
                f"Agent 6: No validation result returned for {option_id} "
                f"— treating as BLOCKED"
            )
            validation = {
                "status": "BLOCKED",
                "reason": {
                    "rule": "VALIDATOR_RESULT_MISSING",
                    "value": option_id,
                    "threshold": None,
                    "source": "agent6",
                },
                "adjusted_volume_mbd": 0.0,
            }

        evaluation_record = {
            "option_id": option_id,
            "supplier": candidate["supplier"],
            "grade": candidate.get("grade", ""),
            "refinery": candidate.get("refinery", ""),
            "route": candidate.get("route", ""),
            "arrival_port": candidate.get("arrival_port", ""),
            "requested_volume_mbd": candidate.get("proposed_volume_mbd", 0.0),
            "confidence": candidate.get("confidence", 0.0),
            "status": validation["status"],
            "reason": validation.get("reason"),
            "adjusted_volume_mbd": validation.get("adjusted_volume_mbd", 0.0),
            "price_premium_pct": candidate.get("price_premium_pct", 0.0),
            "avg_transit_days": candidate.get("avg_transit_days", 0),
        }
        results.append(evaluation_record)

        logger.info(
            f"Agent 6: {option_id} ({candidate['supplier']}) -> "
            f"{validation['status']}"
            + (f" [{validation['reason']['rule']}]" if validation.get("reason") else "")
        )

    # NOTE: no insert_procurement_evaluation() call here — logging now
    # happens inside whichever validator ran (real Agent 7's _result(),
    # or _fallback_validate_batch below). Agent 6 owns candidate
    # generation + ranking; the validator owns validation state + audit
    # persistence — per the agreed ownership boundary with Person B.

    # Step 6 — rank approved/partial options by confidence
    approved_and_partial = [
        r for r in results if r["status"] in ("APPROVED", "PARTIAL")
    ]
    approved_and_partial.sort(key=lambda r: r["confidence"], reverse=True)

    duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

    output = {
        "evaluated_count": len(results),
        "approved_count": len([r for r in results if r["status"] == "APPROVED"]),
        "partial_count": len([r for r in results if r["status"] == "PARTIAL"]),
        "blocked_count": len([r for r in results if r["status"] == "BLOCKED"]),
        "ranked_options": approved_and_partial,
        "full_rejection_trace": results,
        "blocked_chokepoints": blocked_chokepoints_short,
        "duration_ms": duration_ms,
        "generated_at": datetime.utcnow().isoformat(),
    }

    try:
        r = await get_redis()
        await r.setex("agent6:last_run", 600, json.dumps(output, default=str))
    except Exception as e:
        logger.error(f"Agent 6: Redis cache write failed: {e}")

    logger.info(
        f"Agent 6: Done in {duration_ms}ms — "
        f"{output['approved_count']} approved, "
        f"{output['partial_count']} partial, "
        f"{output['blocked_count']} blocked"
    )
    return output



async def _get_blocked_chokepoints() -> list:
    """Reads live risk:state from Redis, returns corridors (short names) at/above the survival threshold."""
    try:
        r = await get_redis()
        data = await r.get("risk:state")
        if not data:
            return []
        risk_data = json.loads(data)
        return [
            corridor for corridor, score in risk_data.items()
            if _is_numeric_score(score) and score >= RISK_SURVIVAL_THRESHOLD
        ]
    except Exception as e:
        logger.error(f"Agent 6: Failed to read risk state: {e}")
        return []



async def _build_candidates(surviving_routes: list, blocked_chokepoints_short: list) -> list:
    """
    Builds one procurement candidate per surviving supplier.
    Emits the shared candidate schema agreed with Person B:
    option_id, supplier, grade, route, refinery, arrival_port,
    proposed_volume_mbd, confidence, contract_reference,
    vessel_class, departure_port (+ extra scoring/display fields).
    """
    if not surviving_routes:
        return []

    risk_vector = {}
    try:
        r = await get_redis()
        data = await r.get("risk:state")
        if data:
            risk_vector = {
                k: v for k, v in json.loads(data).items()
                if _is_numeric_score(v)
            }
    except Exception:
        pass

    brent_price, price_source = await _get_live_brent_price()

    try:
        supplier_grades = get_all_supplier_grades()
    except Exception as e:
        logger.error(f"Agent 6: get_all_supplier_grades failed: {e}")
        supplier_grades = []

    grade_by_supplier = {g["supplier"]: g for g in supplier_grades}

    seen_suppliers = set()
    candidates = []
    idx = 0

    for route in surviving_routes:
        supplier = route.get("supplier")
        if not supplier or supplier in seen_suppliers:
            continue
        seen_suppliers.add(supplier)

        grade_info = grade_by_supplier.get(supplier, {})
        grade = grade_info.get("grade", "Unknown")
        api_gravity = grade_info.get("api_gravity") or 32.0
        sulfur_pct = grade_info.get("sulfur_pct") or 1.5

        arrival_port = route.get("arrival_port") or "Vadinar"
        refinery = PORT_TO_REFINERY.get(arrival_port, "Jamnagar RIL")
        departure_port = DEPARTURE_PORT_BY_SUPPLIER.get(supplier, "Unknown")

        route_name = route.get("route", "")
        primary_chokepoint = _extract_chokepoint_from_route_name(route_name)
        corridor_risk = risk_vector.get(primary_chokepoint, 0.3)
        vessel_class = _pick_vessel_class_for_port(arrival_port)

        route_avail = max(0.0, 1.0 - corridor_risk)

        try:
            grade_compat = 1.0 if _quick_grade_check(grade, refinery) else 0.0
        except Exception:
            grade_compat = 1.0

        premium_pct = _estimate_price_premium(api_gravity, sulfur_pct)
        price_delta_score = 1.0 / (1.0 + premium_pct / 100.0)

        try:
            contract = get_contract_headroom(supplier)
            max_vol = contract.get("max_volume_mbd", 0.0)
            headroom_mbd = contract.get("headroom_mbd", 0.0)
            contract_reference = contract.get("contract_reference", "")
            contract_headroom_score = (
                headroom_mbd / max_vol if max_vol > 0 else 1.0
            )
        except Exception:
            headroom_mbd = 0.5
            contract_reference = ""
            contract_headroom_score = 1.0

        confidence = (
            route_avail * grade_compat * price_delta_score * contract_headroom_score
        )

        requested_volume = min(0.20, headroom_mbd) if headroom_mbd > 0 else 0.15

        option_id = f"proc_{supplier.replace(' ', '_')}_{idx:03d}"
        idx += 1

        candidates.append({
            "option_id": option_id,
            "supplier": supplier,
            "grade": grade,
            "route": route_name,
            "refinery": refinery,
            "arrival_port": arrival_port,
            "departure_port": departure_port,
            "vessel_class": vessel_class,
            "proposed_volume_mbd": round(requested_volume, 4),
            "confidence": round(confidence, 4),
            "contract_reference": contract_reference,
            "primary_chokepoint": primary_chokepoint,
            "avg_transit_days": route.get("avg_transit_days", 0),
            "brent_baseline_usd": brent_price,
            "price_source": price_source,
            "price_premium_pct": round(premium_pct, 2),
            "route_avail_score": round(route_avail, 4),
            "grade_compat_score": grade_compat,
            "price_delta_score": round(price_delta_score, 4),
            "contract_headroom_score": round(contract_headroom_score, 4),
        })

    return candidates



def _extract_chokepoint_from_route_name(route_name: str) -> str:
    """Route names are seeded as 'Supplier to Port via Chokepoint' (space-separated)."""
    if " via " in route_name:
        return route_name.split(" via ")[-1].strip()
    return "Unknown"



def _pick_vessel_class_for_port(arrival_port: str) -> str:
    """
    Picks the LARGEST vessel class the arrival port can actually
    accommodate, using the port's real max_vessel_dwt from Neo4j.

    FIX (this revision): previously assigned vessel class purely by
    which chokepoint the route passed through (e.g. every Hormuz/Cape
    route got VLCC), regardless of the destination port's real
    capacity. That meant routes to smaller ports (Kochi 150k DWT,
    Paradip 180k, Vizag 200k) were auto-blocked on PORT_CAPACITY every
    time, even though a smaller, perfectly adequate vessel class could
    have carried the same cargo. Real shipping picks a vessel size the
    destination port can handle — this now does the same, and uses the
    exact same DWT thresholds Agent 7 checks against downstream, so a
    class picked here will always be one Agent 7 accepts on capacity
    grounds (other checks — tankers available, contract limits — still
    apply independently).
    """
    try:
        port_specs = get_port_specs(arrival_port)
        port_max_dwt = float(port_specs.get("max_vessel_dwt", 0.0) or 0.0)
    except Exception as e:
        logger.warning(f"Agent 6: get_port_specs failed for {arrival_port}: {e}")
        port_max_dwt = 0.0

    if port_max_dwt <= 0:
        return DEFAULT_VESSEL_CLASS

    # Pick the largest class that still fits within the port's capacity.
    for vessel_class, required_dwt in sorted(
        VESSEL_CLASS_MAX_DWT.items(), key=lambda kv: kv[1], reverse=True
    ):
        if port_max_dwt >= required_dwt:
            return vessel_class

    # Port is smaller than even the smallest known class — return the
    # smallest class anyway; Agent 7 will correctly BLOCK it on
    # PORT_CAPACITY, which is the accurate outcome for a genuinely
    # undersized port rather than something to paper over here.
    return min(VESSEL_CLASS_MAX_DWT, key=VESSEL_CLASS_MAX_DWT.get)



def _quick_grade_check(grade: str, refinery: str) -> bool:
    from db.neo4j_queries import check_grade_compatibility
    return check_grade_compatibility(grade, refinery)



def _estimate_price_premium(api_gravity: float, sulfur_pct: float) -> float:
    sulfur_penalty = max(0.0, (sulfur_pct - 1.0) * 3.0)
    gravity_bonus = min(2.0, max(0.0, (api_gravity - 30.0) / 10.0))
    premium_pct = max(0.0, sulfur_penalty - gravity_bonus)
    return premium_pct



async def _get_live_brent_price() -> tuple:
    try:
        r = await get_redis()
        data = await r.get("prices:live")
        if data:
            prices = json.loads(data)
            if "brent" in prices:
                return prices["brent"]["price"], "redis_cache"
    except Exception:
        pass
    return 82.0, "fallback_default"



async def _get_batch_validator():
    """
    Prefers Person B's real agents.agent7.validate_batch() if it exists.
    Falls back to _fallback_validate_batch() (below) if Agent 7 isn't
    importable for any reason.

    IMPORTANT: agents.agent7.validate_batch is a SYNC function (plain
    `def`, not `async def` — confirmed directly from the file, and from
    a real production TypeError: "object list can't be used in 'await'
    expression" when it was called with a bare `await`). It's wrapped in
    asyncio.to_thread here so the caller in run_agent6() can always
    `await` whatever this function returns, regardless of whether the
    real validator or the (async) fallback is running underneath.

    Both paths return the SAME shape: a list of dicts, one per input
    candidate, each containing option_id/supplier/grade/status/reason/
    confidence/adjusted_volume_mbd. Agent 6 merges these back onto its
    own candidate data by option_id afterward (see run_agent6).

    Postgres audit logging happens INSIDE whichever validator runs
    (real Agent 7's internal _result(), or _fallback_validate_batch
    below) — never inside Agent 6 itself. This preserves the agreed
    ownership boundary (Agent 6: candidate generation + ranking;
    validator: validation state + audit persistence) regardless of
    which validator actually executes.
    """
    try:
        from agents.agent7 import validate_batch as sync_validate_batch
        logger.info("Agent 6: Using Person B's agents.agent7.validate_batch")

        async def _real_validate_batch(candidates, playbook_id):
            return await asyncio.to_thread(sync_validate_batch, candidates, playbook_id)

        return _real_validate_batch
    except (ImportError, AttributeError):
        logger.warning(
            "Agent 6: agents.agent7.validate_batch not found — "
            "using fallback batch validator. Agent 7 is not blocking Agent 6."
        )
        return _fallback_validate_batch


async def _fallback_validate_batch(candidates: list, playbook_id=None) -> list:
    """
    Batch-shaped wrapper around agent6_fallback_validator's per-candidate
    validate_candidate(). Used only if agents.agent7 isn't importable.

    Sorts by confidence descending first, matching Agent 7's real
    validate_batch sorting order, so Fix 10's sequential diversification
    behaves consistently regardless of which validator is running.

    Also performs the Postgres audit insert itself (mirroring what real
    Agent 7's _result() does internally) — since Agent 6 no longer logs,
    something has to, or fallback-mode runs would leave no audit trail.
    """
    from agents.agent6_fallback_validator import validate_candidate as fallback_validate
    from db.postgres_queries import insert_procurement_evaluation

    sorted_candidates = sorted(candidates, key=lambda c: c.get("confidence", 0.0), reverse=True)
    results = []

    for candidate in sorted_candidates:
        validation = await fallback_validate(candidate, playbook_id)

        result = {
            "option_id": candidate["option_id"],
            "supplier": candidate["supplier"],
            "grade": candidate.get("grade", ""),
            "status": validation["status"],
            "reason": validation.get("reason"),
            "confidence": candidate.get("confidence", 0.0),
            "adjusted_volume_mbd": validation.get("adjusted_volume_mbd", 0.0),
        }
        results.append(result)

        try:
            insert_procurement_evaluation(
                playbook_id=playbook_id,
                option_id=candidate["option_id"],
                supplier=candidate["supplier"],
                grade=candidate.get("grade", ""),
                status=validation["status"],
                rule_triggered=(validation.get("reason") or {}).get("rule", ""),
                reason=validation.get("reason") or {},
                confidence=candidate.get("confidence", 0.0),
            )
        except Exception as e:
            logger.error(
                f"Agent 6 fallback batch: Failed to log evaluation "
                f"for {candidate['option_id']}: {e}"
            )

    return results 