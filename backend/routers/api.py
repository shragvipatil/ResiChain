# ============================================================
# ResiChain — Main API Router
# All fixes applied (Fix 2, 3, 5 from analysis)
# Merged clean version — psycopg aligned
# ============================================================

from fastapi import APIRouter, HTTPException, Header
from typing import Optional
import copy
import datetime

from contracts.api_contracts import (
    MOCK_RISK_STATE,
    MOCK_EVENTS,
    MOCK_PROCUREMENT_OPTIONS,
    MOCK_PLAYBOOK,
    MOCK_AGENT_STATUS,
    MOCK_VESSELS,
    MOCK_KGRAPH
)

router = APIRouter(prefix="/api", tags=["API"])

_playbooks = {"pb_001": copy.deepcopy(MOCK_PLAYBOOK)}
_risk_weights = {
    "military_incidents": 0.35,
    "conflict_escalation": 0.25,
    "sanctions_change": 0.25,
    "market_volatility": 0.10,
    "seasonal_risk": 0.05
}


def validate_token_format(authorization: Optional[str]) -> bool:
    if not authorization:
        return False
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer":
        return False
    return True


def _risk_to_status(score: float) -> str:
    if score >= 0.65:
        return "CRISIS"
    elif score >= 0.45:
        return "WATCH"
    return "NORMAL"


def _is_numeric_score(value) -> bool:
    """
    True for real int/float values, EXCLUDING bool (bool subclasses int
    in Python). Same fix applied across agent4.py/agent6.py/
    agent3_risk_engine.py/main.py.
    """
    return type(value) in (int, float)


def _get_system_mode(risk_data: dict) -> str:
    scores = [
        v for k, v in risk_data.items()
        if _is_numeric_score(v) and k not in ["updated_at"]
    ]
    if not scores:
        return "NORMAL"
    max_score = max(scores)
    if max_score >= 0.65:
        return "CRISIS"
    elif max_score >= 0.45:
        return "WATCH"
    return "NORMAL"


@router.get("/risk-state")
async def get_risk_state():
    """
    Returns LIVE corridor risk scores from Redis.
    Falls back to mock data if Redis cache expired.
    """
    from db.redis_client import get_redis
    import json

    try:
        r = await get_redis()
        data = await r.get("risk:state")
        if data:
            risk_data = json.loads(data)
            return {
                "corridors": {
                    k: {
                        "risk_score": v,
                        "status": _risk_to_status(v),
                        "trend": "stable"
                    }
                    for k, v in risk_data.items()
                    if k not in ["updated_at", "updated_corridors"]
                    and _is_numeric_score(v)
                },
                "updated_at": risk_data.get("updated_at"),
                "system_mode": _get_system_mode(risk_data)
            }
    except Exception:
        pass

    return MOCK_RISK_STATE


@router.get("/events")
async def get_events(limit: int = 10, corridor: Optional[str] = None):
    """Returns recent verified events from Agent 1."""
    events = MOCK_EVENTS
    if corridor:
        events = [e for e in events if e["corridor"] == corridor]
    return {"events": events[:limit], "total": len(events)}


@router.get("/procurement/options")
async def get_procurement_options(status: Optional[str] = None):
    """Returns procurement alternatives evaluated by Agent 6."""
    options = MOCK_PROCUREMENT_OPTIONS
    if status:
        options = [o for o in options if o["status"] == status]
    return {"options": options, "total": len(options)}


@router.get("/playbook/{playbook_id}")
async def get_playbook(playbook_id: str):
    """Returns full crisis playbook by ID."""
    playbook = _playbooks.get(playbook_id)
    if not playbook:
        raise HTTPException(
            status_code=404,
            detail=f"Playbook {playbook_id} not found"
        )
    return playbook


# PATCH /api/playbook/{id}/approve  (FIX 3 — accepts array)
@router.patch("/playbook/{playbook_id}/approve")
async def approve_playbook_action(
    playbook_id: str,
    body: dict,
    authorization: Optional[str] = Header(None)
):
    """
    Analyst approves or rejects procurement recommendations.
    Accepts BOTH single action and array of decisions.
    """
    playbook = _playbooks.get(playbook_id)
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")

    if "decisions" in body:
        decisions = body["decisions"]
    else:
        decisions = [body]

    results = []
    for item in decisions:
        action_id = item.get("action_id")
        decision = item.get("decision")
        note = item.get("note", "")

        if not action_id or decision not in ["approved", "rejected"]:
            continue

        entry = {"action_id": action_id, "note": note}
        if decision == "approved":
            playbook["approved_actions"].append(entry)
        else:
            playbook["rejected_actions"].append(entry)

        results.append({"action_id": action_id, "decision": decision})

    playbook["status"] = "in_review"

    return {
        "message": f"{len(results)} action(s) processed",
        "playbook_id": playbook_id,
        "results": results,
        "approved_count": len(playbook["approved_actions"]),
        "rejected_count": len(playbook["rejected_actions"])
    }


