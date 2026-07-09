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
    """
    Returns recent verified events — REAL data from the Postgres
    verified_events table (written by Agent 1's verification layer).
    Falls back to mock only if the query fails.
    """
    import asyncio as _asyncio
    try:
        from db.postgres_queries import get_verified_events
        rows = await _asyncio.to_thread(get_verified_events, 100, 0)
        events = []
        for row in rows:
            ev = row.get("event_json") or {}
            ev["id"] = str(row.get("id"))
            ev["created_at"] = str(row.get("created_at"))
            events.append(ev)
        if corridor:
            events = [e for e in events if e.get("corridor") == corridor]
        return {"events": events[:limit], "total": len(events), "source": "postgres"}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"/events: real query failed, serving mock: {e}")
        events = MOCK_EVENTS
        if corridor:
            events = [e for e in events if e["corridor"] == corridor]
        return {"events": events[:limit], "total": len(events), "source": "mock_fallback"}


@router.get("/procurement/options")
async def get_procurement_options(status: Optional[str] = None):
    """
    Returns procurement alternatives — REAL data from Agent 6's most
    recent run (agent6:last_run Redis cache, written after every
    /procurement/evaluate or crisis-graph run). Falls back to mock only
    if Agent 6 has never run or the cache expired (10 min TTL).
    """
    from db.redis_client import get_redis
    import json as _json
    try:
        r = await get_redis()
        cached = await r.get("agent6:last_run")
        if cached:
            data = _json.loads(cached)
            options = data.get("full_rejection_trace", [])
            if status:
                options = [o for o in options if o.get("status") == status]
            return {
                "options": options,
                "total": len(options),
                "generated_at": data.get("generated_at"),
                "source": "agent6_last_run",
            }
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"/procurement/options: cache read failed: {e}")

    options = MOCK_PROCUREMENT_OPTIONS
    if status:
        options = [o for o in options if o["status"] == status]
    return {"options": options, "total": len(options), "source": "mock_fallback"}


@router.get("/playbook/{playbook_id}")
async def get_playbook(playbook_id: str):
    """
    Returns full crisis playbook by ID — REAL data from the Postgres
    playbooks table (written by Agent 8). Requires Person B's
    get_playbook_by_id() helper; until it lands, falls back to the
    in-memory demo playbook so Person C's UI keeps working.
    """
    try:
        import asyncio as _asyncio
        from db.postgres_queries import get_playbook_by_id
        row = await _asyncio.to_thread(get_playbook_by_id, playbook_id)
        if row:
            row["source"] = "postgres"
            return row
    except ImportError:
        pass  # helper not built yet — fall through to in-memory demo store
    except Exception:
        pass

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
    """
    Returns the REAL seeded Knowledge Graph from Neo4j (Fix 14),
    transformed into the exact shape the frontend's D3 code was built
    against (MOCK_KGRAPH shape): nodes {id, label, type, ...props},
    edges {from, to, label}. Falls back to mock only on failure so the
    dashboard never renders empty.
    """
    try:
        from db.neo4j_queries import get_graph_for_visualization
        import asyncio as _asyncio

        raw = await _asyncio.to_thread(get_graph_for_visualization)

        nodes = []
        for n in raw.get("nodes", []):
            props = n.get("props", {}) or {}
            labels = n.get("labels", []) or []
            nodes.append({
                "id": str(n.get("id")),
                "label": props.get("name", str(n.get("id"))),
                "type": labels[0] if labels else "Unknown",
                **{k: v for k, v in props.items() if k != "name"},
            })

        edges = []
        for e in raw.get("edges", []):
            edges.append({
                "from": str(e.get("source")),
                "to": str(e.get("target")),
                "label": e.get("type", ""),
            })

        if not nodes:
            return {**MOCK_KGRAPH, "source": "mock_fallback_empty_graph"}
        return {"nodes": nodes, "edges": edges, "source": "neo4j"}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"kgraph: Neo4j fetch failed, serving mock: {e}")
        return {**MOCK_KGRAPH, "source": "mock_fallback_error"}


