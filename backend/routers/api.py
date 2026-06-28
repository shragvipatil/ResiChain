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

# ---- In-memory state for mock approvals -----------------
_playbooks = {"pb_001": copy.deepcopy(MOCK_PLAYBOOK)}
_risk_weights = {
    "military_incidents": 0.35,
    "conflict_escalation": 0.25,
    "sanctions_change": 0.25,
    "market_volatility": 0.10,
    "seasonal_risk": 0.05
}

# ---- JWT Middleware Skeleton -----------------------------
def validate_token_format(authorization: Optional[str]) -> bool:
    if not authorization:
        return False
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer":
        return False
    return True

# ---- Helper functions (module level) --------------------
def _risk_to_status(score: float) -> str:
    if score >= 0.65:
        return "CRISIS"
    elif score >= 0.45:
        return "WATCH"
    return "NORMAL"


def _get_system_mode(risk_data: dict) -> str:
    scores = [
        v for k, v in risk_data.items()
        if isinstance(v, (int, float))
        and k not in ["updated_at"]
    ]
    if not scores:
        return "NORMAL"
    max_score = max(scores)
    if max_score >= 0.65:
        return "CRISIS"
    elif max_score >= 0.45:
        return "WATCH"
    return "NORMAL"

# =========================================================
# ENDPOINTS
# =========================================================

# GET /api/risk-state
@router.get("/risk-state")
async def get_risk_state():
    """
    Returns LIVE corridor risk scores from Redis.
    Falls back to mock data if Redis cache expired.
    Used by: Ministry dashboard risk cards, map overlay
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
                    and isinstance(v, (int, float))
                },
                "updated_at": risk_data.get("updated_at"),
                "system_mode": _get_system_mode(risk_data)
            }
    except Exception:
        pass

    return MOCK_RISK_STATE


# GET /api/events
@router.get("/events")
async def get_events(limit: int = 10, corridor: Optional[str] = None):
    """
    Returns recent verified events from Agent 1.
    Used by: Events feed on all dashboards
    """
    events = MOCK_EVENTS
    if corridor:
        events = [e for e in events if e["corridor"] == corridor]
    return {"events": events[:limit], "total": len(events)}


# GET /api/procurement/options
@router.get("/procurement/options")
async def get_procurement_options(status: Optional[str] = None):
    """
    Returns procurement alternatives evaluated by Agent 6.
    Used by: Procurement Analyst dashboard
    """
    options = MOCK_PROCUREMENT_OPTIONS
    if status:
        options = [o for o in options if o["status"] == status]
    return {"options": options, "total": len(options)}


# GET /api/playbook/{id}
@router.get("/playbook/{playbook_id}")
async def get_playbook(playbook_id: str):
    """
    Returns full crisis playbook by ID.
    Used by: All role dashboards during crisis
    """
    playbook = _playbooks.get(playbook_id)
    if not playbook:
        raise HTTPException(
            status_code=404,
            detail=f"Playbook {playbook_id} not found"
        )
    return playbook


# PATCH /api/playbook/{id}/approve
@router.patch("/playbook/{playbook_id}/approve")
async def approve_playbook_action(
    playbook_id: str,
    body: dict,
    authorization: Optional[str] = Header(None)
):
    """
    Analyst approves or rejects a procurement recommendation.
    Body: {"action_id": "proc_001", "decision": "approved", "note": "optional"}
    Used by: Procurement Analyst dashboard
    """
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


# PATCH /api/risk-weights
@router.patch("/risk-weights")
async def update_risk_weights(body: dict):
    """
    Updates risk factor weights and immediately recalculates
    risk vector with new weights.
    Body: {"military_incidents": 0.4, "conflict_escalation": 0.2, ...}
    Used by: Admin dashboard weight sliders
    """
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


# GET /api/agents/status
@router.get("/agents/status")
async def get_agents_status():
    """
    Returns last run time and status for all 8 agents.
    Used by: Admin dashboard, Ministry agent status panel
    """
    return {"agents": MOCK_AGENT_STATUS}


# GET /api/map/vessels
@router.get("/map/vessels")
async def get_vessels():
    """
    Returns tanker positions for map display.
    Hardcoded for demo — real AISHub data added later.
    Used by: Map component on all dashboards
    """
    return {"vessels": MOCK_VESSELS}


# GET /api/kgraph
@router.get("/kgraph")
async def get_knowledge_graph():
    """
    Returns Knowledge Graph nodes and edges for D3 visualization.
    Used by: Knowledge Graph panel on Ministry dashboard
    """
    return MOCK_KGRAPH


# GET /api/spr/status
@router.get("/spr/status")
async def get_spr_status():
    """
    Returns current SPR levels and drawdown schedule.
    Used by: Ministry dashboard SPR card
    """
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


# POST /api/demo/inject-crisis
@router.post("/demo/inject-crisis")
async def inject_demo_crisis(
    corridor: str = "Hormuz",
    severity: int = 8
):
    """
    DEMO ONLY endpoint.
    Injects a fake crisis event to trigger the full agent pipeline.
    Called at Minute 2 of the live demo.
    """
    from agents.agent1_ingestion import run_agent1_demo_inject
    import asyncio
    asyncio.create_task(run_agent1_demo_inject(corridor, severity))
    return {
        "message": f"Demo crisis injected for {corridor}",
        "severity": severity,
        "note": "UKMTO confirmation follows in 10 seconds"
    }


# GET /api/debug/corridor-state
@router.get("/debug/corridor-state")
async def get_corridor_state():
    """
    Debug endpoint — shows current verification state.
    Use this to confirm events are flowing through the pipeline.
    """
    from agents.agent1_verification import _active_corridor_events
    from datetime import datetime

    state = {}
    for corridor, events in _active_corridor_events.items():
        state[corridor] = {
            "event_count": len(events),
            "sources": list(set(e["source"] for e in events)),
            "max_severity": max(
                (e["severity"] for e in events), default=0
            ),
            "latest_event": max(
                (e["event_time"].isoformat() for e in events),
                default=None
            )
        }

    return {
        "active_corridors": state,
        "total_active_events": sum(
            len(v) for v in _active_corridor_events.values()
        ),
        "checked_at": datetime.utcnow().isoformat()
    }


# GET /api/debug/verified-events
@router.get("/debug/verified-events")
async def get_verified_events(limit: int = 10):
    """
    Shows recent verified events from PostgreSQL.
    Use this to confirm WATCH/CONFIRMED events are being written.
    """
    from db.postgres import get_db_pool
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT corridor, stage, confidence,
                   sources_confirming, max_severity, created_at
            FROM verified_events
            ORDER BY created_at DESC
            LIMIT $1
        """, limit)

    return {
        "verified_events": [dict(row) for row in rows],
        "total": len(rows)
    } 