@router.patch("/risk-weights")
async def update_risk_weights(body: dict):
    """Updates risk factor weights and recalculates risk vector."""
    total = sum(body.values())
    if abs(total - 1.0) > 0.01:
        raise HTTPException(
            status_code=400,
            detail=f"Weights must sum to 1.0, got {total:.2f}"
        )

    from agents.agent3_risk_engine import update_risk_weights as agent3_update
    new_risk_vector = await agent3_update(body)

    return {
        "message": "Weights updated and risk recalculated",
        "new_weights": body,
        "new_risk_vector": new_risk_vector
    }


# GET /api/agents/status  (FIX 2 — dict shape, correct stream keys)
@router.get("/agents/status")
async def get_agents_status():
    """
    Returns last run time and status for all 8 agents.
    Reads real data from Redis where available.
    """
    from db.redis_client import get_redis
    import json

    r = await get_redis()

    agent1_data = await r.get("agent1:last_run")
    agent3_data = await r.get("agent3:last_run")
    agent5_data = await r.get("agent5:last_run")

    agent1_info = json.loads(agent1_data) if agent1_data else None
    agent3_info = json.loads(agent3_data) if agent3_data else None
    agent5_info = json.loads(agent5_data) if agent5_data else None

    try:
        raw_stream_len = await r.xlen("events:raw")
        verified_stream_len = await r.xlen("events:verified")
    except Exception:
        raw_stream_len = 0
        verified_stream_len = 0

    return {
        "agents": {
            "agent1": {
                "id": "agent1",
                "name": "Agent1_Ingestion",
                "status": "running" if agent1_info else "idle",
                "last_run": agent1_info.get("timestamp") if agent1_info else None,
                "events_processed": agent1_info.get("events_found", 0) if agent1_info else 0,
                "mode": agent1_info.get("system_mode", "NORMAL") if agent1_info else "NORMAL"
            },
            "agent2": {
                "id": "agent2",
                "name": "Agent2_Extraction",
                "status": "idle",
                "last_run": None,
                "mode": "STANDBY"
            },
            "agent3": {
                "id": "agent3",
                "name": "Agent3_RiskEngine",
                "status": "running" if agent3_info else "idle",
                "last_run": agent3_info.get("timestamp") if agent3_info else None,
                "mode": "RUNNING" if agent3_info else "STANDBY"
            },
            "agent4": {"id": "agent4", "name": "Agent4_Compound", "status": "standby", "last_run": None, "mode": "STANDBY"},
            "agent5": {
                "id": "agent5",
                "name": "Agent5_SPR",
                "status": "idle" if agent5_info else "standby",
                "last_run": agent5_info.get("timestamp") if agent5_info else None,
                "mode": "STANDBY"
            },
            "agent6": {"id": "agent6", "name": "Agent6_Procurement", "status": "standby", "last_run": None, "mode": "STANDBY"},
            "agent7": {"id": "agent7", "name": "Agent7_Validator", "status": "standby", "last_run": None, "mode": "STANDBY"},
            "agent8": {"id": "agent8", "name": "Agent8_Playbook", "status": "standby", "last_run": None, "mode": "STANDBY"}
        },
        "redis_stream_depths": {
            "events:raw": raw_stream_len,
            "events:verified": verified_stream_len
        },
        "checked_at": datetime.datetime.utcnow().isoformat()
    }


@router.get("/map/vessels")
async def get_vessels():
    """Returns tanker positions. Reads Redis cache, falls back to mock."""
    from db.redis_client import get_redis
    import json

    try:
        r = await get_redis()
        cached = await r.get("vessels:live")
        if cached:
            return {"vessels": json.loads(cached), "source": "live_cache"}
    except Exception:
        pass

    return {"vessels": MOCK_VESSELS, "source": "mock"}


@router.get("/kgraph")
async def get_knowledge_graph():
    """Returns Knowledge Graph nodes and edges for visualization."""
    return MOCK_KGRAPH


# GET /api/spr/status  (FIX 11 — note field added)
@router.get("/spr/status")
async def get_spr_status():
    """Returns current SPR levels and drawdown schedule."""
    return {
        "total_capacity_mb": 43.9,
        "current_level_mb": 38.0,
        "fill_pct": 86.6,
        "daily_consumption_mbd": 5.1,
        "days_cover": 7.45,
        "days_cover_with_commercial": 9.5,
        "active_drawdown": False,
        "drawdown_schedule": None,
        "note": "days_cover uses strategic SPR only (38mb). days_cover_with_commercial includes private commercial stocks. Government controls strategic reserve only."
    }


# GET /api/prices/live  (Day 7)
@router.get("/prices/live")
async def get_live_prices():
    """Returns live Brent and WTI prices from Redis cache."""
    from db.redis_client import get_redis
    import json

    try:
        r = await get_redis()
        cached = await r.get("prices:live")
        if cached:
            return {"prices": json.loads(cached), "source": "cache"}
    except Exception:
        pass

    from agents.clients.market_client import fetch_live_prices
    prices = await fetch_live_prices()
    if prices:
        return {"prices": prices, "source": "live_fetch"}

    return {
        "prices": {
            "brent": {"price": 82.0, "change_pct": 0.0, "commodity": "Brent Crude"},
            "wti": {"price": 78.5, "change_pct": 0.0, "commodity": "WTI Crude"}
        },
        "source": "fallback"
    }


