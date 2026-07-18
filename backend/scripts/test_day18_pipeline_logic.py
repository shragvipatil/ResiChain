"""
backend/scripts/test_day18_pipeline_logic.py

Day 18 — Scenarios 2 & 3 PIPELINE-LOGIC verification.

Separates two questions the inject-crisis run conflated:

  (a) "Does one injected event reach crisis level?"  -> NO, by design
      (Agent 3 needs multiple corroborating factors; one severity-8
      event lands ~0.38, below the 0.65 threshold). That's an
      injection-TUNING question for the team.

  (b) "Given crisis-level risk, does the pipeline produce correct
      compound / edge-case results?" -> this script answers (b).

It sets the crisis risk vector directly (with the demo:risk_freeze guard
so Agent 3 can't stomp it mid-run — same proven method used earlier),
triggers the graph, and asserts the pipeline LOGIC is correct. This
confirms Scenarios 2 & 3 backend behavior is sound regardless of how the
demo ultimately chooses to reach crisis levels.

    docker-compose exec fastapi python scripts/test_day18_pipeline_logic.py scenario2
    docker-compose exec fastapi python scripts/test_day18_pipeline_logic.py scenario3
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
from db.redis_client import get_redis

BASE_URL = "http://localhost:8000"
FREEZE_KEY = "demo:risk_freeze"
FREEZE_TTL = 300


async def _set_state_frozen(risk: dict) -> None:
    """Set risk:state and freeze Agent 3 so it survives the trigger."""
    r = await get_redis()
    await r.setex(FREEZE_KEY, FREEZE_TTL, "1")
    await r.set("risk:state", json.dumps(risk))


async def _trigger(session) -> dict:
    async with session.post(f"{BASE_URL}/api/crisis/trigger") as resp:
        return await resp.json()


def _kv(label, value):
    print(f"    {label:<28} {value}")


async def scenario2() -> int:
    print("=" * 64)
    print("  SCENARIO 2 (pipeline logic) — Compound Hormuz + Red Sea")
    print("=" * 64)
    risk = {"Hormuz": 0.82, "Red_Sea": 0.87, "Suez": 0.18, "Cape": 0.05}
    await _set_state_frozen(risk)
    print(f"\n[1] Crisis state set (frozen): {risk}")

    async with aiohttp.ClientSession() as session:
        print("[2] Triggering crisis graph...")
        result = await _trigger(session)

    compound = result.get("compound_risk")
    is_comp = result.get("is_compound_event")
    blocked = result.get("blocked_chokepoints")
    surviving = result.get("surviving_routes", [])
    playbook = result.get("playbook", {})

    print("\n[3] Backend facts:")
    _kv("is_compound_event", is_comp)
    _kv("blocked_chokepoints", blocked)
    _kv("compound_risk", compound)
    _kv("surviving_routes (count)", len(surviving))
    for route in surviving:
        print(f"      - {route.get('supplier')}: {route.get('route')}")
    _kv("playbook status", playbook.get("status"))
    _kv("signal_to_playbook_seconds", playbook.get("signal_to_playbook_seconds"))

    expected = round(1.0 - (1.0 - 0.82) * (1.0 - 0.87), 4)
    formula_ok = compound == expected
    print(f"\n[4] compound_risk formula: 1-(1-0.82)(1-0.87) = {expected} "
          f"-> got {compound}  {'MATCH' if formula_ok else 'MISMATCH'}")

    ok = is_comp is True and formula_ok and playbook.get("status") in ("CRITICAL", "HIGH", "WARNING")
    print(f"\n  RESULT: {'PASS — compound pipeline logic correct' if ok else 'FAIL'}")
    print("  NOTE: how the DEMO reaches these risk levels (inject vs direct-set)")
    print("  is a separate team decision — this only proves the logic is sound.")
    return 0 if ok else 1


async def scenario3() -> int:
    print("=" * 64)
    print("  SCENARIO 3 (pipeline logic) — All corridors blocked")
    print("=" * 64)
    risk = {"Hormuz": 0.9, "Red_Sea": 0.9, "Suez": 0.88, "Cape": 0.85}
    await _set_state_frozen(risk)
    print(f"\n[1] Extreme state set (frozen): {risk}")

    async with aiohttp.ClientSession() as session:
        print("[2] Triggering crisis graph (watching for any crash)...")
        try:
            result = await _trigger(session)
            crashed = False
        except Exception as exc:
            print(f"    !! EXCEPTION: {exc}")
            result = {}
            crashed = True

    if crashed:
        print("\n  RESULT: FAIL — pipeline crashed on all-corridors-blocked.")
        return 1

    risk_vector = result.get("risk_vector", {})
    surviving = result.get("surviving_routes", [])
    spr = result.get("spr_schedule_final") or result.get("spr_schedule_first_pass") or {}
    playbook = result.get("playbook", {})

    print("\n[3] Backend facts:")
    _kv("blocked_chokepoints", result.get("blocked_chokepoints"))
    _kv("surviving_routes (count)", len(surviving))
    _kv("spr feasible", spr.get("feasible"))
    _kv("spr critical_warning", spr.get("critical_warning"))
    _kv("playbook status", playbook.get("status"))
    _kv("playbook_id", playbook.get("playbook_id"))

    print("\n[4] Graceful-degradation checks:")
    has_warning = spr.get("critical_warning") is not None
    has_playbook = bool(playbook.get("status"))
    print(f"    - no crash:                 {'yes' if not crashed else 'NO'}")
    print(f"    - SPR critical_warning set: {'yes' if has_warning else 'NO'}")
    print(f"    - playbook still generated: {'yes' if has_playbook else 'NO'}")

    ok = (not crashed) and has_warning and has_playbook
    print(f"\n  RESULT: {'PASS — degrades gracefully, no crash' if ok else 'FAIL — see above'}")
    return 0 if ok else 1


async def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("scenario2", "scenario3"):
        print("Usage: test_day18_pipeline_logic.py [scenario2|scenario3]")
        return 1
    rc = await (scenario2() if sys.argv[1] == "scenario2" else scenario3())

    # Clean up the freeze so normal operation resumes.
    r = await get_redis()
    await r.delete(FREEZE_KEY)
    print("\n(demo:risk_freeze cleared — Agent 3 resumes normal risk calc)")
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main())) 