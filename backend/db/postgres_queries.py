from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from uuid import UUID

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in environment variables")


def _get_connection_kwargs() -> Dict[str, Any]:
    return {"conninfo": DATABASE_URL}


@contextmanager
def get_connection():
    conn = psycopg.connect(**_get_connection_kwargs(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""CREATE EXTENSION IF NOT EXISTS "pgcrypto";""")

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
                    playbook_id UUID NULL REFERENCES playbooks(id) ON DELETE CASCADE,
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
                    playbook_id UUID NULL REFERENCES playbooks(id) ON DELETE CASCADE,
                    feasible BOOLEAN NOT NULL,
                    daily_drawdown_schedule JSONB NOT NULL,
                    confidence DOUBLE PRECISION,
                    spr_remaining_mb DOUBLE PRECISION,
                    infeasibility_warning TEXT,
                    inputs_used JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS ofac_sdn (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    entity_name TEXT NOT NULL UNIQUE,
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

            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    node_name TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    ended_at TIMESTAMPTZ NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            cur.execute("""
                ALTER TABLE procurement_evaluations
                ALTER COLUMN playbook_id DROP NOT NULL;
            """)

            cur.execute("""
                ALTER TABLE spr_schedules
                ALTER COLUMN playbook_id DROP NOT NULL;
            """)


def insert_audit_event(event: Dict[str, Any]) -> UUID:
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
            return row["id"]


def insert_verified_event(
    event_json: Dict[str, Any],
    corridor: str,
    stage: str,
    confidence: float,
) -> UUID:
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
            return row["id"]


def get_verified_events(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
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


def upsert_ofac_entry(
    entity_name: str,
    aliases: Optional[str],
    program: Optional[str],
    date_imposed: Optional[str],
) -> None:
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
    Returns True only if supplier_name appears as a distinct whole-word
    token inside entity_name or aliases -- not as a raw substring hit.

    BUG FIX (Item 4 / Russia false-positive):
    Previous version used ILIKE '%supplier_name%', which matches ANY
    SDN row that merely CONTAINS the country name anywhere in its text
    (e.g. an address field like "..., Moscow, Russia" or a descriptive
    alias). That caused country names like "Russia" to match entities
    that have nothing to do with an actual country-level sanction,
    while OFAC only lists specific individuals/entities/vessels, never
    a bare country as an SDN entity.

    Using a word-boundary anchored regex (~*) ensures "Russia" only
    matches when it is a standalone token, not embedded substring text.
    This does not change behavior for genuinely sanctioned entities
    whose full name IS the match target (e.g. "Islamic Republic of
    Iran" is itself a listed SDN entity, so word-boundary matching
    still correctly flags it).
    """
    if not supplier_name or not supplier_name.strip():
        return False

    escaped = re.escape(supplier_name.strip())
    pattern = rf"\y{escaped}\y"

    sql = """
        SELECT 1
        FROM ofac_sdn
        WHERE entity_name ~* %(pattern)s
           OR aliases ~* %(pattern)s
        LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"pattern": pattern})
            return cur.fetchone() is not None

# db/postgres_queries.py — add alongside check_ofac_match

COMPREHENSIVE_SANCTIONS_COUNTRIES = {
    "iran", "islamic republic of iran",
    "north korea", "democratic people's republic of korea", "dprk",
    "cuba",
    "syria", "syrian arab republic",
}

def is_comprehensively_sanctioned_country(country_name: str) -> bool:
    """
    True only for countries under blanket US comprehensive sanctions
    programs (embargoes) — distinct from check_ofac_match, which
    matches specific SDN entities/individuals/banks.

    This does NOT check the SDN entity table, because entity name
    matching incorrectly flags countries like Russia (which has
    sanctioned entities like Central Bank of Russia, but is not
    itself comprehensively embargoed for crude oil imports).
    """
    return country_name.strip().lower() in COMPREHENSIVE_SANCTIONS_COUNTRIES


def insert_playbook(
    signal_detected_at,
    playbook_generated_at,
    signal_to_playbook_seconds: int,
    status: str,
    ministry_view: Dict[str, Any],
    procurement_view: Dict[str, Any],
    refinery_view: Dict[str, Any],
    confidence: float,
    inputs: Dict[str, Any],
) -> UUID:
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
        "ministry_view": Jsonb(ministry_view),
        "procurement_view": Jsonb(procurement_view),
        "refinery_view": Jsonb(refinery_view),
        "confidence": confidence,
        "inputs": Jsonb(inputs),
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row["id"]


def get_playbook_by_id(playbook_id: UUID) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT
            id,
            signal_detected_at,
            playbook_generated_at,
            signal_to_playbook_seconds,
            status,
            ministry_view,
            procurement_view,
            refinery_view,
            confidence,
            inputs
        FROM playbooks
        WHERE id = %(playbook_id)s
        LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"playbook_id": playbook_id})
            row = cur.fetchone()
            return row if row is not None else None


def insert_playbook_action(
    playbook_id: UUID,
    option_id: str,
    analyst_decision: str,
    analyst_note: Optional[str],
) -> UUID:
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
            return row["id"]


def insert_procurement_evaluation(
    playbook_id: Optional[UUID],
    option_id: str,
    supplier: str,
    grade: Optional[str],
    status: str,
    rule_triggered: Optional[str],
    reason: Optional[Dict[str, Any]],
    confidence: Optional[float],
) -> UUID:
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
        "reason": Jsonb(reason or {}),
        "confidence": confidence,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row["id"]


def get_procurement_evaluations(
    playbook_id: Optional[UUID] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    base_sql = """
        SELECT
            id,
            playbook_id,
            option_id,
            supplier,
            grade,
            status,
            rule_triggered,
            reason,
            confidence,
            evaluated_at
        FROM procurement_evaluations
    """
    params: Dict[str, Any] = {"limit": limit}

    if playbook_id is not None:
        sql = base_sql + """
            WHERE playbook_id = %(playbook_id)s
            ORDER BY evaluated_at DESC, id DESC
            LIMIT %(limit)s
        """
        params["playbook_id"] = playbook_id
    else:
        sql = base_sql + """
            ORDER BY evaluated_at DESC, id DESC
            LIMIT %(limit)s
        """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def insert_spr_schedule(
    playbook_id: Optional[UUID],
    feasible: bool,
    daily_drawdown_schedule: List[float],
    confidence: float,
    spr_remaining_mb: Optional[float],
    infeasibility_warning: Optional[str],
    inputs_used: Optional[Dict[str, Any]] = None,
) -> UUID:
    sql = """
        INSERT INTO spr_schedules (
            playbook_id,
            feasible,
            daily_drawdown_schedule,
            confidence,
            spr_remaining_mb,
            infeasibility_warning,
            inputs_used
        )
        VALUES (
            %(playbook_id)s,
            %(feasible)s,
            %(daily_drawdown_schedule)s,
            %(confidence)s,
            %(spr_remaining_mb)s,
            %(infeasibility_warning)s,
            %(inputs_used)s
        )
        RETURNING id
    """
    params = {
        "playbook_id": playbook_id,
        "feasible": feasible,
        "daily_drawdown_schedule": Jsonb(daily_drawdown_schedule),
        "confidence": confidence,
        "spr_remaining_mb": spr_remaining_mb,
        "infeasibility_warning": infeasibility_warning,
        "inputs_used": Jsonb(inputs_used or {}),
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row["id"]


def get_latest_spr_schedule() -> Optional[Dict[str, Any]]:
    sql = """
        SELECT
            id,
            playbook_id,
            feasible,
            daily_drawdown_schedule,
            confidence,
            spr_remaining_mb,
            infeasibility_warning,
            inputs_used,
            created_at
        FROM spr_schedules
        ORDER BY created_at DESC, id DESC
        LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            return row if row is not None else None


def insert_agent_run(
    node_name: str,
    started_at,
    ended_at,
    duration_ms: int,
) -> UUID:
    sql = """
        INSERT INTO agent_runs (
            node_name,
            started_at,
            ended_at,
            duration_ms
        )
        VALUES (
            %(node_name)s,
            %(started_at)s,
            %(ended_at)s,
            %(duration_ms)s
        )
        RETURNING id
    """
    params = {
        "node_name": node_name,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row["id"]


def upsert_price_history(
    date,
    brent_usd: Optional[float],
    wti_usd: Optional[float],
    source: str,
) -> None:
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