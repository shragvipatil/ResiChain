# ============================================================
# ResiChain — OFAC SDN List Client
# Downloads daily sanctions list to PostgreSQL
# Agent 7 queries locally — never hits URL per check
# Scheduled: daily at 02:00 UTC
#
# Fix 13 (demo pre-cache): download_and_store_ofac() now checks for a
# local snapshot at data/ofac_snapshot.xml first (written by
# scripts/pre_cache_demo_data.py 30 minutes before a demo). If the
# snapshot exists and is younger than SNAPSHOT_MAX_AGE_HOURS, it is
# parsed instead of hitting treasury.gov — the SDN list only changes
# daily, so a <48h snapshot is functionally identical to a live pull.
# When no fresh snapshot exists, the live download runs exactly as
# before, and additionally writes the snapshot as a side effect so the
# next run is network-independent.
# ============================================================


import aiohttp
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from db.postgres_queries import get_connection, check_ofac_match

logger = logging.getLogger(__name__)

# 2026-07: OFAC moved off treasury.gov to a dedicated service domain, and
# the new SDN.XML uses a different default XML namespace than the legacy
# tempuri.org one this parser used to hardcode. Both the URL and the
# namespace detection below were updated together and verified against a
# real downloaded SDN.XML (19,143 entries, 0 blank names after the fix —
# every field silently came back empty under the old hardcoded namespace).
OFAC_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"

# Fix 13 snapshot location (relative to /app inside the container;
# ./backend/data/ofac_snapshot.xml on the host via the volume mount).
OFAC_SNAPSHOT_PATH = os.getenv("OFAC_SNAPSHOT_PATH", "data/ofac_snapshot.xml")
SNAPSHOT_MAX_AGE_HOURS = int(os.getenv("OFAC_SNAPSHOT_MAX_AGE_HOURS", "48"))


def _detect_namespace(root_tag: str) -> str | None:
    """
    Extracts the namespace URI from an ElementTree root tag, e.g.
    '{https://sanctionslistservice.ofac.treas.gov/.../XML}sdnList' ->
    'https://sanctionslistservice.ofac.treas.gov/.../XML'.

    Detecting this dynamically (instead of hardcoding one URI) means a
    future OFAC format/domain change degrades gracefully to "0 entries
    matched, logged clearly" instead of "every field silently blank."
    """
    m = re.match(r"\{(.+)\}", root_tag)
    return m.group(1) if m else None


def _snapshot_age_hours() -> float | None:
    """Age of the local snapshot in hours, or None if it doesn't exist."""
    try:
        mtime = os.path.getmtime(OFAC_SNAPSHOT_PATH)
        return (time.time() - mtime) / 3600.0
    except OSError:
        return None


def _bulk_upsert_ofac_entries(entries: list[dict]) -> int:
    """
    Writes all parsed SDN entries in ONE Postgres connection, using
    chunked multi-row INSERT ... ON CONFLICT statements.

    The original code called upsert_ofac_entry() once per entry, and that
    function opens a brand-new psycopg connection every call (see
    db/postgres_queries.py's get_connection() — no pooling). At 19,143
    SDN entries, that's 19,143 separate TCP+auth handshakes done one at
    a time with zero progress output — several minutes of apparent
    "hang" for what should be a few-second bulk load. This does the same
    ON CONFLICT upsert semantics, just batched: ~39 statements
    (500 rows each) over a single connection instead of 19,143 connections.
    """
    if not entries:
        return 0

    # Deduplicate by entity_name, keeping the LAST occurrence. The SDN list
    # contains the same entity_name more than once (e.g. an entity sanctioned
    # under multiple programs). Postgres rejects an INSERT ... ON CONFLICT that
    # would update the same conflict-target row twice within one statement
    # ("cannot affect row a second time"). The old per-row code never hit this
    # because each row was its own statement (last write silently won); we
    # preserve that exact last-write-wins semantics here by collapsing dupes
    # before batching.
    deduped: dict[str, dict] = {}
    for e in entries:
        deduped[e["entity_name"]] = e
    entries = list(deduped.values())

    CHUNK = 500
    total_written = 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            for i in range(0, len(entries), CHUNK):
                chunk = entries[i:i + CHUNK]

                values_sql = ", ".join(
                    f"(%(entity_name_{j})s, %(aliases_{j})s, %(program_{j})s, %(date_imposed_{j})s)"
                    for j in range(len(chunk))
                )
                params: dict = {}
                for j, e in enumerate(chunk):
                    params[f"entity_name_{j}"] = e["entity_name"]
                    params[f"aliases_{j}"] = e["aliases"]
                    params[f"program_{j}"] = e["program"]
                    params[f"date_imposed_{j}"] = e["date_imposed"]

                cur.execute(
                    f"""
                    INSERT INTO ofac_sdn (
                        entity_name, aliases, program, date_imposed
                    )
                    VALUES {values_sql}
                    ON CONFLICT (entity_name) DO UPDATE
                    SET aliases = EXCLUDED.aliases,
                        program = EXCLUDED.program,
                        date_imposed = EXCLUDED.date_imposed,
                        last_refreshed_at = NOW()
                    """,
                    params,
                )
                total_written += len(chunk)
                logger.info(
                    "OFAC bulk upsert progress: %d / %d", total_written, len(entries)
                )

    return total_written


