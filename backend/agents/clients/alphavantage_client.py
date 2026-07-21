# ============================================================
# ResiChain — Alpha Vantage / yfinance Price Alert Client
# Monitors Brent crude price for sudden moves > 5%
# Uses yfinance as primary (no key needed)
# Falls back to Alpha Vantage if yfinance fails
# ============================================================

import aiohttp
import logging
import os
import json
import asyncio
from datetime import datetime
from db.redis_client import get_redis, publish_event

logger = logging.getLogger(__name__)

PRICE_MOVE_THRESHOLD = 0.05  # 5% move triggers alert

async def fetch_brent_price_alert() -> dict:
    """
    Compares today vs yesterday Brent crude price.
    Flags moves greater than 5% as a supply shock signal.
    Called every 5 minutes by APScheduler.
    """
    try:
        # Try yfinance first (no API key needed)
        price_data = await _fetch_via_yfinance()

        # Fall back to Alpha Vantage if yfinance fails
        if not price_data:
            price_data = await _fetch_via_alphavantage()

        if not price_data:
            logger.warning("Price client: Both yfinance and Alpha Vantage failed")
            return {}

        current_price = price_data["current"]
        previous_price = price_data["previous"]

        if previous_price == 0:
            return {}

        pct_change = (current_price - previous_price) / previous_price

        # Store current price in Redis for other agents to use
        redis = await get_redis()
        await redis.setex(
            "brent:price:latest",
            3600,  # 1 hour TTL
            json.dumps({
                "price": current_price,
                "change_pct": round(pct_change * 100, 2),
                "source": price_data["source"]
            })
        )

        result = {
            "current_price": current_price,
            "previous_price": previous_price,
            "change_pct": round(pct_change * 100, 2),
            "source": price_data["source"],
            "alert_triggered": abs(pct_change) >= PRICE_MOVE_THRESHOLD
        }

        # Publish price shock event if threshold crossed
        if abs(pct_change) >= PRICE_MOVE_THRESHOLD:
            direction = "spike" if pct_change > 0 else "crash"
            event = {
                "source": "AlphaVantage_PriceAlert",
                "headline": f"Brent crude price {direction}: {pct_change*100:.1f}% move detected",
                "corridor": "Global", 
                "severity": 7 if abs(pct_change) > 0.10 else 5,
                "price_current": current_price,
                "price_previous": previous_price,
                "change_pct": pct_change * 100,
                "timestamp": datetime.utcnow().isoformat(),
                "raw_confidence": 0.95
            }
            await publish_event(event)
            logger.warning(
                f"PRICE ALERT: Brent {direction} {pct_change*100:.1f}% "
                f"(${previous_price:.2f} → ${current_price:.2f})"
            )

        logger.info(
            f"Brent price: ${current_price:.2f} "
            f"({'+' if pct_change > 0 else ''}{pct_change*100:.1f}%)"
        )
        return result

    except Exception as e:
        logger.error(f"Price alert client error: {e}")
        return {}


async def _fetch_via_yfinance() -> dict:
    """Fetches Brent price using yfinance — no API key needed."""
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()

        def _download():
            ticker = yf.Ticker("BZ=F")  # Brent crude futures
            hist = ticker.history(period="5d")
            if hist.empty:
                return None
            closes = hist["Close"].dropna()
            if len(closes) < 2:
                return None
            return {
                "current": float(closes.iloc[-1]),
                "previous": float(closes.iloc[-2]),
                "source": "yfinance"
            }

        result = await loop.run_in_executor(None, _download)
        return result

    except Exception as e:
        logger.warning(f"yfinance failed: {e}")
        return None


async def _fetch_via_alphavantage() -> dict:
    """Fallback: fetches Brent price via Alpha Vantage API."""
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    if not api_key or api_key == "placeholder":
        return None

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
                    return None
                data = await resp.json()

        prices = data.get("data", [])
        if len(prices) < 2:
            return None

        return {
            "current": float(prices[0]["value"]),
            "previous": float(prices[1]["value"]),
            "source": "alphavantage"
        }

    except Exception as e:
        logger.warning(f"Alpha Vantage failed: {e}")
        return None


async def get_current_brent_price() -> float:
    """
    Quick helper other agents call to get current Brent price.
    Reads from Redis cache — no API call.
    """
    try:
        redis = await get_redis()
        data = await redis.get("brent:price:latest")
        if data:
            return json.loads(data)["price"]
        return 82.0  # Fallback hardcoded value
    except Exception:
        return 82.0 