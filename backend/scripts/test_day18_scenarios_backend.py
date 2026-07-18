"""
backend/scripts/test_day18_scenarios_backend.py

Day 18 — Scenarios 2 & 3, BACKEND-ONLY trace.

This runs the parts of Scenarios 2 and 3 that don't require Person C's
frontend or Person B watching Neo4j/Postgres in person — it uses the
real injection pipeline (POST /api/demo/inject-crisis, same as the joint
session would) and prints every backend fact a person watching agent
logs + WebSocket events would report on. Run this alone; re-run the
frontend-visible parts (Cape route animation, rejection trace UI) with
Person C once their dashboard fix is in, and cross-check the Neo4j/PG
observations with Person B separately.

    docker-compose exec fastapi python scripts/test_day18_scenarios_backend.py scenario2
    docker-compose exec fastapi python scripts/test_day18_scenarios_backend.py scenario3

Each corridor injection is TWO real events (GDELT signal, then a UKMTO
confirmation 10s later) via the same run_agent1_demo_inject() the demo
uses — NOT a manual redis-cli SET. That's deliberate: it's the corrected
method from the WebSocket testing thread, so this also re-validates that
fix as a side effect.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp

BASE_URL = "http://localhost:8000"
CRISIS_SEVERITY = 8

# Per-corridor wait: 2 injected events (GDELT + UKMTO 10s apart) + margin
# for Agent 1 verification and Agent 3's next 60s cycle to pick it up.
WAIT_AFTER_INJECT_S = 75


async def inject_corridor(session: aiohttp.ClientSession, corridor: str) -> None:
    print(f"  injecting {corridor} (GDELT signal, then UKMTO confirm +10s)...")
    async with session.post(
        f"{BASE_URL}/api/demo/inject-crisis",
        params={"corridor": corridor, "severity": CRISIS_SEVERITY},
    ) as resp:
        body = await resp.text()
        print(f"    -> HTTP {resp.status}")


async def trigger_and_report(session: aiohttp.ClientSession) -> dict:
    async with session.post(f"{BASE_URL}/api/crisis/trigger") as resp:
        return await resp.json()


def _print_kv(label: str, value) -> None:
    print(f"    {label:<26} {value}")


async def scenario2() -> int:
    """Compound disruption: Hormuz + Red Sea both injected."""
    print("=" * 64)
    print("  SCENARIO 2 (backend-only) — Compound: Hormuz + Red Sea")
    print("=" * 64)

    async with aiohttp.ClientSession() as session:
        print("\n[1] Injecting both corridors through the real pipeline...")
        await inject_corridor(session, "Hormuz")
        await inject_corridor(session, "Red_Sea")

        print(f"\n[2] Waiting {WAIT_AFTER_INJECT_S}s for Agent 1 verification "
              f"+ Agent 3's next risk cycle to pick both up...")
        await asyncio.sleep(WAIT_AFTER_INJECT_S)

        print("\n[3] Triggering the crisis graph...")
        result = await trigger_and_report(session)

    print("\n[4] Backend facts to report to the team:")
    compound_risk = result.get("compound_risk")
    is_compound = result.get("is_compound_event")
    blocked = result.get("blocked_chokepoints")
    surviving = result.get("surviving_routes", [])
    risk_vector = result.get("risk_vector", {})
    playbook = result.get("playbook", {})

    _print_kv("risk_vector", risk_vector)
    _print_kv("is_compound_event", is_compound)
    _print_kv("blocked_chokepoints", blocked)
    _print_kv("compound_risk", compound_risk)
    _print_kv("surviving_routes (count)", len(surviving))
    for route in surviving:
        print(f"      - {route.get('supplier')}: {route.get('route')}")
    _print_kv("playbook status", playbook.get("status"))
    _print_kv("playbook_id", playbook.get("playbook_id"))
    _print_kv("signal_to_playbook_seconds", playbook.get("signal_to_playbook_seconds"))

    print("\n[5] Checks worth confirming manually:")
    hormuz = risk_vector.get("Hormuz", 0)
    red_sea = risk_vector.get("Red_Sea", 0)
    if hormuz and red_sea:
        expected = round(1.0 - (1.0 - hormuz) * (1.0 - red_sea), 4)
        match = compound_risk == expected
        print(f"    compound_risk formula check: 1-(1-{hormuz})(1-{red_sea}) "
              f"= {expected}  {'MATCHES' if match else 'MISMATCH!! -> ' + str(compound_risk)}")
    print("    - KG surviving-route traversal: cross-check the 'surviving_routes'")
    print("      list above against Neo4j directly with Person B watching.")
    print("    - Cape route animation on the map: needs Person C's frontend —")
    print("      run this same scenario again in the joint session to confirm")
    print("      the map actually draws the dashed Cape polyline.")

    ok = is_compound is True and compound_risk is not None and playbook.get("status")
    print(f"\n  RESULT: {'backend PASS' if ok else 'backend FAIL — investigate above'}")
    return 0 if ok else 1


async def scenario3() -> int:
    """Edge case: all four corridors pushed critical simultaneously."""
    print("=" * 64)
    print("  SCENARIO 3 (backend-only) — Edge case: all corridors blocked")
    print("=" * 64)

    async with aiohttp.ClientSession() as session:
        print("\n[1] Injecting ALL FOUR corridors through the real pipeline...")
        for corridor in ("Hormuz", "Red_Sea", "Suez", "Cape"):
            await inject_corridor(session, corridor)

        print(f"\n[2] Waiting {WAIT_AFTER_INJECT_S}s for verification + risk cycle...")
        await asyncio.sleep(WAIT_AFTER_INJECT_S)

        print("\n[3] Triggering the crisis graph...")
        try:
            result = await trigger_and_report(session)
            crashed = False
        except Exception as exc:
            print(f"    !! Trigger raised an exception: {exc}")
            result = {}
            crashed = True

    if crashed:
        print("\n  RESULT: FAIL — the pipeline crashed on an all-corridors-blocked")
        print("  scenario. This is exactly the edge case Day 18 wants caught before")
        print("  a demo could ever hit it live.")
        return 1

    print("\n[4] Backend facts to report to the team:")
    risk_vector = result.get("risk_vector", {})
    blocked = result.get("blocked_chokepoints")
    surviving = result.get("surviving_routes", [])
    spr = result.get("spr_schedule_final", {}) or result.get("spr_schedule_first_pass", {})
    playbook = result.get("playbook", {})

    _print_kv("risk_vector", risk_vector)
    _print_kv("blocked_chokepoints", blocked)
    _print_kv("surviving_routes (count)", len(surviving))
    _print_kv("spr feasible", spr.get("feasible"))
    _print_kv("spr critical_warning", spr.get("critical_warning"))
    _print_kv("playbook status", playbook.get("status"))
    _print_kv("playbook_id", playbook.get("playbook_id"))

    print("\n[5] What a graceful edge-case result looks like:")
    print("    - surviving_routes should be EMPTY or near-empty (no safe route left)")
    print("    - spr feasible = False, with a clear critical_warning message")
    print("      (NOT a silent/blank failure)")
    print("    - playbook still GENERATES (status likely CRITICAL), containing")
    print("      the SPR emergency drawdown schedule — it should NOT error out")
    print("      just because procurement has zero good options")
    print("    - No unhandled exception anywhere in this run (see step [3])")

    no_crash = not crashed
    graceful = bool(playbook.get("status")) and spr.get("critical_warning") is not None
    ok = no_crash and graceful
    print(f"\n  RESULT: {'backend PASS — degrades gracefully' if ok else 'backend FAIL — see gaps above'}")
    return 0 if ok else 1


async def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("scenario2", "scenario3"):
        print("Usage: test_day18_scenarios_backend.py [scenario2|scenario3]")
        return 1
    if sys.argv[1] == "scenario2":
        return await scenario2()
    return await scenario3()


if __name__ == "__main__":
    sys.exit(asyncio.run(main())) 