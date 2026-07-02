# ============================================================
# ResiChain — Agent 6: Procurement Orchestrator
# Finds surviving supply routes, builds procurement candidates,
# scores them, and runs each through Agent 7's constraint checks
# (rejection-retry loop). Produces a ranked list + full audit trail.
#
# UPDATED to match Person B's confirmed Agent 7 contract:
#   validate_candidate(candidate: dict, playbook_id: str | None = None) -> dict
#   - Agent 6 no longer builds or passes running_share.
#     Agent 7 owns diversification state internally (Fix 10).
#   - Candidate dict field names now match the shared schema exactly:
#     option_id, supplier, grade, route, refinery, arrival_port,
#     proposed_volume_mbd, confidence, contract_reference,
#     vessel_class, departure_port
#
# FIX (this revision):
#   1. Blocked-chokepoint naming mismatch — Redis risk:state uses short
#      names ("Hormuz", "Suez", "Cape") but Neo4j Chokepoint.name uses
#      full names ("Strait of Hormuz", "Suez Canal", "Cape of Good Hope").
#      Added CHOKEPOINT_SHORT_TO_FULL mapping applied before calling
#      get_surviving_routes(), so the exact-match IN filter actually works.
#   2. _extract_chokepoint_from_route_name assumed underscore-joined route
#      names ("<supplier>_to_India_via_<chokepoint>") but real seeded route
#      names use spaces ("Saudi to Jamnagar via Hormuz") — always returned
#      "Unknown". Fixed to split on " via " instead of "_via_".
# ============================================================


import json
import logging
from datetime import datetime
from db.redis_client import get_redis
from db.neo4j_queries import (
    get_surviving_routes,
    get_all_supplier_grades,
    get_contract_headroom,
)
from db.postgres_queries import insert_procurement_evaluation


logger = logging.getLogger(__name__)


RISK_SURVIVAL_THRESHOLD = 0.40  # corridors at/above this are considered "blocked"


# Port -> Refinery mapping (matches Day 1 Neo4j seed)
# Used to pick a target refinery for grade compatibility checks.
PORT_TO_REFINERY = {
    "Vadinar": "Jamnagar RIL",
    "Sikka":   "Jamnagar RIL",
    "Paradip": "Paradip IOCL",
    "Kochi":   "Kochi BPCL",
}


# Supplier -> primary export/departure terminal.
# Reference lookup (demo-scope, same style as PORT_TO_REFINERY above) —
# not pulled from a live API since no data source currently tracks this.
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


# Chokepoint -> typical tanker class for that corridor.
# Heuristic for demo purposes: Suez/Red Sea routes are canal-constrained
# (Suezmax is literally sized for the Suez Canal); Hormuz/Cape routes
# commonly run VLCC given the longer haul economics.
# NOTE: keys here are the SHORT chokepoint names (as extracted from route
# names via " via "), not the full Neo4j Chokepoint.name values.
VESSEL_CLASS_BY_CHOKEPOINT = {
    "Hormuz":  "VLCC",
    "Cape":    "VLCC",
    "Suez":    "Suezmax",
    "Red_Sea": "Suezmax",
}
DEFAULT_VESSEL_CLASS = "Suezmax"


# Short chokepoint name (used in Redis risk:state and in route names,
# e.g. "Saudi to Jamnagar via Hormuz") -> full Chokepoint.name as seeded
# in Neo4j (e.g. "Strait of Hormuz"). Required because get_surviving_routes()
# does an exact match: `c.name IN $blocked_chokepoints`.
CHOKEPOINT_SHORT_TO_FULL = {
    "Hormuz": "Strait of Hormuz",
    "Suez": "Suez Canal",
    "Cape": "Cape of Good Hope",
    "Red_Sea": "Red Sea",
}


DEFAULT_DAILY_CONSUMPTION_MBD = 5.1



async def run_agent6(playbook_id=None) -> dict:
    """
    Main Agent 6 entry point.


    Flow:
    1. Read current corridor risk from Redis, determine blocked chokepoints
    2. Query Neo4j for surviving routes (suppliers whose routes avoid blocked chokepoints)
    3. Build a procurement candidate per surviving supplier
    4. Score each candidate (route availability, grade compat, price, contract headroom)
    5. Run rejection-retry loop through Agent 7 (or fallback validator) —
       Agent 7 owns diversification state (running_share) internally
    6. Log every evaluation to PostgreSQL procurement_evaluations
    7. Return ranked list of APPROVED / PARTIAL options + full rejection trace


    NOTE: Route-node schema repair is no longer done here — Person B's
    seed_knowledge_graph.py owns that as an idempotent seed step now.
    """
    logger.info("Agent 6: Starting procurement evaluation cycle...")
    start_time = datetime.utcnow()


    # Step 1 — determine blocked chokepoints from live risk state (short names,
    # e.g. "Hormuz"), then translate to the full Neo4j Chokepoint.name values
    # (e.g. "Strait of Hormuz") before querying the graph.
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


    # Step 4 — rejection-retry loop through Agent 7
    # NOTE: no running_share is built or passed here anymore.
    # Agent 7 initializes it from current supplier shares and updates it
    # sequentially per candidate internally (Fix 10 lives entirely in Agent 7 now).
    validator = await _get_validator()
    results = []


    for candidate in candidates:
        option_id = candidate["option_id"]


        validation = await validator(candidate, playbook_id)


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


        # Log to PostgreSQL (Person B's query layer)
        try:
            insert_procurement_evaluation(
                playbook_id=playbook_id,
                option_id=option_id,
                supplier=candidate["supplier"],
                grade=candidate.get("grade", ""),
                status=validation["status"],
                rule_triggered=(validation.get("reason") or {}).get("rule", ""),
                reason=validation.get("reason") or {},
                confidence=candidate.get("confidence", 0.0),
            )
        except Exception as e:
            logger.error(f"Agent 6: Failed to log evaluation for {option_id}: {e}")


        logger.info(
            f"Agent 6: {option_id} ({candidate['supplier']}) -> "
            f"{validation['status']}"
            + (f" [{validation['reason']['rule']}]" if validation.get("reason") else "")
        )


    # Step 5 — rank approved/partial options by confidence
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


    # Cache latest result in Redis for the dashboard / Agent 8
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
    """Reads live risk:state from Redis, returns corridors (short names,
    e.g. 'Hormuz') at/above the survival threshold."""
    try:
        r = await get_redis()
        data = await r.get("risk:state")
        if not data:
            return []
        risk_data = json.loads(data)
        return [
            corridor for corridor, score in risk_data.items()
            if isinstance(score, (int, float)) and score >= RISK_SURVIVAL_THRESHOLD
        ]
    except Exception as e:
        logger.error(f"Agent 6: Failed to read risk state: {e}")
        return []



