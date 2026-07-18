# tests/test_agent7_fix10_race_condition.py
from __future__ import annotations
from unittest.mock import patch
import pytest

from agents.agent7 import validate_batch, DiversificationTracker


MAX_SHARE = 0.40
INDIA_CONSUMPTION_MBD = 5.1

PATCH_SHARE = "agents.agent7.get_supplier_current_share"
PATCH_GRADE = "agents.agent7.check_grade_compatibility"
PATCH_OFAC = "agents.agent7.check_ofac_match"
PATCH_EMBARGO = "agents.agent7.is_comprehensively_sanctioned_country"
PATCH_LOG = "agents.agent7.insert_procurement_evaluation"
PATCH_OP = "agents.agent7._layer4_operational"


def make_candidate(option_id, supplier, confidence, proposed_volume_mbd):
    return {
        "option_id": option_id,
        "supplier": supplier,
        "grade": "Urals",
        "refinery": "Jamnagar RIL",
        "confidence": confidence,
        "proposed_volume_mbd": proposed_volume_mbd,
        "arrival_port": "Vadinar",
        "departure_port": "Novorossiysk",
        "vessel_class": "Suezmax",
    }


class TestFix10RaceCondition:
    def run_batch(self, candidates, starting_share=0.38):
        with patch(PATCH_SHARE, return_value=starting_share), \
             patch(PATCH_GRADE, return_value=True), \
             patch(PATCH_OFAC, return_value=False), \
             patch(PATCH_EMBARGO, return_value=False), \
             patch(PATCH_LOG, return_value=None), \
             patch(PATCH_OP, return_value=None):
            return validate_batch(candidates, playbook_id=None)

    def test_second_candidate_sees_updated_share_not_stale_starting_share(self):
        """
        Core Fix 10 check: Russia starts at 38% share (cap 40%, headroom 2%
        -> 0.02 * 5.1 = 0.102 mbd headroom). Two candidates for Russia, each
        independently requesting 0.15 mbd (would be well within cap if
        checked against the SAME starting 38% twice -- the race condition
        bug). Correct sequential behavior: first candidate consumes the
        remaining headroom and is PARTIAL; second candidate must be BLOCKED
        because there is no headroom left after the first was applied.
        """
        candidates = [
            make_candidate("proc-russia-000", "Russia", confidence=0.90, proposed_volume_mbd=0.15),
            make_candidate("proc-russia-001", "Russia", confidence=0.85, proposed_volume_mbd=0.15),
        ]
        results = self.run_batch(candidates, starting_share=0.38)

        by_option = {r["option_id"]: r for r in results}
        first = by_option["proc-russia-000"]
        second = by_option["proc-russia-001"]

        assert first["status"] == "PARTIAL", f"Expected first (higher confidence) PARTIAL, got {first['status']}"
        assert first["adjusted_volume_mbd"] > 0.0

        assert second["status"] == "BLOCKED", (
            f"Expected second candidate BLOCKED (no headroom left after first "
            f"consumed it), got {second['status']} -- this indicates the race "
            f"condition bug is back: both candidates were validated against "
            f"the SAME stale starting share instead of sequentially updated state."
        )
        assert second["adjusted_volume_mbd"] == 0.0

    def test_batch_sorts_by_confidence_before_sequential_check(self):
        """
        Fix 10 requires sorting by confidence DESC before the sequential
        loop, so the higher-confidence option always gets first claim on
        remaining headroom -- regardless of input order.
        """
        candidates = [
            make_candidate("proc-russia-low", "Russia", confidence=0.60, proposed_volume_mbd=0.15),
            make_candidate("proc-russia-high", "Russia", confidence=0.95, proposed_volume_mbd=0.15),
        ]
        results = self.run_batch(candidates, starting_share=0.38)
        by_option = {r["option_id"]: r for r in results}

        assert by_option["proc-russia-high"]["status"] == "PARTIAL"
        assert by_option["proc-russia-low"]["status"] == "BLOCKED"

    def test_two_different_suppliers_never_share_running_totals(self):
        """
        Sanity check: the running_share dict is keyed per-supplier, so a
        Russia candidate and a UAE candidate in the same batch must never
        interfere with each other's headroom.
        """
        candidates = [
            make_candidate("proc-russia-000", "Russia", confidence=0.90, proposed_volume_mbd=0.15),
            make_candidate("proc-uae-000", "UAE", confidence=0.88, proposed_volume_mbd=0.15),
        ]
        with patch(PATCH_SHARE, side_effect=lambda s: 0.38 if s == "Russia" else 0.08), \
             patch(PATCH_GRADE, return_value=True), \
             patch(PATCH_OFAC, return_value=False), \
             patch(PATCH_EMBARGO, return_value=False), \
             patch(PATCH_LOG, return_value=None), \
             patch(PATCH_OP, return_value=None):
            results = validate_batch(candidates, playbook_id=None)

        by_option = {r["option_id"]: r for r in results}
        assert by_option["proc-uae-000"]["status"] == "APPROVED", (
            "UAE (8% share, well under cap) must not be blocked by Russia's "
            "diversification cap -- running_share must be per-supplier."
        )

    def test_fresh_tracker_per_batch_no_cross_batch_leakage(self):
        """
        validate_batch() must create a NEW DiversificationTracker each call
        (new_diversification_tracker()), never reuse state across separate
        batch invocations -- otherwise a previous crisis run's running_share
        would incorrectly persist and cap suppliers in the next run.
        """
        candidate = make_candidate("proc-russia-a", "Russia", confidence=0.90, proposed_volume_mbd=0.01)

        results_1 = self.run_batch([candidate], starting_share=0.38)
        results_2 = self.run_batch([candidate], starting_share=0.38)

        assert results_1[0]["status"] == results_2[0]["status"] == "APPROVED", (
            "Second independent batch run must start fresh at 38% share, "
            "not inherit running_share state left over from the first batch."
        )