# ============================================================
# ResiChain — Market Data + Vessel Position Client
# Polls yfinance for Brent/WTI prices every 5 minutes
# Polls AISHub for vessel positions every 5 minutes
# Writes all data to Redis with TTL
# Alpha Vantage daily historical series → PostgreSQL (via Person B's queries)
#
# Fix 13 (demo pre-cache): fetch_vessel_positions and fetch_live_prices
# now check vessels:demo_cache / prices:demo_cache FIRST. Those keys are
# written by scripts/pre_cache_demo_data.py 30 minutes before a demo and
# hold real data fetched the same way — so demo output is identical
# whether the external APIs are reachable or not. When the demo cache
# keys are absent (i.e. always, outside demo windows — they carry a 2h
# TTL), behavior is exactly what it was before this fix.
#
# Day 17 (failure-mode hardening): when yfinance fails/times out,
# fetch_live_prices no longer returns {} (which blanks the dashboard
# price cards). It now falls back to the last cached price — first from
# Redis prices:live, then from the Postgres price_history table
# (populated by the Alpha Vantage daily job). Dashboard shows the last
# known price instead of going blank.
# ============================================================

import json
import logging
import os
import asyncio
from datetime import datetime
from db.redis_client import get_redis
from db.postgres_queries import upsert_price_history, get_latest_price_history

logger = logging.getLogger(__name__)

# Fix 13 demo-cache keys (written by scripts/pre_cache_demo_data.py)
DEMO_VESSELS_CACHE_KEY = "vessels:demo_cache"
DEMO_PRICES_CACHE_KEY = "prices:demo_cache"

# Arabian Sea + Red Sea bounding boxes
# Format: (min_lat, max_lat, min_lon, max_lon)
VESSEL_REGIONS = {
    "Arabian_Sea": (10.0, 30.0, 50.0, 75.0),
    "Red_Sea":     (10.0, 30.0, 32.0, 50.0),
    "Gulf_of_Aden": (10.0, 15.0, 42.0, 55.0)
}


# ---- Vessel Position Polling ----------------------------
async def fetch_vessel_positions() -> list:
    """
    Fetches tanker positions and writes to Redis vessels:live (6-min TTL).

    Order of preference (Fix 13):
      1. vessels:demo_cache  (pre-fetched real data, demo windows only)
      2. AISHub live API     (requires AISHUB_USERNAME)
      3. hardcoded demo positions
    """
    r = await get_redis()

    # Fix 13: demo cache first — identical data shape, zero external calls.
    cached = await r.get(DEMO_VESSELS_CACHE_KEY)
    if cached:
        try:
            vessels = json.loads(cached)
            await r.setex("vessels:live", 360, cached)
            logger.info(
                "Vessels: served %d positions from %s (Fix 13 demo cache)",
                len(vessels), DEMO_VESSELS_CACHE_KEY,
            )
            return vessels
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Vessels: %s unreadable (%s) — falling through to live fetch",
                DEMO_VESSELS_CACHE_KEY, exc,
            )

    aishub_user = os.getenv("AISHUB_USERNAME", "")

    vessels = []

    if aishub_user and aishub_user != "placeholder":
        vessels = await _fetch_from_aishub(aishub_user)

    if not vessels:
        vessels = _get_demo_vessel_positions()
        logger.info("Vessels: Using demo positions (AISHub not configured)")
    else:
        logger.info(f"Vessels: Got {len(vessels)} real positions from AISHub")

    await r.setex(
        "vessels:live",
        360,  # 6 minutes
        json.dumps(vessels)
    )

    return vessels


