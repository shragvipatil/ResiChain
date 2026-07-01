# ============================================================
# ResiChain — Market Data + Vessel Position Client
# Polls yfinance for Brent/WTI prices every 5 minutes
# Polls AISHub for vessel positions every 5 minutes
# Writes all data to Redis with TTL
# Alpha Vantage daily historical series → PostgreSQL (via Person B's queries)
# ============================================================

import json
import logging
import os
import asyncio
from datetime import datetime
from db.redis_client import get_redis
from db.postgres_queries import upsert_price_history

logger = logging.getLogger(__name__)

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
    Fetches tanker positions from AISHub API.
    Filters for VLCC and Suezmax in key regions.
    Writes to Redis vessels:live with 6-minute TTL.

    AISHub requires username — falls back to hardcoded
    demo positions if credentials not available.
    """
    aishub_user = os.getenv("AISHUB_USERNAME", "")

    vessels = []

    if aishub_user and aishub_user != "placeholder":
        vessels = await _fetch_from_aishub(aishub_user)

    if not vessels:
        vessels = _get_demo_vessel_positions()
        logger.info("Vessels: Using demo positions (AISHub not configured)")
    else:
        logger.info(f"Vessels: Got {len(vessels)} real positions from AISHub")

    r = await get_redis()
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
    """
    return [
        {
            "mmsi": "477123456",
            "name": "GULF CARRIER",
            "lat": 24.5, "lon": 56.3,
            "speed": 12.4, "heading": 95,
            "destination": "SIKKA",
            "vessel_type": "crude_tanker",
            "source": "demo"
        },
        {
            "mmsi": "477234567",
            "name": "ARABIAN STAR",
            "lat": 22.1, "lon": 60.2,
            "speed": 11.8, "heading": 110,
            "destination": "VADINAR",
            "vessel_type": "crude_tanker",
            "source": "demo"
        },
        {
            "mmsi": "477345678",
            "name": "INDIA SPIRIT",
            "lat": 19.8, "lon": 63.4,
            "speed": 13.1, "heading": 120,
            "destination": "PARADIP",
            "vessel_type": "crude_tanker",
            "source": "demo"
        },
        {
            "mmsi": "477456789",
            "name": "PERSIAN GLORY",
            "lat": 26.2, "lon": 57.1,
            "speed": 10.9, "heading": 88,
            "destination": "KOCHI",
            "vessel_type": "crude_tanker",
            "source": "demo"
        }
    ]


# ---- Price Polling --------------------------------------
async def fetch_live_prices() -> dict:
    """
    Fetches Brent and WTI prices using yfinance.
    Writes to Redis prices:live with 6-minute TTL.
    Called every 5 minutes by APScheduler.
    """
    try:
        prices = await _fetch_prices_yfinance()

        if not prices:
            logger.warning("Price fetch: yfinance failed, using cached/default")
            return {}

        r = await get_redis()
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
                    "source": "yfinance"
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
        logger.error(f"Alpha Vantage daily fetch error: {e}") 