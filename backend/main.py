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

from db.postgres import init_db
from db.redis_client import get_redis, init_redis_streams
from db.neo4j_client import init_neo4j


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
    await init_db()
    logger.info("PostgreSQL connected and tables ready")

    # 2. Initialise Redis streams
    await init_redis_streams()
    logger.info("Redis streams initialised (events:raw, risk:state)")

    # 3. Initialise Neo4j connection
    await init_neo4j()
    logger.info("Neo4j connected")

   # 4. ChromaDB — disabled temporarily, will re-add later
    logger.info("ChromaDB skipped for now") 

    # 5. Start background scheduler
    # Agent 1 polling will be added here once built
    # scheduler.add_job(agent1_poll, "interval", seconds=int(os.getenv("GDELT_POLL_INTERVAL_SECONDS", 300)))
    scheduler.start()
    logger.info("APScheduler started")

    logger.info("ResiChain fully started. All systems nominal.")

    yield  # App runs here

    # --- Shutdown ---
    logger.info("ResiChain shutting down...")
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
# Uncomment as you build each module
# app.include_router(auth.router, prefix="/auth", tags=["Auth"])
# app.include_router(risk.router, prefix="/risk", tags=["Risk"])
# app.include_router(playbook.router, prefix="/playbook", tags=["Playbook"])
# app.include_router(agents.router, prefix="/agents", tags=["Agents"])

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