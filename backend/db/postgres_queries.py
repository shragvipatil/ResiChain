import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

# Load environment variables (works for both Docker and local runs)
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in environment variables")


def _get_connection_kwargs() -> Dict[str, Any]:
    """
    Parse DATABASE_URL into kwargs for psycopg.connect.

    psycopg3 accepts a DSN string directly, so this is mostly here as a single
    place to tweak connection options if needed later.
    """
    return {"conninfo": DATABASE_URL}


@contextmanager
def get_connection():
    """
    Context manager that yields a psycopg connection using dict_row.

    Usage:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
    """
    conn = psycopg.connect(**_get_connection_kwargs(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE EXTENSION IF NOT EXISTS "pgcrypto";
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    event_id TEXT,
                    source TEXT,
                    corridor TEXT,
                    stage TEXT,
                    confidence DOUBLE PRECISION,
                    verified_at TIMESTAMPTZ,
                    archived_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS verified_events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    event_json JSONB NOT NULL,
                    corridor TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    confidence DOUBLE PRECISION NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS playbooks (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    signal_detected_at TIMESTAMPTZ NOT NULL,
                    playbook_generated_at TIMESTAMPTZ NOT NULL,
                    signal_to_playbook_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    ministry_view JSONB NOT NULL,
                    procurement_view JSONB NOT NULL,
                    refinery_view JSONB NOT NULL,
                    confidence DOUBLE PRECISION NOT NULL,
                    inputs JSONB NOT NULL
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS playbook_actions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    playbook_id UUID NOT NULL REFERENCES playbooks(id) ON DELETE CASCADE,
                    option_id TEXT NOT NULL,
                    analyst_decision TEXT NOT NULL,
                    analyst_note TEXT,
                    decided_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS procurement_evaluations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    playbook_id UUID NOT NULL REFERENCES playbooks(id) ON DELETE CASCADE,
                    option_id TEXT NOT NULL,
                    supplier TEXT NOT NULL,
                    grade TEXT,
                    status TEXT NOT NULL,
                    rule_triggered TEXT,
                    reason JSONB,
                    confidence DOUBLE PRECISION,
                    evaluated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS spr_schedules (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    playbook_id UUID NOT NULL REFERENCES playbooks(id) ON DELETE CASCADE,
                    feasible BOOLEAN NOT NULL,
                    daily_drawdown_schedule JSONB NOT NULL,
                    confidence DOUBLE PRECISION,
                    spr_remaining_mb DOUBLE PRECISION,
                    infeasibility_warning TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS ofac_sdn (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    entity_name TEXT NOT NULL,
                    aliases TEXT,
                    program TEXT,
                    date_imposed DATE,
                    last_refreshed_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    date DATE PRIMARY KEY,
                    brent_usd NUMERIC,
                    wti_usd NUMERIC,
                    source TEXT
                );
            """)

        conn.commit()
# ---------------------------------------------------------------------------
# Event & Audit Queries
# ---------------------------------------------------------------------------

def insert_audit_event(event: Dict[str, Any]) -> UUID:
    """
    Insert a row into audit_events.

    Expected keys in `event` (matches spec):
      - event_id (UUID or str)
      - source (str)
      - corridor (str)
      - stage (str: 'WATCH' or 'CONFIRMED')
      - confidence (float 0–1)
      - verified_at (datetime)
      - archived_at (datetime, optional)
    """
    sql = """
        INSERT INTO audit_events (
            event_id, source, corridor, stage,
            confidence, verified_at, archived_at
        )
        VALUES (
            %(event_id)s, %(source)s, %(corridor)s, %(stage)s,
            %(confidence)s, %(verified_at)s,
            COALESCE(%(archived_at)s, NOW())
        )
        RETURNING id
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, event)
            row = cur.fetchone()
            return row["id"]  # type: ignore[return-value]


def insert_verified_event(event_json: Dict[str, Any],
                          corridor: str,
                          stage: str,
                          confidence: float) -> UUID:
    """
    Insert into verified_events and return new id.
    """
    sql = """
        INSERT INTO verified_events (
            event_json, corridor, stage, confidence
        )
        VALUES (%(event_json)s, %(corridor)s, %(stage)s, %(confidence)s)
        RETURNING id
    """
    params = {
    "event_json": Jsonb(event_json),
    "corridor": corridor,
    "stage": stage,
    "confidence": confidence,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row["id"]  # type: ignore[return-value]


def get_verified_events(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Fetch recent verified events for API pagination.

    Ordered by created_at DESC, then id DESC.
    """
    sql = """
        SELECT
            id,
            event_json,
            corridor,
            stage,
            confidence,
            created_at
        FROM verified_events
        ORDER BY created_at DESC, id DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"limit": limit, "offset": offset})
            return cur.fetchall()


# ---------------------------------------------------------------------------
# OFAC Sanctions Queries (Agent 7 Layer 1)
# ---------------------------------------------------------------------------

def upsert_ofac_entry(entity_name: str,
                      aliases: Optional[str],
                      program: Optional[str],
                      date_imposed: Optional[str]) -> None:
    """
    Upsert a single OFAC SDN entry into ofac_sdn.

    Assumes entity_name is unique enough as a natural key for demo purposes.
    """
    sql = """
        INSERT INTO ofac_sdn (
            entity_name, aliases, program, date_imposed
        )
        VALUES (%(entity_name)s, %(aliases)s, %(program)s, %(date_imposed)s)
        ON CONFLICT (entity_name) DO UPDATE
        SET aliases = EXCLUDED.aliases,
            program = EXCLUDED.program,
            date_imposed = EXCLUDED.date_imposed,
            last_refreshed_at = NOW()
    """
    params = {
        "entity_name": entity_name,
        "aliases": aliases,
        "program": program,
        "date_imposed": date_imposed,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def check_ofac_match(supplier_name: str) -> bool:
    """
    Return True if supplier_name appears in ofac_sdn entity_name or aliases.

    Used by Agent 7 Layer 1 sanctions check.
    """
    sql = """
        SELECT 1
        FROM ofac_sdn
        WHERE entity_name ILIKE %(pattern)s
           OR aliases ILIKE %(pattern)s
        LIMIT 1
    """
    pattern = f"%{supplier_name}%"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"pattern": pattern})
            return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Playbooks & Actions (Agent 8)
# ---------------------------------------------------------------------------

def insert_playbook(signal_detected_at,
                    playbook_generated_at,
                    signal_to_playbook_seconds: int,
                    status: str,
                    ministry_view: Dict[str, Any],
                    procurement_view: Dict[str, Any],
                    refinery_view: Dict[str, Any],
                    confidence: float,
                    inputs: Dict[str, Any]) -> UUID:
    """
    Insert a playbook and return its UUID.

    All JSON views and inputs are stored as JSONB.
    """
    sql = """
        INSERT INTO playbooks (
            signal_detected_at,
            playbook_generated_at,
            signal_to_playbook_seconds,
            status,
            ministry_view,
            procurement_view,
            refinery_view,
            confidence,
            inputs
        )
        VALUES (
            %(signal_detected_at)s,
            %(playbook_generated_at)s,
            %(signal_to_playbook_seconds)s,
            %(status)s,
            %(ministry_view)s,
            %(procurement_view)s,
            %(refinery_view)s,
            %(confidence)s,
            %(inputs)s
        )
        RETURNING id
    """
    params = {
        "signal_detected_at": signal_detected_at,
        "playbook_generated_at": playbook_generated_at,
        "signal_to_playbook_seconds": signal_to_playbook_seconds,
        "status": status,
        "ministry_view": ministry_view,
        "procurement_view": procurement_view,
        "refinery_view": refinery_view,
        "confidence": confidence,
        "inputs": inputs,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row["id"]  # type: ignore[return-value]


def insert_playbook_action(playbook_id: UUID,
                           option_id: str,
                           analyst_decision: str,
                           analyst_note: Optional[str]) -> UUID:
    """
    Insert a single analyst decision into playbook_actions.
    """
    sql = """
        INSERT INTO playbook_actions (
            playbook_id,
            option_id,
            analyst_decision,
            analyst_note
        )
        VALUES (%(playbook_id)s, %(option_id)s, %(analyst_decision)s, %(analyst_note)s)
        RETURNING id
    """
    params = {
        "playbook_id": playbook_id,
        "option_id": option_id,
        "analyst_decision": analyst_decision,
        "analyst_note": analyst_note,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row["id"]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Procurement Evaluations (Agent 6 & 7)
# ---------------------------------------------------------------------------

def insert_procurement_evaluation(playbook_id: Optional[UUID],
                                  option_id: str,
                                  supplier: str,
                                  grade: str,
                                  status: str,
                                  rule_triggered: str,
                                  reason: Dict[str, Any],
                                  confidence: float) -> UUID:
    """
    Insert a row into procurement_evaluations.

    `reason` is stored as JSONB and should include structured explanation
    (rule, value, threshold, source, etc.) as per spec. [file:1]
    """
    sql = """
        INSERT INTO procurement_evaluations (
            playbook_id,
            option_id,
            supplier,
            grade,
            status,
            rule_triggered,
            reason,
            confidence
        )
        VALUES (
            %(playbook_id)s,
            %(option_id)s,
            %(supplier)s,
            %(grade)s,
            %(status)s,
            %(rule_triggered)s,
            %(reason)s,
            %(confidence)s
        )
        RETURNING id
    """
    params = {
        "playbook_id": playbook_id,
        "option_id": option_id,
        "supplier": supplier,
        "grade": grade,
        "status": status,
        "rule_triggered": rule_triggered,
        "reason": reason,
        "confidence": confidence,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row["id"]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# SPR Schedules (Agent 5)
# ---------------------------------------------------------------------------

def insert_spr_schedule(playbook_id: Optional[UUID],
                        feasible: bool,
                        daily_drawdown_schedule: List[float],
                        confidence: float,
                        spr_remaining_mb: Optional[float],
                        infeasibility_warning: Optional[str]) -> UUID:
    """
    Store Agent 5's SPR optimization output in spr_schedules.

    daily_drawdown_schedule is stored as JSONB array of numbers.
    """
    sql = """
        INSERT INTO spr_schedules (
            playbook_id,
            feasible,
            daily_drawdown_schedule,
            confidence,
            spr_remaining_mb,
            infeasibility_warning
        )
        VALUES (
            %(playbook_id)s,
            %(feasible)s,
            %(daily_drawdown_schedule)s,
            %(confidence)s,
            %(spr_remaining_mb)s,
            %(infeasibility_warning)s
        )
        RETURNING id
    """
    params = {
        "playbook_id": playbook_id,
        "feasible": feasible,
        "daily_drawdown_schedule": daily_drawdown_schedule,
        "confidence": confidence,
        "spr_remaining_mb": spr_remaining_mb,
        "infeasibility_warning": infeasibility_warning,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row["id"]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Price History (Fallback chain for prices) [file:1]
# ---------------------------------------------------------------------------

def upsert_price_history(date,
                         brent_usd: Optional[float],
                         wti_usd: Optional[float],
                         source: str) -> None:
    """
    Upsert a daily price row into price_history.

    `date` should be a date object or ISO string parsable by PostgreSQL.
    """
    sql = """
        INSERT INTO price_history (date, brent_usd, wti_usd, source)
        VALUES (%(date)s, %(brent_usd)s, %(wti_usd)s, %(source)s)
        ON CONFLICT (date) DO UPDATE
        SET brent_usd = EXCLUDED.brent_usd,
            wti_usd = EXCLUDED.wti_usd,
            source = EXCLUDED.source
    """
    params = {
        "date": date,
        "brent_usd": brent_usd,
        "wti_usd": wti_usd,
        "source": source,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def get_latest_price_history() -> Optional[Dict[str, Any]]:
    """
    Return the most recent price_history row, or None if table is empty.

    Used as the last step in the price fallback chain. [file:1]
    """
    sql = """
        SELECT date, brent_usd, wti_usd, source
        FROM price_history
        ORDER BY date DESC
        LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            return row if row is not None else None