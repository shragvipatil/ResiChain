# ============================================================
# ResiChain AI — FastAPI Main Entry Point
# ============================================================

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import redis.asyncio as aioredis
import os
import json
import logging
import asyncio

from db.postgres_queries import init_db
from db.redis_client import get_redis, init_redis_streams
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agents.crisis_graph import build_crisis_graph_definition


# Import routers (add more as you build agents)
# from routers import risk, playbook, agents, auth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---- Scheduler (APScheduler) --------------------------------
# Runs background polling jobs (Agent 1 every 5 minutes)
scheduler = AsyncIOScheduler()

# ---- WebSocket Connection Manager ---------------------------
class ConnectionManager:
    """Manages active WebSocket connections for real-time dashboard updates."""
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected dashboard clients."""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

# ---- Lifespan (startup + shutdown) --------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs on startup: initialise all DB connections, compile the crisis
    LangGraph with a long-lived checkpointer, and start the scheduler.
    Runs on shutdown: cleanly close connections + stop scheduler.

    IMPORTANT — checkpointer lifecycle: AsyncPostgresSaver is opened here
    via `async with` and kept open for the ENTIRE app lifetime (everything
    below, including `yield`, runs inside that block). Opening it fresh
    per-request instead (e.g. inside an endpoint) closes the connection
    the moment that block exits, breaking every subsequent request — a
    documented real-world FastAPI + LangGraph gotcha, not a hypothetical.
    """
    logger.info("ResiChain starting up...")

    # 1. Initialise PostgreSQL tables
    init_db()
    logger.info("PostgreSQL connected and tables ready")

    # 2. Initialise Redis streams
    await init_redis_streams()
    logger.info("Redis streams initialised (events:raw, risk:state)")

    # 3. Neo4j — no explicit init call; _get_driver() in neo4j_queries.py
    #    connects lazily on first actual query, not at startup. Tradeoff:
    #    a bad Neo4j connection won't surface until something queries it,
    #    not immediately at boot like the other three DBs do.

    # 4. Open the LangGraph checkpointer ONCE, for the app's lifetime.
    #    Everything else (Agent 2, scheduler, yield, shutdown) happens
    #    inside this block so the connection stays alive throughout.
    database_url = os.getenv("DATABASE_URL")
    async with AsyncPostgresSaver.from_conn_string(database_url) as checkpointer:
        # Fix 3: creates the checkpoint tables on first run. Safe to call
        # every startup — no-ops if tables already exist.
        await checkpointer.setup()
        logger.info("LangGraph checkpointer ready (Postgres, Fix 3)")

        # Compile the crisis graph once, with this checkpointer attached,
        # and store it on app.state so routes/other code can invoke it via
        # request.app.state.crisis_graph.ainvoke(...) without ever
        # recompiling or reopening a connection.
        crisis_graph_def = build_crisis_graph_definition()
        app.state.crisis_graph = crisis_graph_def.compile(checkpointer=checkpointer)
        logger.info("Crisis LangGraph compiled and attached to app.state")

        # 5. Initialise Agent 2 / ChromaDB
        #    Wrapped defensively: docker-compose.yml's chromadb healthcheck
        #    was only just fixed, and Agent 2's stream-name bug
        #    (events_verified vs events:verified) is still open on Person
        #    B's side. A failure here logs an error and continues startup
        #    instead of crashing the whole app and taking Agent 1/3/6 down
        #    with it.
        agent2_task = None
        try:
            from agents.agent2 import init_chromadb, run_agent2

            init_chromadb()
            logger.info("Agent 2 ChromaDB initialised")

            agent2_task = asyncio.create_task(run_agent2())
            logger.info("Agent 2 background loop started")

        except Exception as e:
            logger.error(
                f"Agent 2 startup failed: {e}. Continuing without Agent 2."
            )

        # 6. Start background scheduler
        from agents.agent1_ingestion import run_agent1_poll
        from agents.clients.ofac_client import download_and_store_ofac as ofac_download
        from agents.agent1_verification import run_verification_cycle, run_event_expiry
        from agents.agent3_risk_engine import run_agent3
        from agents.clients.market_client import fetch_vessel_positions, fetch_live_prices, fetch_alpha_vantage_daily

        # Agent 1 — polls every 5 minutes
        scheduler.add_job(
            run_agent1_poll,
            "interval",
            seconds=int(os.getenv("GDELT_POLL_INTERVAL_SECONDS", 300)),
            id="agent1_poll",
            replace_existing=True
        )

        # OFAC — downloads daily at 02:00 UTC
        scheduler.add_job(
            ofac_download,
            "cron",
            hour=2,
            minute=0,
            id="ofac_daily",
            replace_existing=True
        )

        # Verification layer — runs every 30 seconds
        scheduler.add_job(
            run_verification_cycle,
            "interval",
            seconds=30,
            id="agent1_verification",
            replace_existing=True
        )

        # Event expiry — runs every hour
        scheduler.add_job(
            run_event_expiry,
            "interval",
            hours=1,
            id="event_expiry",
            replace_existing=True
        )

        # Agent 3 — recalculates risk every 60 seconds
        scheduler.add_job(
            run_agent3,
            "interval",
            seconds=60,
            id="agent3_risk_engine",
            replace_existing=True
        )

        # Vessel positions — every 5 minutes
        scheduler.add_job(
            fetch_vessel_positions,
            "interval",
            seconds=300,
            id="vessel_polling",
            replace_existing=True
        )

        # Live prices — every 5 minutes
        scheduler.add_job(
            fetch_live_prices,
            "interval",
            seconds=300,
            id="price_polling",
            replace_existing=True
        )

        # Alpha Vantage daily historical — once per day
        scheduler.add_job(
            fetch_alpha_vantage_daily,
            "cron",
            hour=6,
            minute=0,
            id="alphavantage_daily",
            replace_existing=True
        )

        logger.info("Scheduled: Agent 1 every 5 min, OFAC daily at 02:00 UTC")
        logger.info("Scheduled: Verification every 30s, Expiry every 1h")
        logger.info("Scheduled: Agent 3 every 60 seconds")
        logger.info("Scheduled: Vessel + price polling every 5 min")
        scheduler.start()
        logger.info("APScheduler started")

        logger.info("ResiChain fully started. All systems nominal.")

        yield  # App runs here — checkpointer connection stays open throughout

        # --- Shutdown (still inside the checkpointer's `async with` block) ---
        logger.info("ResiChain shutting down...")

        if agent2_task is not None:
            agent2_task.cancel()
            try:
                await agent2_task
            except asyncio.CancelledError:
                logger.info("Agent 2 background task cancelled")

        scheduler.shutdown()
        logger.info("Scheduler stopped")
    # checkpointer connection closes here, after everything above completes