async def _build_candidates(surviving_routes: list, blocked_chokepoints_short: list) -> list:
    """
    Builds one procurement candidate per surviving supplier.
    Attaches grade, price, confidence score.


    Emits the shared candidate schema agreed with Person B:
    option_id, supplier, grade, route, refinery, arrival_port,
    proposed_volume_mbd, confidence, contract_reference,
    vessel_class, departure_port  (+ extra scoring/display fields)
    """
    if not surviving_routes:
        return []


    # Pull current risk vector once (used for route_avail scoring).
    # Keys here are SHORT chokepoint names, matching route-name extraction.
    risk_vector = {}
    try:
        r = await get_redis()
        data = await r.get("risk:state")
        if data:
            risk_vector = {
                k: v for k, v in json.loads(data).items()
                if isinstance(v, (int, float))
            }
    except Exception:
        pass


    # Pull live price once
    brent_price, price_source = await _get_live_brent_price()


    # Pull supplier -> grade mapping
    try:
        supplier_grades = get_all_supplier_grades()
    except Exception as e:
        logger.error(f"Agent 6: get_all_supplier_grades failed: {e}")
        supplier_grades = []


    grade_by_supplier = {g["supplier"]: g for g in supplier_grades}


    # Deduplicate by supplier — one candidate per supplier (best/first route)
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


        # Determine which chokepoint this route primarily uses, by checking
        # the route name. Real seeded route names use spaces and the word
        # "via", e.g. "Saudi to Jamnagar via Hormuz" — NOT the underscore
        # format ("<supplier>_to_India_via_<chokepoint>") originally assumed.
        route_name = route.get("route", "")
        primary_chokepoint = _extract_chokepoint_from_route_name(route_name)
        corridor_risk = risk_vector.get(primary_chokepoint, 0.3)
        vessel_class = VESSEL_CLASS_BY_CHOKEPOINT.get(primary_chokepoint, DEFAULT_VESSEL_CLASS)


        # ---- Scoring ----
        route_avail = max(0.0, 1.0 - corridor_risk)


        try:
            grade_compat = 1.0 if _quick_grade_check(grade, refinery) else 0.0
        except Exception:
            grade_compat = 1.0  # fail-open


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
            # Extra fields kept for scoring/dashboard/audit display —
            # not part of Agent 7's contract but harmless to include.
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
    """
    Route names are seeded as '<supplier> to <arrival_port> via <chokepoint>'
    (space-separated, e.g. "Saudi to Jamnagar via Hormuz") — pull the
    chokepoint back out.

    FIX: previously split on the underscore-joined form "_via_", which never
    matched the real space-separated seed format and always fell through to
    "Unknown". Now splits on " via " instead.
    """
    if " via " in route_name:
        return route_name.split(" via ")[-1].strip()
    return "Unknown"



def _quick_grade_check(grade: str, refinery: str) -> bool:
    """Lightweight sync wrapper — real check happens again in Agent 7's Layer 2."""
    from db.neo4j_queries import check_grade_compatibility
    return check_grade_compatibility(grade, refinery)



def _estimate_price_premium(api_gravity: float, sulfur_pct: float) -> float:
    """
    Simple grade-differential heuristic:
    Heavier/more sour crude (lower API gravity, higher sulfur) costs less to buy
    but more to refine — approximate net premium here for ranking purposes.
    """
    sulfur_penalty = max(0.0, (sulfur_pct - 1.0) * 3.0)
    gravity_bonus = min(2.0, max(0.0, (api_gravity - 30.0) / 10.0))
    premium_pct = max(0.0, sulfur_penalty - gravity_bonus)
    return premium_pct



async def _get_live_brent_price() -> tuple:
    """Reads current Brent price from Redis prices:live cache."""
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



async def _get_validator():
    """
    Prefers Person B's real agents.agent7.validate_candidate() if it exists.
    Falls back to the local 4-layer validator if Agent 7 isn't ready yet.


    Confirmed contract (both real and fallback must match):
        validate_candidate(candidate: dict, playbook_id: str | None = None) -> dict
    Agent 7 owns running_share / diversification state internally — Agent 6
    does not build or pass it.
    """
    try:
        from agents.agent7 import validate_candidate
        logger.info("Agent 6: Using Person B's agents.agent7.validate_candidate")
        return validate_candidate
    except (ImportError, AttributeError):
        logger.warning(
            "Agent 6: agents.agent7.validate_candidate not found — "
            "using fallback validator. Agent 7 is not blocking Agent 6."
        )
        from agents.agent6_fallback_validator import validate_candidate as fallback
        return fallback