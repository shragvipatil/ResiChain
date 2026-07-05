"""
agents/crisis_graph.py
=======================
ResiChain AI v2.0 — Crisis Mode LangGraph

Wires Agents 4-8 into a single directed graph, meant to be invoked when
the system enters CRISIS mode (any corridor risk >= 0.65, per Agent 3's
CRISIS_THRESHOLD).

Flow:
    Agent 4 (compound disruption analysis)
        -> [Agent 5 first pass, Agent 6] run in parallel
        -> Agent 5 second pass (Fix 7) — re-runs using Agent 6's
           approved/partial candidates as the confirmed import schedule
        -> Agent 8 (playbook generation) — STUB until someone builds it

NOTE on Agent 7: it does NOT appear as a separate node here. Agent 7's
validate_candidate() is already called inside agent6.py's own
rejection-retry loop (once per procurement candidate) — by the time the
"agent6" node below finishes, every candidate has already been through
Agent 7. There's no separate contract yet for Agent 7 validating Agent 5's
SPR schedule output, so nothing here does that. Flag with the team if
that's expected to exist.

IMPORTANT — this module does NOT compile the graph or attach a
checkpointer. See main.py's lifespan: the checkpointer needs a
long-lived connection kept open for the app's lifetime (not opened
fresh per-invocation), so compilation happens exactly once at startup
and the compiled graph is stored on app.state.crisis_graph.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict, Annotated
from uuid import uuid4

from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reducer for parallel-branch writes
# ---------------------------------------------------------------------------

def _take_latest(existing, incoming):
    """
    Reducer for state keys that parallel branches may both touch in the
    same step. agent5_first and agent6 run concurrently and rejoin at
    agent5_second — without a reducer, LangGraph raises InvalidUpdateError
    ("can receive only one value per step") when both branches' writes
    land together.

    Each key here is in practice written by only ONE branch, so there's no
    true conflict to resolve — this reducer just accepts whichever branch
    actually produced a value (preferring a non-None incoming value, else
    keeping what's there). This is deliberately simple rather than a deep
    merge, because no two branches write the *same* key with *different*
    meaningful values.
    """
    return incoming if incoming is not None else existing


# ---------------------------------------------------------------------------
# Shared graph state
# ---------------------------------------------------------------------------

class CrisisGraphState(TypedDict, total=False):
    playbook_id: Annotated[str, _take_latest]
    risk_vector: Annotated[Dict[str, float], _take_latest]

    # Agent 4 output
    compound_risk: Annotated[Optional[float], _take_latest]
    blocked_chokepoints: Annotated[List[str], _take_latest]
    is_compound_event: Annotated[bool, _take_latest]
    surviving_routes: Annotated[List[dict], _take_latest]

    # Agent 5 (first pass) — pre-Agent-6 rough estimate
    surviving_routes_mbd: Annotated[List[float], _take_latest]
    spr_schedule_first_pass: Annotated[dict, _take_latest]

    # Agent 6 output (Agent 7 validation already embedded inside it)
    procurement_result: Annotated[dict, _take_latest]

    # Agent 5 (second pass, Fix 7) — re-run with Agent 6's approved volumes
    approved_cargoes_mbd: Annotated[List[float], _take_latest]
    spr_schedule_final: Annotated[dict, _take_latest]

    # Agent 8 output
    playbook: Annotated[dict, _take_latest]


# ---------------------------------------------------------------------------
# WebSocket broadcast helper — fires after every node completes
# ---------------------------------------------------------------------------

async def _broadcast_node_complete(node_name: str, extra: Optional[dict] = None) -> None:
    """
    Notifies the dashboard's pipeline status panel that a node just
    finished, so it can animate progress live (Day 9 requirement).
    Wrapped defensively — a broadcast failure should never take down
    the actual pipeline.
    """
    try:
        from main import broadcast_to_dashboard
        await broadcast_to_dashboard("PIPELINE_NODE_COMPLETE", {
            "node": node_name,
            "timestamp": datetime.utcnow().isoformat(),
            **(extra or {}),
        })
    except Exception as e:
        logger.error(f"Crisis graph: WebSocket broadcast failed for {node_name}: {e}")


# ---------------------------------------------------------------------------
# Agent 5 async adapter (Agent 5 itself is a SYNC function — see Person B's
# confirmation: agent5.py exposes plain `def`, not `async def`, including
# run_agent5(). Running it directly inside an async node would block the
# whole event loop for the LP solve + any blocking network calls inside it.
# asyncio.to_thread offloads it to a worker thread instead.
# ---------------------------------------------------------------------------

async def _run_agent5_async(state: dict) -> dict:
    """
    Adapter that works whether agents.agent5.run_agent5 is sync OR async.

    History (why this exists): run_agent5 was originally a plain sync
    `def` (confirmed by Person B at the time), so this wrapper used
    asyncio.to_thread unconditionally. It has since been converted to
    `async def` by a later edit, which made the to_thread call blow up
    with "TypeError: object dict can't be used in 'await' expression".
    Rather than hardcode the new assumption and break again the next
    time the file changes hands, detect at runtime:
      - async def  -> await it directly
      - plain def  -> offload to a worker thread (so a blocking LP solve
                      can't freeze the event loop)

    ALSO DEFENSIVE: if run_agent5 returns None (its error paths can),
    normalize to {} — a None here propagates through node returns and
    can null the entire merged graph state downstream (observed in a
    real run as agent8 receiving state=None).
    """
    import inspect
    from agents.agent5 import run_agent5

    if inspect.iscoroutinefunction(run_agent5):
        result = await run_agent5(state)
    else:
        result = await asyncio.to_thread(run_agent5, state)

    if result is None:
        logger.error(
            "Crisis graph: run_agent5 returned None (likely its internal "
            "error path) — normalizing to empty dict so the graph state "
            "doesn't get nulled downstream."
        )
        return {}
    return result


# ---------------------------------------------------------------------------
# Cross-agent data adapters — FLAGGED ASSUMPTIONS, not confirmed with
# Person B. See the "needs confirmation" list at the bottom of this file.
# ---------------------------------------------------------------------------

def _estimate_available_imports_mbd(surviving_routes: List[dict], horizon_days: int = 30) -> List[float]:
    """
    ASSUMPTION: Agent 5's first pass wants a 30-day daily import-volume
    estimate (available_imports_mbd), but get_surviving_routes() only
    returns route metadata — no volume figures. Estimating total daily
    capacity via each unique surviving supplier's contract headroom
    (same source agent6.py already uses via get_contract_headroom()),
    applied flat across every day of the horizon (no ramp-up modeled).
    """
    from db.neo4j_queries import get_contract_headroom

    seen_suppliers = set()
    total_headroom_mbd = 0.0

    for route in surviving_routes:
        supplier = route.get("supplier")
        if not supplier or supplier in seen_suppliers:
            continue
        seen_suppliers.add(supplier)
        try:
            contract = get_contract_headroom(supplier)
            total_headroom_mbd += contract.get("headroom_mbd", 0.0)
        except Exception as e:
            logger.warning(f"Crisis graph: headroom fetch failed for {supplier}: {e}")

    daily_estimate = round(total_headroom_mbd, 4)
    return [daily_estimate] * horizon_days


def _approved_candidates_to_cargo_schedule(ranked_options: List[dict], horizon_days: int = 30) -> List[float]:
    """
    ASSUMPTION: sums adjusted_volume_mbd (falling back to
    requested_volume_mbd) across every APPROVED/PARTIAL candidate from
    Agent 6, and applies that total flat across the horizon as Agent 5's
    "approved_cargoes_mbd" for its Fix 7 re-run. Real cargoes arrive on
    discrete dates (per avg_transit_days per supplier), not evenly every
    day — this is a reasonable Day 9 placeholder, not a real shipping
    schedule. Confirm with Person B / whoever builds Agent 8 whether a
    flat rate is acceptable, or whether per-cargo arrival dates matter.
    """
    total_mbd = 0.0
    for option in ranked_options:
        volume = option.get("adjusted_volume_mbd")
        if not volume:
            volume = option.get("requested_volume_mbd", 0.0)
        total_mbd += volume

    return [round(total_mbd, 4)] * horizon_days


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

async def node_agent4(state: CrisisGraphState) -> dict:
    from agents.agent4 import run_agent4
    updated = await run_agent4(state)
    await _broadcast_node_complete("agent4", {
        "compound_risk": updated.get("compound_risk"),
        "is_compound_event": updated.get("is_compound_event"),
    })

    # Day 9 spec: Person C's frontend listens for a WebSocket event of
    # type "compound_disruption_detected" to trigger the Leaflet Cape
    # route polyline animation. This exact event type was never being
    # emitted anywhere — only the generic PIPELINE_NODE_COMPLETE — so
    # the animation could never fire. Emitting it here, only when a
    # genuine compound event is detected.
    if updated.get("is_compound_event"):
        try:
            from main import broadcast_to_dashboard
            await broadcast_to_dashboard("compound_disruption_detected", {
                "compound_risk": updated.get("compound_risk"),
                "blocked_chokepoints": updated.get("blocked_chokepoints", []),
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            logger.error(f"Crisis graph: compound_disruption_detected broadcast failed: {e}")

    return updated


async def node_agent5_first_pass(state: CrisisGraphState) -> dict:
    surviving_routes = state.get("surviving_routes", [])
    surviving_routes_mbd = _estimate_available_imports_mbd(surviving_routes)

    state_for_agent5 = {**state, "surviving_routes_mbd": surviving_routes_mbd}
    updated = await _run_agent5_async(state_for_agent5)

    # Same present-but-None guard as agent5_second — see note there.
    spr_schedule = updated.get("spr_schedule") or {}
    await _broadcast_node_complete("agent5_first_pass", {
        "feasible": spr_schedule.get("feasible"),
    })
    # Return ONLY the keys this branch produces — not a full {**state}
    # spread. During the parallel step, spreading the whole state would
    # re-emit agent6's keys too, compounding the InvalidUpdateError. The
    # reducer merges these back with agent6's separate writes.
    return {
        "surviving_routes_mbd": surviving_routes_mbd,
        "spr_schedule_first_pass": spr_schedule,
    }


async def node_agent6(state: CrisisGraphState) -> dict:
    from agents.agent6 import run_agent6
    playbook_id = state.get("playbook_id")
    result = await run_agent6(playbook_id=playbook_id)

    await _broadcast_node_complete("agent6", {
        "approved_count": result.get("approved_count"),
        "blocked_count": result.get("blocked_count"),
    })
    # Only this branch's own key — see node_agent5_first_pass note.
    return {"procurement_result": result}


async def node_agent5_second_pass(state: CrisisGraphState) -> dict:
    """
    Fix 7: re-runs Agent 5 using Agent 6's approved/partial candidates
    (already validated through Agent 7 inside Agent 6's own node) as the
    confirmed import schedule, instead of the rough first-pass estimate.

    Depends on BOTH agent5_first_pass and agent6 having completed —
    wired as a join point below via two incoming edges.
    """
    procurement_result = state.get("procurement_result", {})
    ranked_options = procurement_result.get("ranked_options", [])
    approved_cargoes_mbd = _approved_candidates_to_cargo_schedule(ranked_options)

    state_for_agent5 = {**state, "approved_cargoes_mbd": approved_cargoes_mbd}
    updated = await _run_agent5_async(state_for_agent5)

    # (updated.get("spr_schedule") or {}) not updated.get("spr_schedule", {}):
    # .get's default only applies when the key is ABSENT — if the key
    # exists holding None (which happens when Agent 5's error path runs),
    # .get returns None and .get("feasible") on it crashes. Observed in a
    # real run.
    spr_schedule = updated.get("spr_schedule") or {}
    await _broadcast_node_complete("agent5_second_pass", {
        "feasible": spr_schedule.get("feasible"),
    })
    return {
        "approved_cargoes_mbd": approved_cargoes_mbd,
        "spr_schedule_final": spr_schedule,
    }


async def node_agent8_stub(state: CrisisGraphState) -> dict:
    """
    TEMPORARY — nobody has built Agent 8 (Playbook Generator) yet.
    Assembles a minimal, clearly-flagged playbook shape from whatever
    Agent 5 and Agent 6 produced, so the graph completes end-to-end today
    instead of erroring at the last node. Replace this entire function
    with a real call the moment Agent 8 exists.

    DEFENSIVE: state arrived as None in a real run (AttributeError:
    'NoneType' object has no attribute 'get') — root cause under
    investigation with the None-state log below. A None state here should
    degrade to an explicitly-flagged empty playbook, not a 500 for the
    whole pipeline.
    """
    if state is None:
        logger.error(
            "Crisis graph: node_agent8_stub received state=None — upstream "
            "node returned a value that merged to nothing. Emitting empty "
            "flagged playbook instead of crashing."
        )
        state = {}

    playbook = {
        "playbook_id": state.get("playbook_id"),
        "status": "STUB — Agent 8 not yet built, this is NOT a real playbook",
        "compound_risk": state.get("compound_risk"),
        "blocked_chokepoints": state.get("blocked_chokepoints"),
        "spr_schedule_first_pass": state.get("spr_schedule_first_pass"),
        "spr_schedule_final": state.get("spr_schedule_final"),
        "procurement_result": state.get("procurement_result"),
        "generated_at": datetime.utcnow().isoformat(),
    }
    await _broadcast_node_complete("agent8_stub", {"status": playbook["status"]})
    return {"playbook": playbook}


# ---------------------------------------------------------------------------
# Graph definition (uncompiled — no checkpointer attached here)
# ---------------------------------------------------------------------------

def build_crisis_graph_definition() -> StateGraph:
    """
    Returns the uncompiled graph builder. Compile this ONCE, with a
    long-lived checkpointer, inside main.py's lifespan — not per
    invocation. See main.py for why.
    """
    workflow = StateGraph(CrisisGraphState)

    workflow.add_node("agent4", node_agent4)
    workflow.add_node("agent5_first", node_agent5_first_pass)
    workflow.add_node("agent6", node_agent6)
    workflow.add_node("agent5_second", node_agent5_second_pass)
    workflow.add_node("agent8", node_agent8_stub)

    workflow.set_entry_point("agent4")

    # Parallel branch: Agent 5 (first pass) and Agent 6 both fire after Agent 4.
    workflow.add_edge("agent4", "agent5_first")
    workflow.add_edge("agent4", "agent6")

    # Join: agent5_second has two incoming edges, so LangGraph runs it only
    # once BOTH agent5_first and agent6 have completed in the same step.
    # NOTE: verify this executes as a true single join rather than firing
    # twice once Agent 7 lands and the graph can actually be run end-to-end
    # — this is standard LangGraph fan-in behavior, but worth confirming
    # empirically rather than trusting blind, since I can't execute this
    # graph myself to verify.
    workflow.add_edge("agent5_first", "agent5_second")
    workflow.add_edge("agent6", "agent5_second")

    workflow.add_edge("agent5_second", "agent8")
    workflow.add_edge("agent8", END)

    return workflow


# ---------------------------------------------------------------------------
# Invocation helper — takes the ALREADY-COMPILED graph (from app.state)
# ---------------------------------------------------------------------------

async def run_crisis_graph(compiled_graph, risk_vector: dict, playbook_id: Optional[str] = None) -> dict:
    """
    Invokes the crisis graph. `compiled_graph` must be the graph already
    compiled with a checkpointer in main.py's lifespan (app.state.crisis_graph)
    — this function does not compile anything itself.
    """
    if playbook_id is None:
        playbook_id = str(uuid4())

    initial_state: CrisisGraphState = {
        "playbook_id": playbook_id,
        "risk_vector": risk_vector,
    }
    config = {"configurable": {"thread_id": playbook_id}}

    logger.warning(f"Crisis graph: starting run for playbook_id={playbook_id}")
    result = await compiled_graph.ainvoke(initial_state, config=config)
    logger.warning(f"Crisis graph: completed for playbook_id={playbook_id}")
    return result 