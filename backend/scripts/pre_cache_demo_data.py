"""
backend/scripts/pre_cache_demo_data.py

Fix 13 — demo pre-cache. Run 30 MINUTES BEFORE any demo:

    docker-compose exec fastapi python scripts/pre_cache_demo_data.py

Fetches everything the live pipeline would normally pull from external
APIs and caches it locally, so a venue with bad Wi-Fi / a rate-limited
API / a down upstream cannot break the demo:

  1. AISHub vessel positions -> Redis  vessels:demo_cache   (2h TTL)
  2. yfinance Brent/WTI      -> Redis  prices:demo_cache    (2h TTL)
  3. OFAC SDN XML            -> file   data/ofac_snapshot.xml

The main application (agents/clients/market_client.py and
agents/clients/ofac_client.py) checks these demo caches FIRST and only
falls through to the live external APIs when a cache is absent. Because
the cached data is real data fetched the same way, the demo is identical
whether it runs live or cached — no one can tell the difference.

Each of the three steps is independent: one failing does not stop the
others. Exit code is 0 only if all three succeeded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# See seed_demo_state.py for why this is needed: no PYTHONPATH=/app in the
# Dockerfile, and `python scripts/pre_cache_demo_data.py` only puts
# /app/scripts on sys.path by default, not /app.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp

from db.redis_client import get_redis
from agents.clients.market_client import (
    _fetch_from_aishub,
    _get_demo_vessel_positions,
    _fetch_prices_yfinance,
)
from agents.clients.ofac_client import (
    download_and_store_ofac,
    OFAC_URL,
    OFAC_SNAPSHOT_PATH,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("pre_cache_demo_data")

DEMO_VESSELS_CACHE_KEY = "vessels:demo_cache"
DEMO_PRICES_CACHE_KEY = "prices:demo_cache"
DEMO_CACHE_TTL_SECONDS = 7200  # 2h — covers "30 min before" with wide margin

# OFAC_URL and OFAC_SNAPSHOT_PATH are imported from ofac_client above —
# they were previously duplicated here as local constants, which meant
# fixing the URL in ofac_client.py didn't fix this script (it kept
# 404ing on its own stale copy). One source of truth now.


async def cache_vessels() -> bool:
    """Step 1 — fresh AISHub positions (or realistic demo fallback)."""
    try:
        aishub_user = os.getenv("AISHUB_USERNAME", "")
        vessels = []
        if aishub_user and aishub_user != "placeholder":
            vessels = await _fetch_from_aishub(aishub_user)

        source = "aishub_live"
        if not vessels:
            vessels = _get_demo_vessel_positions()
            source = "demo_fallback"

        r = await get_redis()
        await r.setex(DEMO_VESSELS_CACHE_KEY, DEMO_CACHE_TTL_SECONDS, json.dumps(vessels))
        logger.info(
            "vessels:demo_cache written: %d vessels (source=%s, TTL=%ds)",
            len(vessels), source, DEMO_CACHE_TTL_SECONDS,
        )
        return True
    except Exception as exc:
        logger.error("Vessel pre-cache FAILED: %s", exc)
        return False


async def cache_prices() -> bool:
    """Step 2 — current yfinance Brent/WTI, same dict shape as prices:live."""
    try:
        prices = await _fetch_prices_yfinance()
        if not prices or "brent" not in prices:
            logger.error("Price pre-cache FAILED: yfinance returned no Brent data")
            return False

        prices["cached_at"] = datetime.utcnow().isoformat()
        r = await get_redis()
        await r.setex(DEMO_PRICES_CACHE_KEY, DEMO_CACHE_TTL_SECONDS, json.dumps(prices))
        logger.info(
            "prices:demo_cache written: Brent=$%s WTI=$%s (TTL=%ds)",
            prices.get("brent", {}).get("price", "N/A"),
            prices.get("wti", {}).get("price", "N/A"),
            DEMO_CACHE_TTL_SECONDS,
        )
        return True
    except Exception as exc:
        logger.error("Price pre-cache FAILED: %s", exc)
        return False


async def snapshot_ofac() -> bool:
    """
    Step 3 — download the latest OFAC SDN XML to data/ofac_snapshot.xml,
    THEN load it into Postgres.

    The second half matters: main.py only schedules download_and_store_ofac()
    at 02:00 UTC daily, never at startup. After a fresh `docker-compose down -v`
    (or any clean boot), the ofac_entries table is empty and Agent 7's
    check_supplier_sanctions() would silently return "not sanctioned" for
    everyone until 02:00 UTC naturally arrives. Calling the real loader here
    closes that gap — and because we just wrote a fresh snapshot, it uses
    that snapshot instantly instead of downloading a second time.
    """
    try:
        os.makedirs(os.path.dirname(OFAC_SNAPSHOT_PATH) or ".", exist_ok=True)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                OFAC_URL, timeout=aiohttp.ClientTimeout(total=180)
            ) as resp:
                if resp.status != 200:
                    logger.error("OFAC snapshot FAILED: HTTP %d", resp.status)
                    return False
                content = await resp.read()

        # Sanity check before overwriting a possibly-good older snapshot:
        # the real SDN list is tens of MB; a tiny body means an error page.
        if len(content) < 1_000_000:
            logger.error(
                "OFAC snapshot FAILED: body only %.1f KB — refusing to overwrite",
                len(content) / 1024,
            )
            return False

        tmp_path = OFAC_SNAPSHOT_PATH + ".tmp"
        with open(tmp_path, "wb") as fh:
            fh.write(content)
        os.replace(tmp_path, OFAC_SNAPSHOT_PATH)  # atomic swap

        logger.info(
            "OFAC snapshot saved: %s (%.1f MB)",
            OFAC_SNAPSHOT_PATH, len(content) / 1024 / 1024,
        )

        # Load it into Postgres now — don't wait for 02:00 UTC.
        result = await download_and_store_ofac()
        if not result.get("success"):
            logger.error("OFAC Postgres load FAILED: %s", result.get("error"))
            return False

        logger.info(
            "OFAC Postgres load OK: %d entries (source=%s)",
            result.get("entries_stored", 0), result.get("source", "?"),
        )
        return True
    except Exception as exc:
        logger.error("OFAC snapshot/load FAILED: %s", exc)
        return False


async def main() -> int:
    logger.info("Pre-caching demo data (Fix 13)...")
    v_ok = await cache_vessels()
    p_ok = await cache_prices()
    o_ok = await snapshot_ofac()

    print("\n--- PRE-CACHE SUMMARY ---------------------------------------")
    print(f"vessels:demo_cache        {'OK' if v_ok else 'FAILED'}")
    print(f"prices:demo_cache         {'OK' if p_ok else 'FAILED'}")
    print(f"OFAC snapshot + Postgres  {'OK' if o_ok else 'FAILED'}")
    print("--------------------------------------------------------------")
    if v_ok and p_ok and o_ok:
        print("ALL CACHES READY — demo is now immune to external API failures.")
        return 0
    print("At least one cache FAILED — demo will fall back to live APIs for it.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main())) 