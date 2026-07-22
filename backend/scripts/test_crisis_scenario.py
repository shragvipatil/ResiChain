import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import redis.asyncio as redis


REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
STREAM_NAME = "eventsverified"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def main() -> None:
    r = redis.from_url(REDIS_URL, decode_responses=True)

    event = {
        "eventid": str(uuid4()),
        "event": "Test CONFIRMED maritime disruption in Strait of Hormuz for frontend/browser crisis validation.",
        "source": "TESTHARNESS",
        "sourcesconfirming": ["TESTHARNESS", "UKMTO"],
        "location": "Strait of Hormuz",
        "corridor": "Hormuz",
        "severity": 9,
        "stage": "CONFIRMED",
        "confidence": 0.95,
        "eventtimestamp": iso_now(),
        "verifiedat": iso_now(),
        "hourssinceevent": 0.0,
        "rawsourceurl": "https://example.com/test-crisis-scenario"
    }

    payload = {"data": json.dumps(event)}

    msg_id = await r.xadd(STREAM_NAME, payload)
    print("=" * 60)
    print("Injected crisis test event into Redis Stream")
    print(f"stream: {STREAM_NAME}")
    print(f"message id: {msg_id}")
    print(f"corridor: {event['corridor']}")
    print(f"stage: {event['stage']}")
    print(f"confidence: {event['confidence']}")
    print("=" * 60)
    print("Expected next behavior:")
    print("- Agent 3 consumes CONFIRMED event and updates riskstate")
    print("- If corridor risk exceeds 0.65, crisis graph should trigger")
    print("- WebSocket clients should receive live updates")
    print("- Frontend tabs should update without refresh")
    print("=" * 60)

    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())