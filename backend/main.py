# ============================================================
# ResiChain AI — FastAPI Main Entry Point
# ============================================================

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import redis.asyncio as aioredis
import os
import json
import logging
import asyncio

from db.postgres_queries import init_db
from db.redis_client import get_redis, init_redis_streams
from db.neo4j_queries import init_neo4j


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
    Runs on startup: initialise all DB connections + start scheduler.
    Runs on shutdown: cleanly close connections + stop scheduler.
    """
    logger.info("ResiChain starting up...")

    # 1. Initialise PostgreSQL tables
    init_db()
    logger.info("PostgreSQL connected and tables ready")

    # 2. Initialise Redis streams
    await init_redis_streams()
    logger.info("Redis streams initialised (events:raw, risk:state)")

    # 3. Initialise Neo4j connection
    await init_neo4j()
    logger.info("Neo4j connected")

    # 4. Initialise Agent 2 / ChromaDB
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


    # 5. Start background scheduler
    # Agent 1 polling will be added here once built
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

    yield  # App runs here

    # --- Shutdown ---
    logger.info("ResiChain shutting down...")

    if agent2_task is not None:
        agent2_task.cancel()
        try:
            await agent2_task
        except asyncio.CancelledError:
            logger.info("Agent 2 background task cancelled")

    scheduler.shutdown()
    logger.info("Scheduler stopped")

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

from routers.pdf_router import router as pdf_router
app.include_router(pdf_router)

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