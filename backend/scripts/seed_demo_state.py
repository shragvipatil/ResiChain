"""
backend/scripts/seed_demo_state.py

Demo pre-seed script — run 2 MINUTES BEFORE every demo and test run.

    docker-compose exec fastapi python scripts/seed_demo_state.py

Sets the exact Section-12 pre-demo state:
  1. risk:state          -> Hormuz 0.34, Red_Sea 0.41, Suez 0.18, Cape 0.05
  2. vessels:live        -> 3 mock AIS tanker positions in the Arabian Sea
  3. audit_events (PG)   -> 1 historical resolved alert (Houthi drone near
                            Bab-el-Mandeb, 6 days ago, stage='resolved')
  4. agentN:last_run     -> agents 1, 2, 3, 5 (5 is read by the admin
                            dashboard) show a run within the last 5 minutes
  5. demo:risk_freeze    -> 30-min flag telling Agent 3 NOT to overwrite
                            risk:state with recomputed (near-zero) values.
                            Without this, Agent 3's 60-second scheduled job
                            wipes the seeded state before the demo starts —
                            the exact race condition documented on Day 12.

Idempotent: re-running replaces state, never stacks duplicate rows
(the demo audit row is deleted by event_id before re-insert).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Bootstrap: seed_knowledge_graph.py never needed this because it talks to
# Neo4j directly and never imports db.*. This script does (db.redis_client,
# db.postgres_queries), and the Dockerfile has no PYTHONPATH=/app set — so
# `python scripts/seed_demo_state.py` only puts /app/scripts on sys.path,
# not /app, and `db` (which lives at /app/db) can't be found. Inserting the
# parent directory here makes the script work no matter how it's invoked
# (plain path, `python -m scripts.seed_demo_state`, or a different cwd).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.redis_client import (
    get_redis,
    update_risk_state,
    set_vessels_live,
)
from db.postgres_queries import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed_demo_state")

# ---------------------------------------------------------------------------
# Section 12 pre-demo constants
# ---------------------------------------------------------------------------

DEMO_RISK_STATE = {
    "Hormuz": 0.34,
    "Red_Sea": 0.41,
    "Suez": 0.18,
    "Cape": 0.05,
}

# Same dict shape as market_client._get_demo_vessel_positions() so every
# consumer (Agent 7 tanker checks, /api dashboard map) parses identically.
DEMO_VESSELS = [
    {
        "mmsi": "477111001",
        "name": "GULF CARRIER",
        "lat": 24.5, "lon": 56.3,
        "speed": 12.4, "heading": 95,
        "destination": "SIKKA",
        "vessel_type": "crude_tanker",
        "source": "demo_seed",
    },
    {
        "mmsi": "477111002",
        "name": "ARABIAN STAR",
        "lat": 22.1, "lon": 60.2,
        "speed": 11.8, "heading": 110,
        "destination": "VADINAR",
        "vessel_type": "crude_tanker",
        "source": "demo_seed",
    },
    {
        "mmsi": "477111003",
        "name": "INDIA SPIRIT",
        "lat": 19.8, "lon": 63.4,
        "speed": 13.1, "heading": 120,
        "destination": "PARADIP",
        "vessel_type": "crude_tanker",
        "source": "demo_seed",
    },
]

# Deterministic ID so re-running the seed replaces (not duplicates) the row.
DEMO_AUDIT_EVENT_ID = "DEMO-SEED-HOUTHI-BABELMANDEB"

RISK_FREEZE_KEY = "demo:risk_freeze"
RISK_FREEZE_TTL_SECONDS = 1800  # 30 min — auto-expires, can't leak past demo

AGENT_LAST_RUN_TTL = 600  # matches what the real agents use


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

async def seed_risk_state() -> None:
    """Step 1 — write the Section-12 calm-baseline risk vector."""
    now_iso = datetime.utcnow().isoformat()
    risk_vector = {
        **DEMO_RISK_STATE,
        # Same extra fields Agent 3 writes, so consumers see an identical shape.
        "updated_at": now_iso,
        "updated_corridors": [],
    }
    await update_risk_state(risk_vector)
    logger.info("risk:state seeded: %s", DEMO_RISK_STATE)


async def seed_vessels() -> None:
    """Step 2 — three mock AIS tanker positions in the Arabian Sea."""
    await set_vessels_live(DEMO_VESSELS)
    logger.info("vessels:live seeded with %d Arabian Sea tankers", len(DEMO_VESSELS))


def seed_audit_event_sync() -> None:
    """
    Step 3 — one historical, already-resolved alert so the audit trail
    isn't empty at demo start: Houthi drone near Bab-el-Mandeb, 6 days ago.

    Sync (psycopg) — invoked from async main via asyncio.to_thread.
    Delete-then-insert on the fixed event_id keeps this idempotent.
    """
    six_days_ago = datetime.now(timezone.utc) - timedelta(days=6)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM audit_events WHERE event_id = %s",
                (DEMO_AUDIT_EVENT_ID,),
            )
            cur.execute(
                """
                INSERT INTO audit_events (
                    event_id, source, corridor, stage,
                    confidence, verified_at, archived_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    DEMO_AUDIT_EVENT_ID,
                    "UKMTO",
                    "Red_Sea",
                    "resolved",
                    0.94,
                    six_days_ago,
                    six_days_ago + timedelta(hours=9),
                ),
            )
    logger.info(
        "audit_events: inserted resolved Houthi drone alert (Bab-el-Mandeb, %s)",
        six_days_ago.date().isoformat(),
    )


