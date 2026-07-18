"""
backend/scripts/test_scheduler_timing.py

Day 16, Person A — APScheduler job execution + timing test.

The scheduler runs inside the FastAPI app (main.py lifespan), so a
standalone script can't reach into its in-memory job registry. Instead
this test does something stronger and more honest: it watches the
*effects* the scheduled jobs produce, proving they actually FIRE on
their configured cadence — not merely that they were registered.

The most observable, fast-cadence job is Agent 3 (id=agent3_risk_engine,
interval=60s), which writes a fresh agent3:last_run heartbeat to Redis
every cycle. Agent 1 verification (30s) is even faster. This test:

  1. Reads the current agent3:last_run timestamp.
  2. Waits one full interval + margin (~70s).
  3. Reads it again and asserts it ADVANCED — i.e. the scheduled job
     fired within its window. A stale timestamp = the scheduler isn't
     running that job.

It also snapshots agent1's heartbeat as a second, faster-cadence signal.

Missed-job handling: APScheduler is configured in main.py; if you want
to specifically test coalescing/misfire behavior, pause the container
(docker-compose pause fastapi) for longer than an interval, unpause, and
re-run — APScheduler coalesces the missed runs rather than firing a
backlog burst. This script's advance-check confirms the job resumes
firing after such a gap.

Run inside the container:

    docker-compose exec fastapi python scripts/test_scheduler_timing.py
    docker-compose exec fastapi python scripts/test_scheduler_timing.py --wait 70
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.redis_client import get_redis

AGENT3_INTERVAL_S = 60  # matches scheduler.add_job(..., seconds=60, id="agent3_risk_engine")


async def _read_heartbeat(r, key: str):
    raw = await r.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_ts(hb: dict | None):
    if not hb:
        return None
    ts = hb.get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", type=int, default=AGENT3_INTERVAL_S + 12,
                        help="Seconds to wait for the next scheduler tick "
                             "(default: one interval + 12s margin).")
    args = parser.parse_args()

    print("=" * 60)
    print("  APScheduler job execution + timing test")
    print("=" * 60)

    r = await get_redis()

    hb1_before = await _read_heartbeat(r, "agent1:last_run")
    hb3_before = await _read_heartbeat(r, "agent3:last_run")
    t3_before = _parse_ts(hb3_before)

    print(f"\n[1] Initial heartbeats:")
    print(f"    agent1:last_run = {hb1_before.get('timestamp') if hb1_before else 'MISSING'}")
    print(f"    agent3:last_run = {t3_before.isoformat() if t3_before else 'MISSING'}")

    if t3_before is None:
        print("\n  WARNING: no agent3:last_run yet. Either the app just started")
        print("  (give Agent 3 one 60s cycle) or a demo:risk_freeze is active")
        print("  (Agent 3 still logs its heartbeat under freeze, so this should")
        print("  still advance). Waiting and re-checking anyway...")

    print(f"\n[2] Waiting {args.wait}s for the next Agent 3 tick (interval={AGENT3_INTERVAL_S}s)...")
    await asyncio.sleep(args.wait)

    hb3_after = await _read_heartbeat(r, "agent3:last_run")
    t3_after = _parse_ts(hb3_after)
    hb1_after = await _read_heartbeat(r, "agent1:last_run")

    print(f"\n[3] After wait:")
    print(f"    agent3:last_run = {t3_after.isoformat() if t3_after else 'MISSING'}")

    advanced = (
        t3_after is not None
        and (t3_before is None or t3_after > t3_before)
    )

    # Agent 1 verification runs every 30s — with a ~70s wait it should have
    # ticked at least once too. Secondary confirmation.
    a1_advanced = False
    if hb1_before and hb1_after:
        a1_advanced = hb1_after.get("timestamp") != hb1_before.get("timestamp")

    print("\n" + "=" * 60)
    print(f"  Agent 3 (60s) heartbeat advanced : {'PASS' if advanced else 'FAIL'}")
    print(f"  Agent 1 (30s) heartbeat advanced : {'PASS' if a1_advanced else 'n/a'}")
    print("=" * 60)

    if advanced:
        print("  RESULT: PASS — the scheduler is firing jobs on cadence. The")
        print("  Agent 3 heartbeat advanced within one interval, proving the")
        print("  scheduled job actually runs (not just registered).")
        return 0

    print("  RESULT: FAIL — Agent 3 heartbeat did not advance in the window.")
    print("  Possible causes: scheduler not started, the job errored (check")
    print("  docker-compose logs fastapi), or the container was paused.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main())) 