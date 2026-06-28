# ============================================================
# ResiChain — PostgreSQL Client
# Handles connection + table creation on startup
# ============================================================

import asyncpg
import os
import logging

logger = logging.getLogger(__name__)

_pool = None  # Global connection pool

async def get_db_pool():
    """Returns the global asyncpg connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=os.getenv("DATABASE_URL"),
            min_size=2,
            max_size=10
        )
    return _pool

async def init_db():
    """
    Called on FastAPI startup.
    Creates all required tables if they don't exist.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # ---- Audit Log Table --------------------------------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ DEFAULT NOW(),
                event_id TEXT,
                agent TEXT,
                action TEXT,
                details JSONB,
                analyst_id INTEGER
            )
        """)

        # ---- Playbooks Table --------------------------------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS playbooks (
                id SERIAL PRIMARY KEY,
                playbook_id TEXT UNIQUE NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                event_summary TEXT,
                risk_level FLOAT,
                status TEXT DEFAULT 'pending',
                confidence FLOAT,
                cost_delta_bn FLOAT,
                supply_continuity_pct FLOAT,
                evidence_chain JSONB,
                actions JSONB,
                analyst_id INTEGER,
                analyst_note TEXT,
                signal_detected_at TIMESTAMPTZ,
                playbook_generated_at TIMESTAMPTZ
            )
        """)

        # ---- Users Table ------------------------------------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                hashed_password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'VIEWER',
                totp_secret TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                is_active BOOLEAN DEFAULT TRUE
            )
        """)

        # ---- Sessions / Token Blacklist ---------------------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS token_blacklist (
                jti TEXT PRIMARY KEY,
                revoked_at TIMESTAMPTZ DEFAULT NOW(),
                expires_at TIMESTAMPTZ
            )
        """)

        # ---- Alert History ----------------------------------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                alert_type TEXT,
                corridor TEXT,
                risk_score FLOAT,
                stage TEXT,
                message TEXT,
                acknowledged BOOLEAN DEFAULT FALSE,
                acknowledged_by INTEGER
            )
        """)

        # ---- Agent Run Log ----------------------------------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id SERIAL PRIMARY KEY,
                run_at TIMESTAMPTZ DEFAULT NOW(),
                agent_name TEXT,
                status TEXT,
                duration_ms INTEGER,
                input_summary TEXT,
                output_summary TEXT,
                confidence FLOAT,
                error_message TEXT
            )
        """)

        # ---- OFAC Sanctions Table ---------------------------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ofac_sanctions (
                uid TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                first_name TEXT,
                last_name TEXT,
                sdn_type TEXT,
                programs JSONB,
                aliases JSONB,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

    logger.info("All PostgreSQL tables created/verified") 