async def _fetch_from_aishub(username: str) -> list:
    """Fetches real vessel data from AISHub API."""
    import aiohttp
    vessels = []

    try:
        url = (
            f"https://data.aishub.net/ws.php"
            f"?username={username}&format=1&output=json&compress=0"
            f"&mmsi=&imo=&shipname=&callsign="
            f"&latmin=10&latmax=30&lonmin=50&lonmax=75"
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

        for vessel in data:
            try:
                ship_type = int(vessel.get("SHIPTYPE", 0))
                if not (80 <= ship_type <= 89):
                    continue

                vessels.append({
                    "mmsi": str(vessel.get("MMSI", "")),
                    "name": vessel.get("NAME", "UNKNOWN"),
                    "lat": float(vessel.get("LATITUDE", 0)),
                    "lon": float(vessel.get("LONGITUDE", 0)),
                    "speed": float(vessel.get("SOG", 0)),
                    "heading": float(vessel.get("COG", 0)),
                    "destination": vessel.get("DESTINATION", ""),
                    "vessel_type": "crude_tanker",
                    "source": "aishub_live"
                })
            except Exception:
                continue

    except Exception as e:
        logger.error(f"AISHub fetch error: {e}")

    return vessels


def _get_demo_vessel_positions() -> list:
    """
    Returns hardcoded demo vessel positions.
    Used when AISHub credentials are not available.
    Positions are realistic for Arabian Sea tanker routes.

    current_port added (Day 19, found by Person B): Agent 7's
    get_vessels_near_port() matches a candidate's DEPARTURE port
    against current_port (then location, then destination). These
    only had `destination` (the Indian arrival port), so the check
    never found a match at any real Gulf departure port — TANKER_
    UNAVAILABLE fired for every supplier, every time. Kept identical
    to scripts/seed_demo_state.py's DEMO_VESSELS so both vessel-
    seeding paths give the same coverage regardless of which one
    happens to populate vessels:live.
    """
    return [
        {
            "mmsi": "477123456",
            "name": "GULF CARRIER",
            "lat": 24.5, "lon": 56.3,
            "speed": 12.4, "heading": 95,
            "current_port": "Ras Tanura",
            "destination": "SIKKA",
            "vessel_type": "crude_tanker",
            "source": "demo"
        },
        {
            "mmsi": "477234567",
            "name": "ARABIAN STAR",
            "lat": 22.1, "lon": 60.2,
            "speed": 11.8, "heading": 110,
            "current_port": "Fujairah",
            "destination": "VADINAR",
            "vessel_type": "crude_tanker",
            "source": "demo"
        },
        {
            "mmsi": "477345678",
            "name": "INDIA SPIRIT",
            "lat": 19.8, "lon": 63.4,
            "speed": 13.1, "heading": 120,
            "current_port": "Basra Oil Terminal",
            "destination": "PARADIP",
            "vessel_type": "crude_tanker",
            "source": "demo"
        },
        {
            "mmsi": "477456789",
            "name": "PERSIAN GLORY",
            "lat": 26.2, "lon": 57.1,
            "speed": 10.9, "heading": 88,
            "current_port": "Novorossiysk",
            "destination": "KOCHI",
            "vessel_type": "crude_tanker",
            "source": "demo"
        }
    ]


# ---- Price Polling --------------------------------------
async def fetch_live_prices() -> dict:
    """
    Fetches Brent and WTI prices and writes to Redis prices:live (6-min TTL).
    Called every 5 minutes by APScheduler.

    Fix 13: checks prices:demo_cache first (pre-fetched real yfinance data);
    falls through to a live yfinance call when the cache is absent.

    Day 17: if yfinance fails, falls back to the last cached price
    (Redis prices:live, then Postgres price_history) instead of returning
    an empty dict — so the dashboard shows the last known price rather
    than blank cards.
    """
    try:
        r = await get_redis()

        # Fix 13: demo cache first.
        cached = await r.get(DEMO_PRICES_CACHE_KEY)
        prices = {}
        from_cache = False
        if cached:
            try:
                prices = json.loads(cached)
                from_cache = True
                logger.info(
                    "Prices: served from %s (Fix 13 demo cache)",
                    DEMO_PRICES_CACHE_KEY,
                )
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning(
                    "Prices: %s unreadable (%s) — falling through to yfinance",
                    DEMO_PRICES_CACHE_KEY, exc,
                )
                prices = {}

        if not prices:
            # A raised exception here (timeout, network error) must be
            # treated the same as an empty return, so the cache fallback
            # below always runs instead of the raise escaping to the outer
            # except (which would blank the dashboard with {}).
            try:
                prices = await _fetch_prices_yfinance()
            except Exception as yf_exc:
                logger.warning(f"Price fetch: yfinance raised {yf_exc!r}")
                prices = {}

        if not prices:
            # Day 17 graceful degradation — yfinance is down. Do NOT return
            # {} (that blanks the dashboard). Serve the last known price.
            logger.warning("Price fetch: yfinance failed — trying cached fallbacks")

            # Fallback 1: last good value still in Redis prices:live.
            try:
                last_live = await r.get("prices:live")
                if last_live:
                    logger.info(
                        "Price fetch: served last cached prices:live (yfinance down)"
                    )
                    return json.loads(last_live)
            except Exception:
                pass

            # Fallback 2: last daily row from Postgres price_history
            # (populated by the Alpha Vantage daily job). This is the
            # "use Alpha Vantage cached value from PostgreSQL" path.
            try:
                row = get_latest_price_history()
                if row and row.get("brent_usd") is not None:
                    logger.info(
                        "Price fetch: served last price_history row from Postgres "
                        "(yfinance down)"
                    )
                    result = {
                        "brent": {
                            "price": float(row["brent_usd"]),
                            "change_pct": 0.0,
                            "commodity": "Brent Crude",
                            "unit": "USD/barrel",
                        },
                        "source": "postgres_cache",
                    }
                    if row.get("wti_usd") is not None:
                        result["wti"] = {
                            "price": float(row["wti_usd"]),
                            "change_pct": 0.0,
                            "commodity": "WTI Crude",
                            "unit": "USD/barrel",
                        }
                    return result
            except Exception as exc:
                logger.warning(
                    f"Price fetch: Postgres price_history fallback failed: {exc}"
                )

            # Nothing cached anywhere — empty as the true last resort.
            logger.warning("Price fetch: no cached price available anywhere")
            return {}

        await r.setex(
            "prices:live",
            360,
            json.dumps(prices)
        )

        if "brent" in prices:
            await r.setex(
                "brent:price:latest",
                3600,
                json.dumps({
                    "price": prices["brent"]["price"],
                    "change_pct": prices["brent"]["change_pct"],
                    "source": "demo_cache" if from_cache else "yfinance"
                })
            )

        logger.info(
            f"Prices updated: Brent=${prices.get('brent', {}).get('price', 'N/A')} "
            f"WTI=${prices.get('wti', {}).get('price', 'N/A')}"
        )
        return prices

    except Exception as e:
        logger.error(f"Price fetch error: {e}")
        return {}


async def _fetch_prices_yfinance() -> dict:
    """Fetches Brent and WTI prices using yfinance."""
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()

        def _download():
            result = {}

            brent = yf.Ticker("BZ=F")
            brent_hist = brent.history(period="5d")
            if not brent_hist.empty:
                closes = brent_hist["Close"].dropna()
                if len(closes) >= 2:
                    current = float(closes.iloc[-1])
                    previous = float(closes.iloc[-2])
                    change = ((current - previous) / previous) * 100
                    result["brent"] = {
                        "price": round(current, 2),
                        "previous": round(previous, 2),
                        "change_pct": round(change, 2),
                        "commodity": "Brent Crude",
                        "unit": "USD/barrel"
                    }

            wti = yf.Ticker("CL=F")
            wti_hist = wti.history(period="5d")
            if not wti_hist.empty:
                closes = wti_hist["Close"].dropna()
                if len(closes) >= 2:
                    current = float(closes.iloc[-1])
                    previous = float(closes.iloc[-2])
                    change = ((current - previous) / previous) * 100
                    result["wti"] = {
                        "price": round(current, 2),
                        "previous": round(previous, 2),
                        "change_pct": round(change, 2),
                        "commodity": "WTI Crude",
                        "unit": "USD/barrel"
                    }

            result["updated_at"] = datetime.utcnow().isoformat()
            return result

        return await loop.run_in_executor(None, _download)

    except Exception as e:
        logger.error(f"yfinance error: {e}")
        return {}


# ---- Alpha Vantage Daily Historical ---------------------
async def fetch_alpha_vantage_daily():
    """
    Fetches daily Brent price series from Alpha Vantage.
    Runs once daily — stays within 25 call/day free limit.
    Stores in PostgreSQL price_history via Person B's query layer.
    Used by Agent 6 for cost delta calculations.
    """
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    if not api_key or api_key == "placeholder":
        logger.info("Alpha Vantage: No API key — skipping daily fetch")
        return

    import aiohttp
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=BRENT&interval=daily&apikey={api_key}"
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()

        prices = data.get("data", [])
        if not prices:
            return

        inserted = 0
        for entry in prices[:90]:  # Last 90 days only
            try:
                upsert_price_history(
                    date=entry["date"],
                    brent_usd=float(entry["value"]),
                    wti_usd=None,
                    source="alphavantage"
                )
                inserted += 1
            except Exception:
                continue

        logger.info(f"Alpha Vantage: Stored {inserted} daily price records")

    except Exception as e:
        logger.error(f"Alpha Vantage error: {e}")