async def seed_agent_heartbeats() -> None:
    """
    Step 4 — make all background agents report a run within the last
    5 minutes. Payload shapes copied from each agent's real setex call:
      - agent1: timestamp/duration_ms/events_found/corridors_active/system_mode
      - agent3: timestamp/corridors_updated/risk_vector
      - agent2 has no reader today (written for spec completeness)
      - agent5 included because routers/api.py's agent-status endpoint
        reads agent1/agent3/agent5 for the Admin dashboard.
    """
    r = await get_redis()
    now_iso = datetime.utcnow().isoformat()

    await r.setex("agent1:last_run", AGENT_LAST_RUN_TTL, json.dumps({
        "timestamp": now_iso,
        "duration_ms": 1240,
        "events_found": 0,
        "corridors_active": 0,
        "system_mode": "NORMAL",
    }))
    await r.setex("agent2:last_run", AGENT_LAST_RUN_TTL, json.dumps({
        "timestamp": now_iso,
        "events_processed": 0,
        "extraction_method": "idle",
    }))
    await r.setex("agent3:last_run", AGENT_LAST_RUN_TTL, json.dumps({
        "timestamp": now_iso,
        "corridors_updated": [],
        "risk_vector": {**DEMO_RISK_STATE, "updated_at": now_iso},
    }))
    await r.setex("agent5:last_run", AGENT_LAST_RUN_TTL, json.dumps({
        "timestamp": now_iso,
        "status": "standby",
    }))
    logger.info("agent last-run heartbeats seeded (agents 1, 2, 3, 5)")


async def set_risk_freeze() -> None:
    """
    Step 5 — freeze flag: while this key exists, Agent 3 computes normally
    and logs its heartbeat but skips overwriting risk:state, so the seeded
    baseline (and any manually injected crisis values during the demo)
    survive. Auto-expires after 30 min; delete early to resume live risk:

        docker-compose exec redis redis-cli DEL demo:risk_freeze
    """
    r = await get_redis()
    await r.setex(RISK_FREEZE_KEY, RISK_FREEZE_TTL_SECONDS, "1")
    logger.info(
        "%s set (TTL %ds) — Agent 3 will not overwrite risk:state until it expires",
        RISK_FREEZE_KEY, RISK_FREEZE_TTL_SECONDS,
    )


async def verify() -> bool:
    """Read everything back and print it, so the pre-demo check is visual."""
    r = await get_redis()
    ok = True

    risk = await r.get("risk:state")
    print("\n--- VERIFICATION -------------------------------------------")
    print(f"risk:state          -> {risk}")
    if not risk or json.loads(risk).get("Hormuz") != 0.34:
        ok = False

    vessels = await r.get("vessels:live")
    n = len(json.loads(vessels)) if vessels else 0
    print(f"vessels:live        -> {n} vessels")
    ok = ok and n == 3

    for key in ("agent1:last_run", "agent2:last_run", "agent3:last_run", "agent5:last_run"):
        val = await r.get(key)
        print(f"{key:<19} -> {'OK' if val else 'MISSING'}")
        ok = ok and val is not None

    freeze = await r.get(RISK_FREEZE_KEY)
    print(f"demo:risk_freeze    -> {'SET' if freeze else 'MISSING'}")
    ok = ok and freeze is not None

    def _check_pg() -> int:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM audit_events WHERE event_id = %s",
                    (DEMO_AUDIT_EVENT_ID,),
                )
                return cur.fetchone()["n"]

    n_audit = await asyncio.to_thread(_check_pg)
    print(f"audit_events row    -> {'OK' if n_audit == 1 else f'FAIL ({n_audit} rows)'}")
    ok = ok and n_audit == 1
    print("-------------------------------------------------------------")
    return ok


async def main() -> int:
    logger.info("Seeding Section-12 pre-demo state...")
    await seed_risk_state()
    await seed_vessels()
    await asyncio.to_thread(seed_audit_event_sync)
    await seed_agent_heartbeats()
    await set_risk_freeze()

    if await verify():
        print("\nDEMO STATE READY — run this again before every demo/test.")
        return 0
    print("\nSEED INCOMPLETE — check the FAIL/MISSING lines above.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main())) 