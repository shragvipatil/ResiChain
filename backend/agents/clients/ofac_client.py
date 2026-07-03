# ============================================================
# ResiChain — OFAC SDN List Client
# Downloads daily sanctions list to PostgreSQL
# Agent 7 queries locally — never hits URL per check
# Scheduled: daily at 02:00 UTC
# ============================================================


import aiohttp
import xml.etree.ElementTree as ET
import logging
from db.postgres_queries import upsert_ofac_entry, check_ofac_match

logger = logging.getLogger(__name__)

OFAC_URL = "https://www.treasury.gov/ofac/downloads/sdnlist.xml"

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

        root = ET.fromstring(content)
        inserted = 0

        sdn_entries = root.findall(".//sdn:sdnEntry", NS)
        if not sdn_entries:
            sdn_entries = root.findall(".//sdnEntry")

        for entry in sdn_entries:
            try:
                def get_text(tag):
                    el = entry.find(f"sdn:{tag}", NS) or entry.find(tag)
                    return el.text.strip() if el is not None and el.text else ""

                first_name = get_text("firstName")
                last_name = get_text("lastName")
                full_name = f"{first_name} {last_name}".strip() or last_name or first_name

                programs = []
                prog_list = entry.find("sdn:programList", NS) or entry.find("programList")
                if prog_list:
                    for prog in prog_list:
                        prog_text = prog.text.strip() if prog.text else ""
                        if prog_text:
                            programs.append(prog_text)

                aliases = []
                aka_list = entry.find("sdn:akaList", NS) or entry.find("akaList")
                if aka_list:
                    for aka in aka_list:
                        aka_fn = aka.find("sdn:firstName", NS) or aka.find("firstName")
                        aka_ln = aka.find("sdn:lastName", NS) or aka.find("lastName")

                        alias_parts = []
                        if aka_fn is not None and aka_fn.text:
                            alias_parts.append(aka_fn.text.strip())
                        if aka_ln is not None and aka_ln.text:
                            alias_parts.append(aka_ln.text.strip())

                        alias_name = " ".join(alias_parts).strip()
                        if alias_name:
                            aliases.append(alias_name)

                upsert_ofac_entry(
                    entity_name=full_name,
                    aliases=", ".join(aliases) if aliases else None,
                    program=", ".join(programs) if programs else None,
                    date_imposed=None,
                )
                inserted += 1

            except Exception:
                continue

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