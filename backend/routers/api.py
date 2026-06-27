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
# This gets replaced with real DB calls later
_playbooks = {"pb_001": copy.deepcopy(MOCK_PLAYBOOK)}
_risk_weights = {
    "military_incidents": 0.35,
    "conflict_signals": 0.25,
    "sanctions": 0.20,
    "market_volatility": 0.10,
    "seasonal": 0.10
}

# ---- JWT Middleware Skeleton -----------------------------
def validate_token_format(authorization: Optional[str]) -> bool:
    """
    Validates token format only — does not check DB yet.
    Real validation added on Day 5 when auth is built.
    For now: any Bearer token passes.
    """
    if not authorization:
        return False
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer":
        return False
    return True

# =========================================================
# ENDPOINTS
# =========================================================

# GET /api/risk-state
# Dashboard risk overview cards
@router.get("/risk-state")
async def get_risk_state():
    """
    Returns current corridor risk scores and system mode.
    Used by: Ministry dashboard risk cards, map overlay
    """
    return MOCK_RISK_STATE

# GET /api/events
# Recent verified events list
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
# Procurement options from Agent 6
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
# Full playbook details
@router.get("/playbook/{playbook_id}")
async def get_playbook(playbook_id: str):
    """
    Returns full crisis playbook by ID.
    Used by: All role dashboards during crisis
    """
    playbook = _playbooks.get(playbook_id)
    if not playbook:
        raise HTTPException(status_code=404, detail=f"Playbook {playbook_id} not found")
    return playbook

# PATCH /api/playbook/{id}/approve
# Analyst approves or rejects a playbook action
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
# Admin updates agent risk scoring weights
@router.patch("/risk-weights")
async def update_risk_weights(body: dict):
    """
    Updates the weights used by Agent 3 risk scoring formula.
    Body: {"military_incidents": 0.4, "conflict_signals": 0.2, ...}
    Used by: Admin dashboard
    """
    global _risk_weights
    total = sum(body.values())
    if abs(total - 1.0) > 0.01:
        raise HTTPException(
            status_code=400,
            detail=f"Weights must sum to 1.0, got {total:.2f}"
        )
    _risk_weights.update(body)
    return {"message": "Risk weights updated", "weights": _risk_weights}

# GET /api/agents/status
# All 8 agent run statuses
@router.get("/agents/status")
async def get_agents_status():
    """
    Returns last run time and status for all 8 agents.
    Used by: Admin dashboard, Ministry agent status panel
    """
    return {"agents": MOCK_AGENT_STATUS}

# GET /api/map/vessels
# Live tanker positions for Leaflet map
@router.get("/map/vessels")
async def get_vessels():
    """
    Returns tanker positions for map display.
    Hardcoded for demo — real AISHub data added later.
    Used by: Map component on all dashboards
    """
    return {"vessels": MOCK_VESSELS}

# GET /api/kgraph
# Knowledge graph nodes and edges for visualization
@router.get("/kgraph")
async def get_knowledge_graph():
    """
    Returns Knowledge Graph nodes and edges for D3 visualization.
    Used by: Knowledge Graph panel on Ministry dashboard
    """
    return MOCK_KGRAPH

# GET /api/spr/status
# SPR current level and schedule
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