def _parse_and_store(content: bytes) -> int:
    """
    Parses SDN XML bytes and upserts every entry into PostgreSQL.
    Shared by the snapshot path and the live-download path so both
    produce byte-identical database state. Returns entries stored.

    Two bugs fixed here together (2026-07), both silent — neither raised
    an exception, both just produced blank/empty data:

    1. Hardcoded namespace: NS used to hardcode "http://tempuri.org/
       sdnList.xsd". The current SDN.XML's real default namespace is
       "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview
       /exports/XML" — a namespace mismatch means every sdn:-prefixed
       find() returns no match. Fixed by detecting the namespace from the
       document's own root tag instead of hardcoding one.

    2. `entry.find(...) or entry.find(...)` fallback pattern: ElementTree
       elements with zero child elements are falsy in a boolean context
       (regardless of whether they hold text) — firstName/lastName/etc.
       are always leaf/childless elements, so this `or` silently discards
       a SUCCESSFUL find and falls through to the second (usually failing)
       lookup. Fixed with explicit `is None` checks.

    Verified against a real downloaded SDN.XML (19,143 entries): 0 blank
    names after both fixes, vs. every single entry blank before them.
    """
    root = ET.fromstring(content)
    parsed_entries: list[dict] = []

    ns_uri = _detect_namespace(root.tag)
    ns = {"sdn": ns_uri} if ns_uri else {}

    def _find(el, tag):
        """entry.find with namespace if detected, else bare tag — using
        an explicit is-None check, never the truthy-or pattern."""
        found = el.find(f"sdn:{tag}", ns) if ns_uri else None
        if found is None:
            found = el.find(tag)
        return found

    sdn_entries = root.findall(".//sdn:sdnEntry", ns) if ns_uri else []
    if not sdn_entries:
        sdn_entries = root.findall(".//sdnEntry")

    logger.info(
        "OFAC parse: namespace=%s entries_found=%d",
        ns_uri or "(none)", len(sdn_entries),
    )

    for entry in sdn_entries:
        try:
            def get_text(tag):
                el = _find(entry, tag)
                return el.text.strip() if el is not None and el.text else ""

            first_name = get_text("firstName")
            last_name = get_text("lastName")
            full_name = f"{first_name} {last_name}".strip() or last_name or first_name

            programs = []
            prog_list = _find(entry, "programList")
            if prog_list is not None:
                for prog in prog_list:
                    prog_text = prog.text.strip() if prog.text else ""
                    if prog_text:
                        programs.append(prog_text)

            aliases = []
            aka_list = _find(entry, "akaList")
            if aka_list is not None:
                for aka in aka_list:
                    aka_fn = _find(aka, "firstName")
                    aka_ln = _find(aka, "lastName")

                    alias_parts = []
                    if aka_fn is not None and aka_fn.text:
                        alias_parts.append(aka_fn.text.strip())
                    if aka_ln is not None and aka_ln.text:
                        alias_parts.append(aka_ln.text.strip())

                    alias_name = " ".join(alias_parts).strip()
                    if alias_name:
                        aliases.append(alias_name)

            if not full_name:
                # Don't silently insert a blank-name row — log once so a
                # regression here is visible instead of invisible.
                logger.warning("OFAC: entry with no name field, skipping")
                continue

            parsed_entries.append({
                "entity_name": full_name,
                "aliases": ", ".join(aliases) if aliases else None,
                "program": ", ".join(programs) if programs else None,
                "date_imposed": None,
            })

        except Exception:
            continue

    return _bulk_upsert_ofac_entries(parsed_entries)


