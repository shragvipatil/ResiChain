import sys
from pathlib import Path
from datetime import datetime, timezone
from pprint import pprint

sys.path.append(str(Path(__file__).resolve().parents[1]))

from db.postgres_queries import (
    get_connection,
    insert_verified_event,
    get_verified_events,
    upsert_ofac_entry,
    check_ofac_match,
    insert_playbook,
    insert_playbook_action,
    insert_procurement_evaluation,
    insert_spr_schedule,
    upsert_price_history,
    get_latest_price_history,
)


def print_section(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def main():
    print_section("1) Connection smoke test")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db, NOW() AS ts;")
            row = cur.fetchone()
            pprint(row)

    print_section("2) Insert verified event")
    verified_event_id = insert_verified_event(
        event_json={
            "event_id": "demo-event-1",
            "event": "Test maritime alert for Hormuz",
            "source": "UKMTO",
            "sources_confirming": ["UKMTO"],
            "corridor": "Hormuz",
            "severity": 7,
            "stage": "WATCH",
        },
        corridor="Hormuz",
        stage="WATCH",
        confidence=0.91,
    )
    print("verified_event_id:", verified_event_id)

    print_section("3) Read verified events")
    events = get_verified_events(limit=5, offset=0)
    pprint(events)

    print_section("4) OFAC upsert + sanctions check")
    # NOTE: uses a fake, non-country test entity so this smoke test never
    # collides with real seeded OFAC data or plants a fake country-level
    # row (see: Item 4 bug, "Islamic Republic of Iran" row from this exact
    # script caused a false country-embargo match in production testing).
    TEST_ENTITY = "ZZTEST Smoke Test Entity"
    upsert_ofac_entry(
        entity_name=TEST_ENTITY,
        aliases="ZZTEST Alias Co",
        program="OFAC-SDN-TEST",
        date_imposed="2024-01-01",
    )
    print("check_ofac_match('ZZTEST Smoke Test Entity') ->", check_ofac_match(TEST_ENTITY))
    print("check_ofac_match('Saudi Arabia') ->", check_ofac_match("Saudi Arabia"))

    # Clean up immediately so this test entity never lingers in the real table
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ofac_sdn WHERE entity_name = %s", (TEST_ENTITY,))

    print_section("5) Insert playbook")
    now_utc = datetime.now(timezone.utc)
    playbook_id = insert_playbook(
        signal_detected_at=now_utc,
        playbook_generated_at=now_utc,
        signal_to_playbook_seconds=95,
        status="PENDING_REVIEW",
        ministry_view={
            "risk_level": "HIGH",
            "supply_continuity_pct": 94,
            "additional_cost_usd_billion": 1.4,
        },
        procurement_view={
            "approved_alternatives": ["UAE Murban"],
            "blocked_options": ["Iranian crude"],
        },
        refinery_view={
            "Kochi BPCL": {"grade_switch_feasible": False},
            "Jamnagar RIL": {"grade_switch_feasible": True},
        },
        confidence=0.87,
        inputs={
            "scenario": "Hormuz disruption",
            "trigger": "demo-test",
        },
    )
    print("playbook_id:", playbook_id)

    print_section("6) Insert playbook action")
    playbook_action_id = insert_playbook_action(
        playbook_id=playbook_id,
        option_id="opt-001",
        analyst_decision="APPROVED",
        analyst_note="Approved during smoke test",
    )
    print("playbook_action_id:", playbook_action_id)

    print_section("7) Insert procurement evaluation")
    procurement_eval_id = insert_procurement_evaluation(
        playbook_id=playbook_id,
        option_id="opt-iran-001",
        supplier="Iran",
        grade="Iran Heavy",
        status="BLOCKED",
        rule_triggered="OFAC_SDN",
        reason={
            "rule": "OFAC_SDN",
            "value": "Islamic Republic of Iran",
            "threshold": None,
            "source": "ofac_sdn",
        },
        confidence=0.99,
    )
    print("procurement_eval_id:", procurement_eval_id)

    print_section("8) Insert SPR schedule")
    spr_schedule_id = insert_spr_schedule(
        playbook_id=playbook_id,
        feasible=True,
        daily_drawdown_schedule=[0.42] * 10 + [0.21] * 20,
        confidence=0.82,
        spr_remaining_mb=24.5,
        infeasibility_warning=None,
    )
    print("spr_schedule_id:", spr_schedule_id)

    print_section("9) Upsert + fetch latest price history")
    upsert_price_history(
        date=now_utc.date(),
        brent_usd=84.25,
        wti_usd=79.10,
        source="smoke-test",
    )
    latest_price = get_latest_price_history()
    pprint(latest_price)

    print_section("10) Row counts")
    with get_connection() as conn:
        with conn.cursor() as cur:
            for table in [
                "verified_events",
                "ofac_sdn",
                "playbooks",
                "playbook_actions",
                "procurement_evaluations",
                "spr_schedules",
                "price_history",
            ]:
                cur.execute(f"SELECT COUNT(*) AS count FROM {table};")
                result = cur.fetchone()
                print(f"{table}: {result['count']}")

    print_section("All tests completed successfully")


if __name__ == "__main__":
    main()