# ---- FastAPI App --------------------------------------------
app = FastAPI(
    title="ResiChain AI",
    description="Agentic Energy Supply Chain Resilience System",
    version="2.0.0",
    lifespan=lifespan
)

# ---- CORS (allow React frontend on localhost:3000) ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Include Routers ----------------------------------------
from routers.api import router as api_router
app.include_router(api_router) 

# ---- Health Check -------------------------------------------
@app.get("/health", tags=["System"])
async def health_check():
    """
    Health check endpoint.
    Docker depends_on uses this to confirm FastAPI is ready.
    """
    return {
        "status": "healthy",
        "service": "resichain-fastapi",
        "version": "2.0.0"
    }

# ---- Root ---------------------------------------------------
@app.get("/", tags=["System"])
async def root():
    return {
        "message": "ResiChain AI v2.0 — Energy Supply Chain Resilience System",
        "docs": "/docs",
        "health": "/health"
    }

# ---- Crisis Graph — manual trigger (Day 9) -------------------
@app.post("/api/crisis/trigger", tags=["Crisis"])
async def trigger_crisis_graph(request: Request):
    """
    Manually invokes the full crisis LangGraph (Agent 4 -> [5,6] -> 5 ->
    8-stub). Useful for testing the pipeline end-to-end without waiting
    for Agent 3 to actually push a corridor above the crisis threshold.

    Reads risk:state directly rather than accepting it in the request
    body, so it reflects whatever's actually live in Redis right now —
    same source Agent 6 already reads from.
    """
    from agents.crisis_graph import run_crisis_graph
    import json as _json

    r = await get_redis()
    data = await r.get("risk:state")
    risk_vector = _json.loads(data) if data else {}

    result = await run_crisis_graph(request.app.state.crisis_graph, risk_vector)
    return result

# ---- WebSocket — Agent Status Stream ------------------------
@app.websocket("/ws/agent-status")
async def websocket_agent_status(websocket: WebSocket):
    """
    WebSocket endpoint for real-time agent status updates.
    React dashboard connects here to receive live risk scores,
    agent run status, and crisis alerts.
    
    Message format:
    {
        "type": "risk_update" | "agent_status" | "crisis_alert" | "playbook_ready",
        "data": { ... }
    }
    """
    await manager.connect(websocket)
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connected",
            "data": {"message": "ResiChain WebSocket connected"}
        })
        
        # Keep connection alive — listen for client messages
        while True:
            data = await websocket.receive_text()
            # Echo back for now — will handle client commands later
            await websocket.send_json({
                "type": "ack",
                "data": {"received": data}
            })

    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ---- Broadcast helper (used by agents to push updates) ------
async def broadcast_to_dashboard(message_type: str, data: dict):
    """
    Call this from any agent to push real-time updates to the dashboard.
    Example:
        await broadcast_to_dashboard("risk_update", {
            "Hormuz": 0.82, "Red_Sea": 0.87, "Suez": 0.41, "Cape": 0.12
        })
    """
    await manager.broadcast({"type": message_type, "data": data}) 