# ============================================================
# ResiChain — Main API Router
# All endpoints return mock data for now
# Person C builds against these exact shapes
# ============================================================

from fastapi import APIRouter, HTTPException, Header
from typing import Optional
import copy

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


def _get_system_mode(risk_data: dict) -> str:
    scores = [
        v for k, v in risk_data.items()
        if isinstance(v, (int, float)) and k not in ["updated_at"]
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
                    and isinstance(v, (int, float))
                },
                "updated_at": risk_data.get("updated_at"),
                "system_mode": _get_system_mode(risk_data)
            }
    except Exception:
        pass

    return MOCK_RISK_STATE


@router.get("/events")
async def get_events(limit: int = 10, corridor: Optional[str] = None):
    events = MOCK_EVENTS
    if corridor:
        events = [e for e in events if e["corridor"] == corridor]
    return {"events": events[:limit], "total": len(events)}


@router.get("/procurement/options")
async def get_procurement_options(status: Optional[str] = None):
    options = MOCK_PROCUREMENT_OPTIONS
    if status:
        options = [o for o in options if o["status"] == status]
    return {"options": options, "total": len(options)}


@router.get("/playbook/{playbook_id}")
async def get_playbook(playbook_id: str):
    playbook = _playbooks.get(playbook_id)
    if not playbook:
        raise HTTPException(
            status_code=404,
            detail=f"Playbook {playbook_id} not found"
        )
    return playbook


@router.patch("/playbook/{playbook_id}/approve")
async def approve_playbook_action(
    playbook_id: str,
    body: dict,
    authorization: Optional[str] = Header(None)
):
    playbook = _playbooks.get(playbook_id)
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")

    action_id = body.get("action_id")
    decision = body.get("decision")
    note = body.get("note", "")

    if not action_id or decision not in ["approved", "rejected"]:
        raise HTTPException(
            status_code=400,
            detail="Body must have action_id and decision (approved/rejected)"
        )

    entry = {"action_id": action_id, "note": note}

    if decision == "approved":
        playbook["approved_actions"].append(entry)
    else:
        playbook["rejected_actions"].append(entry)

    playbook["status"] = "in_review"

    return {
        "message": f"Action {action_id} {decision}",
        "playbook_id": playbook_id,
        "approved_count": len(playbook["approved_actions"]),
        "rejected_count": len(playbook["rejected_actions"])
    }


@router.patch("/risk-weights")
async def update_risk_weights(body: dict):
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


@router.get("/agents/status")
async def get_agents_status():
    from db.redis_client import get_redis
    import json

    r = await get_redis()

    agent1_data = await r.get("agent1:last_run")
    agent3_data = await r.get("agent3:last_run")

    agent1_info = json.loads(agent1_data) if agent1_data else None
    agent3_info = json.loads(agent3_data) if agent3_data else None

    try:
        raw_stream_len = await r.xlen("events:raw")
        verified_stream_len = await r.xlen("events:verified")
    except Exception:
        raw_stream_len = 0
        verified_stream_len = 0

    agents = [
        {
            "agent": "Agent1_Ingestion",
            "status": "running" if agent1_info else "idle",
            "last_run": agent1_info.get("timestamp") if agent1_info else None,
            "events_processed": agent1_info.get("events_found", 0) if agent1_info else 0,
            "queue_depth": raw_stream_len,
            "mode": agent1_info.get("system_mode", "NORMAL") if agent1_info else "NORMAL"
        },
        {
            "agent": "Agent2_Extraction",
            "status": "idle",
            "last_run": None,
            "queue_depth": verified_stream_len,
            "mode": "STANDBY"
        },
        {
            "agent": "Agent3_RiskEngine",
            "status": "running" if agent3_info else "idle",
            "last_run": agent3_info.get("timestamp") if agent3_info else None,
            "risk_scores_updated": 4 if agent3_info else 0,
            "queue_depth": 0,
            "mode": "RUNNING" if agent3_info else "STANDBY"
        },
        {"agent": "Agent4_Compound", "status": "standby", "last_run": None, "queue_depth": 0, "mode": "STANDBY"},
        {"agent": "Agent5_SPR", "status": "standby", "last_run": None, "queue_depth": 0, "mode": "STANDBY"},
        {"agent": "Agent6_Procurement", "status": "standby", "last_run": None, "queue_depth": 0, "mode": "STANDBY"},
        {"agent": "Agent7_Validator", "status": "standby", "last_run": None, "queue_depth": 0, "mode": "STANDBY"},
        {"agent": "Agent8_Playbook", "status": "standby", "last_run": None, "queue_depth": 0, "mode": "STANDBY"}
    ]

    return {
        "agents": agents,
        "stream_depths": {
            "events_raw": raw_stream_len,
            "events_verified": verified_stream_len
        },
        "checked_at": __import__('datetime').datetime.utcnow().isoformat()
    }


@router.get("/map/vessels")
async def get_vessels():
    from db.redis_client import get_redis
    import json

    try:
        r = await get_redis()
        cached = await r.get("ais:vessels:latest")
        if cached:
            vessels = json.loads(cached)
            return {"vessels": vessels, "source": "live_cache"}
    except Exception:
        pass

    return {"vessels": MOCK_VESSELS, "source": "mock"}


@router.get("/kgraph")
async def get_knowledge_graph():
    return MOCK_KGRAPH


@router.get("/spr/status")
async def get_spr_status():
    return {
        "total_capacity_mb": 43.9,
        "current_level_mb": 38.0,
        "fill_pct": 86.6,
        "daily_consumption_mbd": 5.1,
        "days_cover": 7.45,
        "days_cover_with_commercial": 9.5,
        "active_drawdown": False,
        "drawdown_schedule": None
    }


@router.post("/demo/inject-crisis")
async def inject_demo_crisis(corridor: str = "Hormuz", severity: int = 8):
    from agents.agent1_ingestion import run_agent1_demo_inject
    import asyncio
    asyncio.create_task(run_agent1_demo_inject(corridor, severity))
    return {
        "message": f"Demo crisis injected for {corridor}",
        "severity": severity,
        "note": "UKMTO confirmation follows in 10 seconds"
    }


@router.get("/debug/corridor-state")
async def get_corridor_state():
    from agents.agent1_verification import _active_corridor_events
    from datetime import datetime

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
        "checked_at": datetime.utcnow().isoformat()
    }


@router.get("/debug/verified-events")
async def get_verified_events_debug(limit: int = 10):
    from db.postgres_queries import get_verified_events
    rows = get_verified_events(limit=limit, offset=0)
    return {
        "verified_events": rows,
        "total": len(rows)
    }


@router.post("/debug/broadcast-test")
async def test_broadcast(body: dict):
    from main import broadcast_to_dashboard

    message_type = body.get("type", "test")
    data = body.get("data", {"message": "test broadcast"})

    await broadcast_to_dashboard(message_type, data)

    return {
        "message": "Broadcast sent to all connected WebSocket clients",
        "type": message_type,
        "data": data
    }