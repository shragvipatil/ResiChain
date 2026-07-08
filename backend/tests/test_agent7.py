"""
tests/test_agent7.py
=====================
Unit tests for agents/agent7.py — constraint validator (4 layers).

Run with:
    docker exec -it resichain_fastapi python -m pytest tests/test_agent7.py -v

Tests verify:
  - Layer 1 (OFAC sanctions) blocks correctly
  - Layer 2 (grade compatibility) blocks correctly
  - Layer 3 (diversification cap, Fix 10) blocks/partials correctly and is
    applied SEQUENTIALLY — the race-condition scenario from Day 14/19 of the
    schedule (two simultaneous 1.5% Russia recommendations, 2% headroom left:
    only the first is approved in full, the second is blocked/partial)
  - Layer 4 (operational: port capacity, tanker availability, contract
    ceiling, take-or-pay floor) blocks/partials correctly
  - A fully clean candidate is APPROVED
  - validate_batch() sorts by confidence and shares one tracker across the
    whole batch (this is what makes Fix 10 sequential rather than parallel)
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Patch targets — everything agent7 pulls from Neo4j/Postgres/Redis
# ---------------------------------------------------------------------------

PATCH_OFAC = "agents.agent7.check_ofac_match"
PATCH_GRADE = "agents.agent7.check_grade_compatibility"
PATCH_SHARE = "agents.agent7.get_supplier_current_share"
PATCH_PORT = "agents.agent7.get_port_specs"
PATCH_HEADROOM = "agents.agent7.get_contract_headroom"
PATCH_VESSELS = "agents.agent7._get_vessels_near_port"
PATCH_INSERT = "agents.agent7.insert_procurement_evaluation"

PLAYBOOK_ID = uuid4()

CLEAN_HEADROOM = {
    "max_volume_mbd": 1.0,
    "take_or_pay_floor": 0.0,
    "current_volume_mbd": 0.0,
}

CLEAN_PORT = {"max_vessel_dwt": 320_000}


def _candidate(**overrides):
    base = {
        "option_id": "test_001",
        "supplier": "UAE",
        "grade": "Murban",
        "refinery": "Jamnagar RIL",
        "arrival_port": "Vadinar",
        "departure_port": "Fujairah",
        "vessel_class": "VLCC",
        "proposed_volume_mbd": 0.10,
        "confidence": 0.90,
    }
    base.update(overrides)
    return base


def _patched(**kw):
    """Context manager stack with sane defaults, overridable per-test."""
    defaults = dict(
        ofac=False,
        grade_compat=True,
        share=0.0,
        headroom=CLEAN_HEADROOM,
        port=CLEAN_PORT,
        vessels=2,
    )
    defaults.update(kw)
    return (
        patch(PATCH_OFAC, return_value=defaults["ofac"]),
        patch(PATCH_GRADE, return_value=defaults["grade_compat"]),
        patch(PATCH_SHARE, return_value=defaults["share"]),
        patch(PATCH_HEADROOM, return_value=defaults["headroom"]),
        patch(PATCH_PORT, return_value=defaults["port"]),
        patch(PATCH_VESSELS, return_value=defaults["vessels"]),
        patch(PATCH_INSERT, return_value=None),
    )


def _validate(candidate, tracker=None, **kw):
    """
    Defaults to a FRESH DiversificationTracker per call so single-candidate
    tests are isolated from each other. Tests that specifically exercise
    shared-tracker behavior (Fix 10 sequencing) pass their own tracker
    explicitly across two calls.
    """
    from agents.agent7 import validator, new_diversification_tracker

    if tracker is None:
        tracker = new_diversification_tracker()

    p1, p2, p3, p4, p5, p6, p7 = _patched(**kw)
    with p1, p2, p3, p4, p5, p6, p7:
        return validator(candidate, playbook_id=PLAYBOOK_ID, tracker=tracker)


def _validate_batch(candidates, **kw):
    from agents.agent7 import validate_batch

    p1, p2, p3, p4, p5, p6, p7 = _patched(**kw)
    with p1, p2, p3, p4, p5, p6, p7:
        return validate_batch(candidates, playbook_id=PLAYBOOK_ID)


# ---------------------------------------------------------------------------
# Layer 1 — Sanctions
# ---------------------------------------------------------------------------

class TestLayer1Sanctions:
    def test_ofac_match_blocks(self):
        result = _validate(_candidate(supplier="Iran"), ofac=True)
        assert result["status"] == "BLOCKED"
        assert result["reason"]["rule"] == "OFAC_SDN"

    def test_no_match_passes_layer1(self):
        result = _validate(_candidate(supplier="UAE"), ofac=False)
        assert result["reason"] is None or result["reason"].get("rule") != "OFAC_SDN"


# ---------------------------------------------------------------------------
# Layer 2 — Grade compatibility
# ---------------------------------------------------------------------------

class TestLayer2GradeCompatibility:
    def test_incompatible_grade_blocks(self):
        result = _validate(
            _candidate(refinery="Kochi BPCL", grade="Urals"),
            grade_compat=False,
        )
        assert result["status"] == "BLOCKED"
        assert result["reason"]["rule"] == "GRADE_INCOMPATIBLE"

    def test_compatible_grade_passes_layer2(self):
        result = _validate(_candidate(), grade_compat=True)
        assert result["status"] != "BLOCKED" or result["reason"]["rule"] != "GRADE_INCOMPATIBLE"


# ---------------------------------------------------------------------------
# Layer 3 — Diversification cap (Fix 10)
# ---------------------------------------------------------------------------

class TestLayer3Diversification:
    def test_within_cap_approved(self):
        # 5.1 mbd consumption baseline; 0.10 mbd proposed => ~2% delta share,
        # well under the 40% cap with 0% current share.
        result = _validate(_candidate(proposed_volume_mbd=0.10), share=0.0)
        assert result["status"] != "BLOCKED"

    def test_exceeding_cap_blocks_or_partials(self):
        # current share already at 39%, proposing a huge volume that would
        # blow past the 40% cap.
        result = _validate(
            _candidate(supplier="Russia", proposed_volume_mbd=5.0),
            share=0.39,
        )
        assert result["status"] in ("BLOCKED", "PARTIAL")
        assert result["reason"]["rule"] == "DIVERSIFICATION_CAP"

    def test_sequential_not_parallel_race_condition(self):
        """
        Fix 10: two simultaneous recommendations for the same supplier, each
        1.5% of daily consumption, with only 2% headroom left before the 40%
        cap. Validated one after another against a SHARED tracker (as
        validate_batch does): the first must be approved in full and consume
        the headroom; the second must see the reduced headroom and be
        blocked or partially approved — never both approved in full.
        """
        from agents.agent7 import new_diversification_tracker

        tracker = new_diversification_tracker()

        # current_share is loaded once per supplier the first time it's seen,
        # so both calls must run under the same patched value (38% current,
        # 40% cap => 2% headroom).
        cand_a = _candidate(option_id="race_a", supplier="Russia", proposed_volume_mbd=0.0765)  # ~1.5% of 5.1
        cand_b = _candidate(option_id="race_b", supplier="Russia", proposed_volume_mbd=0.0765)

        result_a = _validate(cand_a, tracker=tracker, share=0.38)
        result_b = _validate(cand_b, tracker=tracker, share=0.38)

        statuses = {result_a["status"], result_b["status"]}
        assert not (result_a["status"] == "APPROVED" and result_b["status"] == "APPROVED"), (
            "Both recommendations were approved in full against the same "
            "starting share — Fix 10 sequential validation was not applied."
        )
        assert "BLOCKED" in statuses or "PARTIAL" in statuses

    def test_running_share_updates_between_calls(self):
        from agents.agent7 import new_diversification_tracker

        tracker = new_diversification_tracker()
        _validate(_candidate(supplier="Russia", proposed_volume_mbd=0.10), tracker=tracker, share=0.0)
        assert tracker.running_share["Russia"] > 0.0


# ---------------------------------------------------------------------------
# Layer 4 — Operational (port, tankers, contract ceiling, take-or-pay)
# ---------------------------------------------------------------------------

class TestLayer4Operational:
    def test_port_capacity_exceeded_blocks(self):
        result = _validate(
            _candidate(vessel_class="VLCC"),
            port={"max_vessel_dwt": 150_000},  # smaller than VLCC's 320,000
        )
        assert result["status"] == "BLOCKED"
        assert result["reason"]["rule"] == "PORT_CAPACITY"

    def test_no_tankers_available_blocks(self):
        result = _validate(_candidate(), vessels=0)
        assert result["status"] == "BLOCKED"
        assert result["reason"]["rule"] == "TANKER_UNAVAILABLE"

    def test_contract_ceiling_exceeded_blocks(self):
        result = _validate(
            _candidate(proposed_volume_mbd=2.0),
            headroom={"max_volume_mbd": 1.0, "take_or_pay_floor": 0.0, "current_volume_mbd": 0.5},
        )
        assert result["status"] == "BLOCKED"
        assert result["reason"]["rule"] == "CONTRACT_CEILING"

    def test_below_take_or_pay_floor_partial(self):
        result = _validate(
            _candidate(proposed_volume_mbd=0.05),
            headroom={"max_volume_mbd": 1.0, "take_or_pay_floor": 0.20, "current_volume_mbd": 0.0},
        )
        assert result["status"] == "PARTIAL"
        assert result["reason"]["rule"] == "TAKE_OR_PAY_PENALTY"


# ---------------------------------------------------------------------------
# Fully clean candidate
# ---------------------------------------------------------------------------

class TestApproved:
    def test_clean_candidate_is_approved(self):
        result = _validate(_candidate())
        assert result["status"] == "APPROVED"
        assert result["reason"]["rule"] == "ALL_LAYERS_PASSED"


# ---------------------------------------------------------------------------
# validate_batch — ordering + shared tracker
# ---------------------------------------------------------------------------

class TestValidateBatch:
    def test_sorted_by_confidence_descending(self):
        candidates = [
            _candidate(option_id="low", confidence=0.5, supplier="UAE"),
            _candidate(option_id="high", confidence=0.95, supplier="Saudi Arabia"),
        ]
        results = _validate_batch(candidates, share=0.0)
        assert [r["option_id"] for r in results] == ["high", "low"]

    def test_batch_shares_one_tracker_for_diversification(self):
        # Same setup as the race-condition test, but through the real
        # validate_batch() entrypoint end-to-end.
        candidates = [
            _candidate(option_id="a", supplier="Russia", confidence=0.9, proposed_volume_mbd=0.0765),
            _candidate(option_id="b", supplier="Russia", confidence=0.8, proposed_volume_mbd=0.0765),
        ]
        results = _validate_batch(candidates, share=0.38)
        statuses = [r["status"] for r in results]
        assert not (statuses[0] == "APPROVED" and statuses[1] == "APPROVED")