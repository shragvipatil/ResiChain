"""
backend/scripts/day20_websocket_probe.py

Day 20 diagnostic — observed truth, not theory.

Connects to the real /ws/agent-status WebSocket, triggers a compound
crisis via the SANCTIONED manual method (direct-set + freeze — the same
one locked as the demo trigger hours ago), and prints EVERY message
type that actually arrives over the next 15 seconds. This settles
exactly what Person B's message raised: does the manually-triggered
crisis graph actually broadcast the event types the frontend listens
for, with the exact casing/naming it expects?

    docker-compose exec fastapi python scripts/day20_websocket_probe.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
from db.redis_client import get_redis

WS_URL = "ws://localhost:8000/ws/agent-status"
TRIGGER_URL = "http://localhost:8000/api/crisis/trigger"
LISTEN_SECONDS = 15


async def main():
    r = await get_redis()
    await r.set("risk:state", json.dumps(
        {"Hormuz": 0.82, "Red_Sea": 0.87, "Suez": 0.18, "Cape": 0.05}
    ))
    await r.setex("demo:risk_freeze", 60, "1")

    seen_types = []

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL, heartbeat=30) as ws:
            greeting = await asyncio.wait_for(ws.receive(), timeout=5)
            print(f"[connected] {json.loads(greeting.data)}")

            async def listen():
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.receive(), timeout=LISTEN_SECONDS)
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(msg.data)
                            mtype = payload.get("type", "<no type field>")
                            seen_types.append(mtype)
                            print(f"  BROADCAST type={mtype!r}")
                except asyncio.TimeoutError:
                    pass

            listener = asyncio.create_task(listen())

            print("Triggering crisis via the sanctioned manual method...")
            async with session.post(TRIGGER_URL) as resp:
                result = await resp.json()
                print(f"  trigger HTTP {resp.status}, "
                      f"playbook.status={result.get('playbook', {}).get('status')}")

            await listener

    print("\n" + "=" * 60)
    print(f"Message types observed over {LISTEN_SECONDS}s after trigger:")
    for t in seen_types:
        print(f"  - {t}")
    if not seen_types:
        print("  (none — nothing broadcast at all during the crisis run)")
    print("=" * 60)
    print("\nCompare this list against what frontend/src (WebSocket handler /")
    print("AppContext.tsx) actually switches on. Any expected type NOT in this")
    print("list means the backend never sends it. Any casing mismatch between")
    print("this list and the frontend's expected strings means it's sent but")
    print("never matched.")


if __name__ == "__main__":
    asyncio.run(main())
