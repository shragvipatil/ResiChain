"""
backend/scripts/test_websocket_stability.py

Day 14, Task 2 — WebSocket stability + reconnection (headless).

This is the SERVER-SIDE proof for Task 2: it opens 5 simultaneous
WebSocket connections to /ws/agent-status (the same endpoint Person C's
dashboard uses), then triggers a crisis and measures how long each of
the 5 takes to receive the broadcast. It validates that the FastAPI
ConnectionManager fans a single event out to all connected clients
within the 1-second target.

It does NOT replace the real browser test — a passing result here means
"the server broadcasts correctly to 5 clients," which is exactly the
half you (Person A) own. If the real 5-tab browser test later fails
while THIS passes, the bug is in the frontend's render/reconnect logic
(Person C), not your WebSocket server. That separation is the main
reason to run this.

Two sub-tests:
  1. fan-out:    5 clients connected, trigger crisis, assert all 5 get a
                 broadcast within 1s.
  2. reconnect:  drop one client mid-crisis, reconnect it, assert it
                 receives the connection-confirmation + subsequent
                 broadcasts immediately (proves a reconnecting client
                 isn't left stale).

Run from the HOST:
    docker-compose exec fastapi python scripts/test_websocket_stability.py

Or via pytest:
    python -m pytest scripts/test_websocket_stability.py -v --asyncio-mode=auto

Requires only aiohttp and redis.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import aiohttp
import redis.asyncio as redis

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WS_URL = os.getenv("WS_TEST_URL", "ws://localhost:8000/ws/agent-status")
TRIGGER_URL = os.getenv("TRIGGER_TEST_URL", "http://localhost:8000/api/crisis/trigger")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

N_CLIENTS = 5
LATENCY_TARGET_S = 1.0

COMPOUND_RISK = {
    "Hormuz": 0.82,
    "Red_Sea": 0.87,
    "Suez": 0.18,
    "Cape": 0.05,
}

UPDATE_TYPES = {
    "PIPELINE_NODE_COMPLETE",
    "compound_disruption_detected",
    "RISK_STATE_UPDATED",
    "risk_update",
    "crisis_alert",
    "playbook_ready",
}


async def _set_compound_state() -> None:
    """
    Use a fresh Redis async client for this script call and close it
    immediately after use.

    This avoids cross-event-loop reuse of a shared app-level Redis client,
    which is what causes:
      - got Future attached to a different loop
      - Event loop is closed
    """
    r = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await r.set("risk:state", json.dumps(COMPOUND_RISK))
    finally:
        await r.aclose()


async def _fire_trigger(session: aiohttp.ClientSession) -> None:
    try:
        async with session.post(TRIGGER_URL) as resp:
            await resp.read()
    except Exception:
        pass


async def _client_wait_for_update(
    session: aiohttp.ClientSession,
    client_id: int,
    ready_evt: asyncio.Event,
    start_holder: dict,
) -> dict:
    """
    Connect, signal ready, then block until the first genuine dashboard
    update arrives. Returns {client_id, latency_s, msg_type} or an error.
    """
    try:
        async with session.ws_connect(WS_URL, heartbeat=30) as ws:
            first = await asyncio.wait_for(ws.receive(), timeout=5)

            if first.type != aiohttp.WSMsgType.TEXT:
                return {
                    "client_id": client_id,
                    "error": f"missing greeting, first frame type={first.type}",
                }

            try:
                greeting = json.loads(first.data)
            except Exception:
                return {
                    "client_id": client_id,
                    "error": "greeting was not valid JSON",
                }

            if greeting.get("type") != "connected":
                return {
                    "client_id": client_id,
                    "error": f"unexpected greeting type={greeting.get('type')}",
                }

            ready_evt.set()

            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=15)

                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        payload = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue

                    if payload.get("type") in UPDATE_TYPES:
                        t0 = start_holder.get("t0")
                        latency = (time.monotonic() - t0) if t0 else -1.0
                        return {
                            "client_id": client_id,
                            "latency_s": latency,
                            "msg_type": payload.get("type"),
                        }

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    return {"client_id": client_id, "error": "socket closed early"}

    except asyncio.TimeoutError:
        return {"client_id": client_id, "error": "no update within timeout"}
    except Exception as exc:
        return {"client_id": client_id, "error": str(exc)}


async def test_fanout() -> bool:
    print(f"\n{'=' * 60}")
    print(f"  SUB-TEST 1: fan-out to {N_CLIENTS} clients")
    print(f"{'=' * 60}")

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        ready_events = [asyncio.Event() for _ in range(N_CLIENTS)]
        start_holder: dict = {}

        tasks = [
            asyncio.create_task(
                _client_wait_for_update(session, i + 1, ready_events[i], start_holder)
            )
            for i in range(N_CLIENTS)
        ]

        await asyncio.gather(*(evt.wait() for evt in ready_events))
        print(f"  all {N_CLIENTS} clients connected — triggering crisis...")

        await _set_compound_state()
        start_holder["t0"] = time.monotonic()

        trigger_task = asyncio.create_task(_fire_trigger(session))
        results = await asyncio.gather(*tasks)
        await trigger_task

    print(f"  {'client':<10}{'latency':<14}{'msg type':<28}{'status'}")
    print(f"  {'-' * 58}")

    all_ok = True
    for res in sorted(results, key=lambda r: r["client_id"]):
        cid = res["client_id"]

        if "error" in res:
            print(f"  {cid:<10}{'—':<14}{'—':<28}FAIL: {res['error']}")
            all_ok = False
            continue

        lat = res["latency_s"]
        within = lat >= 0 and lat <= LATENCY_TARGET_S
        status = "OK" if within else f"SLOW (> {LATENCY_TARGET_S}s)"

        if not within:
            all_ok = False

        print(f"  {cid:<10}{lat * 1000:<14.0f}{res['msg_type']:<28}{status}")

    print(f"  {'-' * 58}")
    if all_ok:
        print(
            f"  RESULT: PASS — all {N_CLIENTS} clients updated within "
            f"{LATENCY_TARGET_S}s."
        )
    else:
        print("  RESULT: FAIL — see clients above.")

    return all_ok


async def test_reconnect() -> bool:
    print(f"\n{'=' * 60}")
    print("  SUB-TEST 2: mid-crisis reconnection")
    print(f"{'=' * 60}")

    timeout = aiohttp.ClientTimeout(total=30)
    ok_greeting = False
    got_update = False

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(WS_URL, heartbeat=30) as ws:
            greeting = await asyncio.wait_for(ws.receive(), timeout=5)
            if greeting.type != aiohttp.WSMsgType.TEXT:
                print("  initial greeting missing")
                return False

            g = json.loads(greeting.data)
            assert g.get("type") == "connected", g
            print("  client connected, got greeting — now dropping it mid-crisis")

        await asyncio.sleep(0.5)

        t0 = time.monotonic()
        async with session.ws_connect(WS_URL, heartbeat=30) as ws2:
            greeting2 = await asyncio.wait_for(ws2.receive(), timeout=5)
            reconnect_latency = time.monotonic() - t0

            if greeting2.type == aiohttp.WSMsgType.TEXT:
                g2 = json.loads(greeting2.data)
                ok_greeting = g2.get("type") == "connected"
            else:
                ok_greeting = False

            print(
                f"  reconnected in {reconnect_latency * 1000:.0f} ms, "
                f"greeting={'OK' if ok_greeting else 'MISSING'}"
            )

            await _set_compound_state()
            await _fire_trigger(session)

            try:
                while True:
                    msg = await asyncio.wait_for(ws2.receive(), timeout=10)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        payload = json.loads(msg.data)
                        if payload.get("type") in UPDATE_TYPES:
                            got_update = True
                            print(
                                f"  reconnected client received "
                                f"'{payload.get('type')}' broadcast — good"
                            )
                            break
            except asyncio.TimeoutError:
                pass

    print(f"  {'-' * 58}")
    ok = ok_greeting and got_update

    if ok:
        print("  RESULT: PASS — reconnected client immediately rejoined the")
        print("  broadcast pool and received live updates.")
    else:
        print("  RESULT: FAIL — reconnected client did not receive updates.")
        if not got_update:
            print("  (Server accepted the reconnect but no broadcast arrived —")
            print("   check that the trigger actually produced a crisis.)")

    return ok


async def main() -> int:
    print("WebSocket stability test — server-side (Person A scope)")
    print(f"target: all {N_CLIENTS} clients update within {LATENCY_TARGET_S}s")

    fanout_ok = await test_fanout()
    await asyncio.sleep(1)
    reconnect_ok = await test_reconnect()

    print(f"\n{'=' * 60}")
    print(
        f"  OVERALL: fan-out {'PASS' if fanout_ok else 'FAIL'}, "
        f"reconnect {'PASS' if reconnect_ok else 'FAIL'}"
    )
    print(f"{'=' * 60}")
    print("\n  NOTE: this proves the WebSocket SERVER broadcasts correctly.")
    print("  Still run the real 5-tab browser test against Person C's")
    print("  frontend for the full milestone — if that fails while this")
    print("  passes, the bug is frontend-side, not your server.")

    return 0 if (fanout_ok and reconnect_ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))