# GET /api/spr/schedule/latest  (Day 7)
@router.get("/spr/schedule/latest")
async def get_spr_schedule():
    """Returns latest SPR drawdown schedule from Agent 5."""
    from db.redis_client import get_redis
    import json

    try:
        r = await get_redis()
        cached = await r.get("spr:schedule:latest")
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    return {
        "status": "no_active_scenario",
        "message": "No SPR schedule generated yet. Trigger via demo crisis inject.",
        "confidence": None
    }


# POST /api/spr/optimize  (Day 7 — uses Person B's agent5)
@router.post("/spr/optimize")
async def trigger_spr_optimization(body: dict = {}):
    """
    Manually triggers Agent 5 SPR optimization.
    Uses Person B's solve_spr_schedule (LangGraph-compatible agent5.py).
    Optional body: {"import_gap_mbd": 1.5} -> converted to a flat import shortfall.
    """
    from agents.agent5 import solve_spr_schedule

    import_gap = body.get("import_gap_mbd", None)

    # Person B's solver takes available_imports per day, not a gap.
    # Convert a single gap figure into a 30-day flat available-imports list:
    # available = max(0, consumption - gap). If no gap given, pass None (uses live data).
    available_imports = None
    if import_gap is not None:
        consumption = 5.1  # India daily consumption mbd (fallback baseline)
        daily_available = max(0.0, consumption - float(import_gap))
        available_imports = [daily_available] * 30

    result = solve_spr_schedule(available_imports_mbd=available_imports)
    return result


# POST /api/demo/inject-crisis
@router.post("/demo/inject-crisis")
async def inject_demo_crisis(corridor: str = "Hormuz", severity: int = 8):
    """DEMO ONLY — injects fake crisis event to trigger the pipeline."""
    from agents.agent1_ingestion import run_agent1_demo_inject
    import asyncio
    asyncio.create_task(run_agent1_demo_inject(corridor, severity))
    return {
        "message": f"Demo crisis injected for {corridor}",
        "severity": severity,
        "note": "UKMTO confirmation follows in 10 seconds"
    }


# POST /api/debug/broadcast-test
@router.post("/debug/broadcast-test")
async def test_broadcast(body: dict):
    """Manually triggers a WebSocket broadcast to all connected clients."""
    from main import broadcast_to_dashboard
    message_type = body.get("type", "test")
    data = body.get("data", {"message": "test broadcast"})
    await broadcast_to_dashboard(message_type, data)
    return {
        "message": "Broadcast sent to all connected WebSocket clients",
        "type": message_type,
        "data": data
    }


# GET /api/debug/corridor-state
@router.get("/debug/corridor-state")
async def get_corridor_state():
    """Debug — shows current verification state."""
    from agents.agent1_verification import _active_corridor_events

    state = {}
    for corridor, events in _active_corridor_events.items():
        state[corridor] = {
            "event_count": len(events),
            "sources": list(set(e["source"] for e in events)),
            "max_severity": max((e["severity"] for e in events), default=0),
            "latest_event": max(
                (e["event_time"].isoformat() for e in events), default=None
            )
        }

    return {
        "active_corridors": state,
        "total_active_events": sum(
            len(v) for v in _active_corridor_events.values()
        ),
        "checked_at": datetime.datetime.utcnow().isoformat()
    }


# GET /api/debug/verified-events  (uses Person B's psycopg query)
@router.get("/debug/verified-events")
async def get_verified_events_debug(limit: int = 10):
    """Shows recent verified events from PostgreSQL via Person B's query layer."""
    from db.postgres_queries import get_verified_events
    rows = get_verified_events(limit=limit, offset=0)
    return {
        "verified_events": rows,
        "total": len(rows)
    } 

# POST /api/procurement/evaluate  (Day 8)
@router.post("/procurement/evaluate")
async def trigger_procurement_evaluation(body: dict = {}):
    """
    Manually triggers Agent 6's full procurement evaluation cycle.
    Runs the rejection-retry loop through Agent 7 for every surviving
    supplier and returns the ranked APPROVED/PARTIAL list plus the
    full rejection trace (including BLOCKED options with reasons).
 
    Optional body: {"playbook_id": "<uuid>"} to tie evaluations to a playbook.
    """
    from agents.agent6 import run_agent6
    playbook_id = body.get("playbook_id")
    result = await run_agent6(playbook_id=playbook_id)
    return result
 
 
# GET /api/procurement/last-run  (Day 8)
@router.get("/procurement/last-run")
async def get_last_procurement_run():
    """Returns the cached result of the most recent Agent 6 run."""
    from db.redis_client import get_redis
    import json
 
    try:
        r = await get_redis()
        cached = await r.get("agent6:last_run")
        if cached:
            return json.loads(cached)
    except Exception:
        pass
 
    return {
        "status": "no_run_yet",
        "message": "Agent 6 has not run yet. Trigger via POST /api/procurement/evaluate"
    } 