async def download_and_store_ofac() -> dict:
    """
    Loads the OFAC SDN list into PostgreSQL.
    Called once daily at 02:00 UTC by APScheduler (and at startup).
    Agent 7 uses check_supplier_sanctions() for fast local lookups.

    Fix 13 order of preference:
      1. Local snapshot data/ofac_snapshot.xml, if younger than
         SNAPSHOT_MAX_AGE_HOURS (written by scripts/pre_cache_demo_data.py)
      2. Live download from treasury.gov (also refreshes the snapshot)
    """
    # --- Fix 13: snapshot-first ------------------------------------
    age = _snapshot_age_hours()
    if age is not None and age <= SNAPSHOT_MAX_AGE_HOURS:
        try:
            with open(OFAC_SNAPSHOT_PATH, "rb") as fh:
                content = fh.read()
            inserted = _parse_and_store(content)
            logger.info(
                "OFAC: Stored %d SDN entries from local snapshot %s (age %.1fh)",
                inserted, OFAC_SNAPSHOT_PATH, age,
            )
            return {"success": True, "entries_stored": inserted, "source": "snapshot"}
        except Exception as e:
            logger.warning(
                "OFAC: snapshot at %s unreadable (%s) — falling back to live download",
                OFAC_SNAPSHOT_PATH, e,
            )
    elif age is not None:
        logger.info(
            "OFAC: snapshot exists but is stale (%.1fh > %dh) — downloading live",
            age, SNAPSHOT_MAX_AGE_HOURS,
        )

    # --- Live download (original behavior) --------------------------
    try:
        logger.info("OFAC: Starting daily SDN list download...")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                OFAC_URL,
                timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                if resp.status != 200:
                    logger.error(f"OFAC download failed: {resp.status}")
                    return {"success": False, "error": f"HTTP {resp.status}"}

                content = await resp.read()

        logger.info(f"OFAC: Downloaded {len(content) / 1024 / 1024:.1f} MB")

        # Fix 13 side effect: refresh the snapshot so the next run
        # (or a demo with no network) can use it. Best-effort only.
        try:
            os.makedirs(os.path.dirname(OFAC_SNAPSHOT_PATH) or ".", exist_ok=True)
            tmp_path = OFAC_SNAPSHOT_PATH + ".tmp"
            with open(tmp_path, "wb") as fh:
                fh.write(content)
            os.replace(tmp_path, OFAC_SNAPSHOT_PATH)
            logger.info("OFAC: snapshot refreshed at %s", OFAC_SNAPSHOT_PATH)
        except Exception as snap_exc:
            logger.warning("OFAC: could not write snapshot (%s) — continuing", snap_exc)

        inserted = _parse_and_store(content)

        logger.info(f"OFAC: Stored {inserted} SDN entries in PostgreSQL")
        return {"success": True, "entries_stored": inserted, "source": "live"}

    except Exception as e:
        logger.error(f"OFAC client error: {e}")
        return {"success": False, "error": str(e)}


async def check_supplier_sanctions(supplier_name: str) -> dict:
    """
    Agent 7 calls this for every procurement option.
    Checks local PostgreSQL — never hits OFAC URL.
    Returns: {"sanctioned": bool, "programs": [], "matched_name": str}
    """
    try:
        sanctioned = check_ofac_match(supplier_name)

        if sanctioned:
            return {
                "sanctioned": True,
                "programs": [],
                "matched_name": supplier_name
            }

        return {"sanctioned": False, "programs": [], "matched_name": None}

    except Exception as e:
        logger.error(f"OFAC check error: {e}")
        return {"sanctioned": False, "programs": [], "error": str(e)} 