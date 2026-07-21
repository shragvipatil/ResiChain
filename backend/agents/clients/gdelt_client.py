# ============================================================
# ResiChain — GDELT 2.0 Ingestion Client
# Polls every 5 minutes for new geopolitical events
# Filters for oil-relevant event codes
# Deduplicates via Redis cache
# ============================================================

import aiohttp
import csv
import io
import json
import hashlib
import logging
import os
from datetime import datetime
from db.redis_client import get_redis, publish_event

logger = logging.getLogger(__name__)

# Event codes relevant to oil supply disruption
# 17 = Sanctions, 18 = Maritime, 19-20 = Conflict/War
RELEVANT_EVENT_CODES = {"17", "18", "19", "20", "193", "194", "195", "196"}

# Keywords that indicate oil supply relevance
RELEVANT_KEYWORDS = [
    "hormuz", "strait", "red sea", "persian gulf", "gulf of aden",
    "bab el mandeb", "suez", "iran", "saudi", "iraq", "houthi",
    "tanker", "crude", "oil", "petroleum", "shipping lane"
]

async def fetch_gdelt_events() -> list:
    """
    Main GDELT polling function.
    Called by APScheduler every 5 minutes.
    
    Flow:
    1. Fetch lastupdate.txt to get latest CSV filename
    2. Check Redis if we already processed this file
    3. If new — download CSV, filter relevant events
    4. Publish to Redis Stream events:raw
    """
    events_found = []

    try:
        async with aiohttp.ClientSession() as session:

            # Step 1 — Get latest file URL
            async with session.get(
                "http://data.gdeltproject.org/gdeltv2/lastupdate.txt",
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"GDELT lastupdate.txt returned {resp.status}")
                    return []
                text = await resp.text()

            # Parse the URL from lastupdate.txt
            # Format: size hash url (3 lines, we want the export CSV)
            csv_url = None
            for line in text.strip().split("\n"):
                parts = line.strip().split(" ")
                if len(parts) == 3 and "export.CSV" in parts[2]:
                    csv_url = parts[2]
                    break

            if not csv_url:
                logger.warning("Could not parse GDELT CSV URL")
                return []

            # Step 2 — Check Redis deduplication cache
            file_hash = hashlib.md5(csv_url.encode()).hexdigest()
            redis = await get_redis()
            cache_key = f"gdelt:processed:{file_hash}"

            already_processed = await redis.exists(cache_key)
            if already_processed:
                logger.debug(f"GDELT file already processed: {csv_url}")
                return []

            # Step 3 — Download and parse CSV
            logger.info(f"GDELT: Processing new file: {csv_url}")
            async with session.get(
                csv_url,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"GDELT CSV download failed: {resp.status}")
                    return []
                content = await resp.read()

            # GDELT files are tab-separated
            decoded = content.decode("utf-8", errors="ignore")
            reader = csv.reader(io.StringIO(decoded), delimiter="\t")

            for row in reader:
                try:
                    if len(row) < 58:
                        continue

                    event_code = row[26]
                    actor1 = row[6].lower() if row[6] else ""
                    actor2 = row[16].lower() if row[16] else ""
                    location = row[51].lower() if row[51] else ""
                    source_url = row[57] if len(row) > 57 else ""
                    tone = float(row[33]) if row[33] else 0.0
                    date_str = row[1]  # SQLDATE, format YYYYMMDD

                    try:
                        timestamp = datetime.strptime(date_str, "%Y%m%d").isoformat()
                    except (ValueError, TypeError):
                        # Malformed/missing SQLDATE — fall back to "now" rather
                        # than fail the whole row; this matches the previous
                        # (unintentional) behavior instead of dropping events.
                        timestamp = datetime.utcnow().isoformat()

                    # Filter by event code
                    if not any(event_code.startswith(c) for c in RELEVANT_EVENT_CODES):
                        continue

                    # Filter by keyword relevance
                    text_blob = f"{actor1} {actor2} {location}".lower()
                    if not any(kw in text_blob for kw in RELEVANT_KEYWORDS):
                        continue

                    # Determine which corridor this relates to
                    corridor = _detect_corridor(text_blob)
                    if not corridor:
                        continue

                    event = {
                        "source": "GDELT",
                        "event_code": event_code,
                        "date": date_str,
                        "timestamp": timestamp,
                        "actor1": row[6],
                        "actor2": row[16],
                        "location": row[51],
                        "corridor": corridor,
                        "tone": tone,
                        "severity": _tone_to_severity(tone),
                        "source_url": source_url,
                        "raw_confidence": 0.71
                    }

                    events_found.append(event)

                    # Publish to Redis Stream
                    await publish_event(event)

                except Exception as e:
                    continue

            # Step 4 — Mark file as processed in Redis (TTL = 1 hour)
            await redis.setex(cache_key, 3600, "processed")
            logger.info(f"GDELT: Found {len(events_found)} relevant events")

    except Exception as e:
        logger.error(f"GDELT client error: {e}")

    return events_found


def _detect_corridor(text: str) -> str:
    """Maps event text to a shipping corridor."""
    if any(kw in text for kw in ["hormuz", "persian gulf", "iran", "gulf of oman"]):
        return "Hormuz"
    if any(kw in text for kw in ["red sea", "houthi", "bab el mandeb", "aden"]):
        return "Red_Sea"
    if any(kw in text for kw in ["suez"]):
        return "Suez"
    if any(kw in text for kw in ["cape of good hope", "cape route", "southern africa"]):
        return "Cape"
    return None 


def _tone_to_severity(tone: float) -> int:
    """
    Converts GDELT tone score to 1-10 severity.
    GDELT tone is negative for bad news (range roughly -10 to +10)
    """
    if tone < -8:
        return 9
    elif tone < -6:
        return 7
    elif tone < -4:
        return 5
    elif tone < -2:
        return 3
    else:
        return 1 