# GET /api/spr/status  (Day 12 — wired to real sources)
@router.get("/spr/status")
async def get_spr_status():
    """
    Returns current SPR levels — REAL data assembled from three sources
    that previously disagreed (hardcoded 43.9/38.0 here vs .env's
    SPR_TOTAL_MB=38 vs Neo4j's actual summed StorageFacility.capacity_mb):

    - total_capacity_mb: Neo4j get_spr_total_volume() (sum of real
      StorageFacility nodes) — the actual source of truth for capacity.
    - current_level_mb: latest persisted SPR schedule's spr_remaining_mb
      (Postgres, Agent 5's real solve) if one exists; otherwise equals
      total_capacity_mb (nothing drawn down yet).
    - daily_consumption_mbd: agents/simulation.py's live EIA API fetch
      (falls back to a constant internally if EIA_API_KEY missing/fails
      — that fallback is real code behavior, not something this endpoint
      re-implements).
    - active_drawdown / drawdown_schedule: reflect whether a real
      schedule exists and is actually drawing down the reserve.

    days_cover_with_commercial has NO real data source anywhere in this
    system (nothing tracks private commercial stock levels) — kept as
    an explicitly-labeled estimate (same 1.275x ratio as the original
    hardcoded 9.5/7.45), not represented as real.

    Falls back to the original hardcoded snapshot only if all three
    real sources fail, so the endpoint never hard-errors.
    """
    import asyncio as _asyncio

    try:
        from db.neo4j_queries import get_spr_total_volume
        from agents.simulation import _get_india_daily_consumption

        total_capacity_mb = await _asyncio.to_thread(get_spr_total_volume)
        daily_consumption_mbd = await _asyncio.to_thread(_get_india_daily_consumption)

        current_level_mb = total_capacity_mb
        active_drawdown = False
        drawdown_schedule = None

        try:
            from db.postgres_queries import get_latest_spr_schedule
            latest = await _asyncio.to_thread(get_latest_spr_schedule)
            if latest:
                current_level_mb = latest.get("spr_remaining_mb", total_capacity_mb)
                schedule = latest.get("daily_drawdown_schedule") or []
                if any(v > 0 for v in schedule):
                    active_drawdown = True
                    drawdown_schedule = schedule
        except ImportError:
            pass  # helper not built yet — current_level defaults to full capacity

        fill_pct = round((current_level_mb / total_capacity_mb) * 100, 2) if total_capacity_mb > 0 else 0.0
        days_cover = round(current_level_mb / daily_consumption_mbd, 2) if daily_consumption_mbd > 0 else 0.0

        return {
            "total_capacity_mb": round(total_capacity_mb, 2),
            "current_level_mb": round(current_level_mb, 2),
            "fill_pct": fill_pct,
            "daily_consumption_mbd": round(daily_consumption_mbd, 3),
            "days_cover": days_cover,
            "days_cover_with_commercial": round(days_cover * 1.275, 2),
            "active_drawdown": active_drawdown,
            "drawdown_schedule": drawdown_schedule,
            "note": "days_cover uses strategic SPR only. days_cover_with_commercial is an ESTIMATE (1.275x ratio) — no real commercial-stock data source exists in this system.",
            "source": "live",
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"/spr/status: real data assembly failed, serving snapshot: {e}")
        return {
            "total_capacity_mb": 43.9,
            "current_level_mb": 38.0,
            "fill_pct": 86.6,
            "daily_consumption_mbd": 5.1,
            "days_cover": 7.45,
            "days_cover_with_commercial": 9.5,
            "active_drawdown": False,
            "drawdown_schedule": None,
            "note": "FALLBACK snapshot — live data assembly failed, see server logs.",
            "source": "fallback",
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

    # Day 12 spec: "returns the latest LP-solved schedule from PostgreSQL".
    # Preferred source is the spr_schedules table (Agent 5 persists every
    # solve there). Requires Person B's get_latest_spr_schedule() helper —
    # defensively imported until it lands, then this just starts working.
    try:
        import asyncio as _asyncio
        from db.postgres_queries import get_latest_spr_schedule
        row = await _asyncio.to_thread(get_latest_spr_schedule)
        if row:
            row["source"] = "postgres"
            return row
    except ImportError:
        pass  # helper not built yet — fall through to Redis cache
    except Exception:
        pass

    try:
        r = await get_redis()
        cached = await r.get("spr:schedule:latest")
        if cached:
            data = json.loads(cached)
            data["source"] = "redis_cache"
            return data
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


# GET /api/audit/events  (Day 12 — paginated event history)
@router.get("/audit/events")
async def get_audit_events(limit: int = 20, offset: int = 0):
    """
    Paginated verified-event history from Postgres — the audit trail
    Person C's admin/audit views read. limit capped at 100.
    """
    import asyncio as _asyncio
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    try:
        from db.postgres_queries import get_verified_events
        rows = await _asyncio.to_thread(get_verified_events, limit, offset)
        events = []
        for row in rows:
            ev = row.get("event_json") or {}
            events.append({
                "id": str(row.get("id")),
                "corridor": row.get("corridor"),
                "stage": row.get("stage"),
                "confidence": row.get("confidence"),
                "created_at": str(row.get("created_at")),
                "event": ev,
            })
        return {
            "events": events,
            "limit": limit,
            "offset": offset,
            "count": len(events),
            "source": "postgres",
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"/audit/events failed: {e}")
        raise HTTPException(status_code=500, detail="Audit query failed")


# GET /api/simulation/run  (Day 12 — four formulas on CURRENT risk state)
@router.get("/simulation/run")
async def run_simulation():
    """
    Runs all four simulation formulas (agents/simulation.py: import
    disruption, SPR drawdown, Brent price impact, refinery utilization)
    against the CURRENT live risk state, and returns all four outputs.

    How the inputs are assembled:
    - affected_chokepoint = the corridor with the highest current risk
      in Redis risk:state; closure_severity = that risk score.
    - supplier_route_risks built live: each supplier's route (from
      Neo4j, chokepoint parsed from the route name), their current
      import share (Neo4j SUPPLIES relationship), and their route's
      corridor risk from risk:state.
    simulation.run_all itself is sync (its own sync Redis/Neo4j calls),
    so the whole thing runs via asyncio.to_thread.
    """
    import asyncio as _asyncio
    import json as _json
    from db.redis_client import get_redis

    # 1. Current risk vector (numeric corridors only — bool excluded)
    r = await get_redis()
    data = await r.get("risk:state")
    raw = _json.loads(data) if data else {}
    risk_vector = {k: v for k, v in raw.items() if _is_numeric_score(v)}

    if not risk_vector:
        raise HTTPException(
            status_code=409,
            detail="risk:state empty — Agent 3 has not produced a risk vector yet",
        )

    affected_chokepoint = max(risk_vector, key=risk_vector.get)
    closure_severity = risk_vector[affected_chokepoint]

    # 2. Supplier route risks from Neo4j + risk vector
    def _build_inputs():
        from db.neo4j_queries import get_surviving_routes, get_supplier_current_share
        routes = get_surviving_routes([])  # no blocks: every route
        entries, seen = [], set()
        for route in routes:
            supplier = route.get("supplier")
            if not supplier or supplier in seen:
                continue
            seen.add(supplier)
            route_name = route.get("route", "")
            chokepoint = (
                route_name.split(" via ")[-1].strip()
                if " via " in route_name else "Unknown"
            )
            try:
                share = get_supplier_current_share(supplier)
            except Exception:
                share = 0.0
            entries.append({
                "supplier": supplier,
                "primary_chokepoint": chokepoint,
                "import_share": share,
                "route_risk": risk_vector.get(chokepoint, 0.0),
            })
        return entries

    supplier_route_risks = await _asyncio.to_thread(_build_inputs)

    # 3. Get the real post-drawdown SPR level, if one exists.
    # Person B's run_all() takes spr_remaining_mb as optional — None
    # correctly falls back to the static baseline
    # (spr_total_mb / daily_consumption_mbd), which is the accurate
    # number when no Agent 5 run has actually happened yet. When a real
    # schedule exists (Postgres, via Agent 5's LP solver), pass its
    # true remaining level so spr_cover_days reflects the actual
    # post-crisis figure instead of always reporting the baseline.
    spr_remaining_mb = None
    try:
        from db.postgres_queries import get_latest_spr_schedule
        latest_schedule = await _asyncio.to_thread(get_latest_spr_schedule)
        if latest_schedule:
            spr_remaining_mb = latest_schedule.get("spr_remaining_mb")
    except ImportError:
        pass  # helper not built yet — None correctly falls back to baseline
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"/simulation/run: get_latest_spr_schedule failed, using baseline: {e}"
        )

    # 4. Run all four formulas (sync module — offloaded)
    from agents.simulation import run_all
    result = await _asyncio.to_thread(
        run_all,
        supplier_route_risks,
        closure_severity,
        affected_chokepoint,
        spr_remaining_mb=spr_remaining_mb,
    )

    result["inputs"] = {
        "affected_chokepoint": affected_chokepoint,
        "closure_severity": closure_severity,
        "risk_vector": risk_vector,
        "supplier_route_risks": supplier_route_risks,
        "spr_remaining_mb_used": spr_remaining_mb,
    }
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