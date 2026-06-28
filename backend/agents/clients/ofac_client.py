# ============================================================
# ResiChain — OFAC SDN List Client
# Downloads daily sanctions list to PostgreSQL
# Agent 7 queries locally — never hits URL per check
# Scheduled: daily at 02:00 UTC
# ============================================================

import aiohttp
import xml.etree.ElementTree as ET
import logging
import json
from db.postgres import get_db_pool

logger = logging.getLogger(__name__)

OFAC_URL = "https://www.treasury.gov/ofac/downloads/sdnlist.xml"

# Namespaces in the OFAC XML
NS = {
    "sdn": "http://tempuri.org/sdnList.xsd"
}

async def download_and_store_ofac() -> dict:
    """
    Downloads OFAC SDN XML and stores all entries in PostgreSQL.
    Called once daily at 02:00 UTC by APScheduler.
    Agent 7 uses check_supplier_sanctions() for fast local lookups.
    """
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

        # Parse XML
        root = ET.fromstring(content)
        entries = []

        # Try with namespace first, then without
        sdn_entries = root.findall(".//sdn:sdnEntry", NS)
        if not sdn_entries:
            sdn_entries = root.findall(".//sdnEntry")

        for entry in sdn_entries:
            try:
                # Extract fields — handle both namespaced and plain
                def get_text(tag):
                    el = entry.find(f"sdn:{tag}", NS) or entry.find(tag)
                    return el.text.strip() if el is not None and el.text else ""

                uid = get_text("uid")
                first_name = get_text("firstName")
                last_name = get_text("lastName")
                sdn_type = get_text("sdnType")

                # Get programs (sanctions programs like IRAN, RUSSIA etc)
                programs = []
                prog_list = entry.find("sdn:programList", NS) or entry.find("programList")
                if prog_list:
                    for prog in prog_list:
                        prog_text = prog.text.strip() if prog.text else ""
                        if prog_text:
                            programs.append(prog_text)

                # Get aliases
                aliases = []
                aka_list = entry.find("sdn:akaList", NS) or entry.find("akaList")
                if aka_list:
                    for aka in aka_list:
                        aka_fn = aka.find("sdn:firstName", NS) or aka.find("firstName")
                        aka_ln = aka.find("sdn:lastName", NS) or aka.find("lastName")
                        if aka_ln is not None and aka_ln.text:
                            aliases.append(aka_ln.text.strip())

                entries.append({
                    "uid": uid,
                    "first_name": first_name,
                    "last_name": last_name,
                    "full_name": f"{first_name} {last_name}".strip(),
                    "sdn_type": sdn_type,
                    "programs": programs,
                    "aliases": aliases
                })

            except Exception as e:
                continue

        # Store in PostgreSQL
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Clear old entries
            await conn.execute("DELETE FROM ofac_sanctions")

            # Insert new entries in batches
            inserted = 0
            for entry in entries:
                await conn.execute("""
                    INSERT INTO ofac_sanctions
                    (uid, full_name, first_name, last_name, sdn_type, programs, aliases)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (uid) DO UPDATE
                    SET full_name = EXCLUDED.full_name,
                        programs = EXCLUDED.programs,
                        aliases = EXCLUDED.aliases,
                        updated_at = NOW()
                """,
                    entry["uid"],
                    entry["full_name"],
                    entry["first_name"],
                    entry["last_name"],
                    entry["sdn_type"],
                    json.dumps(entry["programs"]),
                    json.dumps(entry["aliases"])
                )
                inserted += 1

        logger.info(f"OFAC: Stored {inserted} SDN entries in PostgreSQL")
        return {"success": True, "entries_stored": inserted}

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
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Check exact name and aliases
            rows = await conn.fetch("""
                SELECT full_name, programs, aliases
                FROM ofac_sanctions
                WHERE LOWER(full_name) LIKE LOWER($1)
                   OR aliases::text ILIKE $2
            """,
                f"%{supplier_name}%",
                f"%{supplier_name}%"
            )

            if rows:
                programs = json.loads(rows[0]["programs"])
                return {
                    "sanctioned": True,
                    "programs": programs,
                    "matched_name": rows[0]["full_name"]
                }

            return {"sanctioned": False, "programs": [], "matched_name": None}

    except Exception as e:
        logger.error(f"OFAC check error: {e}")
        return {"sanctioned": False, "programs": [], "error": str(e)} 