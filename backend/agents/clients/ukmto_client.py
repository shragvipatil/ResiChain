# ============================================================
# ResiChain — UKMTO RSS Client
# Highest trust maritime security alerts
# Trust score: 0.99 (official naval authority)
# ============================================================

import feedparser
import logging
import hashlib
from datetime import datetime
from db.redis_client import get_redis, publish_event

logger = logging.getLogger(__name__)

UKMTO_RSS_URL = "https://www.ukmto.org/rss"
TRUST_SCORE = 0.99

CORRIDOR_KEYWORDS = {
    "Hormuz": ["hormuz", "persian gulf", "gulf of oman", "iran"],
    "Red_Sea": ["red sea", "houthi", "bab el mandeb", "aden", "hodeidah"],
    "Suez": ["suez", "mediterranean"],
    "Cape": ["cape of good hope", "cape route"]
}

async def fetch_ukmto_alerts() -> list:
    """
    Parses UKMTO RSS feed for maritime security advisories.
    Called by APScheduler every 5 minutes alongside GDELT.
    
    UKMTO is the most trusted source in the system.
    A UKMTO advisory alone can trigger CONFIRMED state.
    """
    events_found = []

    try:
        # feedparser handles the HTTP request synchronously
        # For async we run it in executor
        import asyncio
        loop = asyncio.get_event_loop()
        feed = await loop.run_in_executor(
            None,
            lambda: feedparser.parse(UKMTO_RSS_URL)
        )

        if not feed.entries:
            logger.info("UKMTO: No entries in feed")
            return []

        redis = await get_redis()

        for entry in feed.entries:
            title = entry.get("title", "").lower()
            summary = entry.get("summary", "").lower()
            published = entry.get("published", "")
            link = entry.get("link", "")

            # feedparser exposes a pre-parsed UTC struct_time whenever it can
            # recognize the feed's date format — much more robust than
            # parsing the raw RFC 822 "published" string ourselves.
            published_parsed = entry.get("published_parsed")
            if published_parsed:
                timestamp = datetime(*published_parsed[:6]).isoformat()
            else:
                timestamp = datetime.utcnow().isoformat()

            # Deduplication by entry ID
            entry_id = entry.get("id", link)
            cache_key = f"ukmto:processed:{hashlib.md5(entry_id.encode()).hexdigest()}"
            if await redis.exists(cache_key):
                continue

            # Detect corridor
            full_text = f"{title} {summary}"
            corridor = _detect_corridor(full_text)

            if not corridor:
                continue

            # Calculate severity from keywords
            severity = _calculate_severity(full_text)

            event = {
                "source": "UKMTO",
                "headline": entry.get("title", ""),
                "summary": entry.get("summary", "")[:500],
                "corridor": corridor,
                "published": published,
                "timestamp": timestamp,
                "link": link,
                "severity": severity,
                "raw_confidence": TRUST_SCORE
            }

            events_found.append(event)
            await publish_event(event)

            # Mark as processed — TTL 24 hours
            await redis.setex(cache_key, 86400, "processed")

        logger.info(f"UKMTO: Found {len(events_found)} new advisories")

    except Exception as e:
        logger.error(f"UKMTO client error: {e}")

    return events_found


def _detect_corridor(text: str) -> str:
    text = text.lower()
    for corridor, keywords in CORRIDOR_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return corridor
    return None


def _calculate_severity(text: str) -> int:
    text = text.lower()
    if any(w in text for w in ["attack", "missile", "drone strike", "explosion"]):
        return 9
    if any(w in text for w in ["warning", "threat", "hostile", "suspicious"]):
        return 6
    if any(w in text for w in ["advisory", "caution", "notice"]):
        return 3
    return 2 