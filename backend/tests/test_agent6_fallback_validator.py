# tests/test_agent6_fallback_validator.py
from unittest.mock import patch

import pytest

import agents.agent6_fallback_validator as validator


@pytest.fixture(autouse=True)
def reset_run_state_cache():
    validator._run_state_cache.clear()
    yield
    validator._run_state_cache.clear()


def make_candidate(supplier="UAE", grade="Murban", refinery="Jamnagar RIL",
                    proposed_volume_mbd=0.15, vessel_class="VLCC", arrival_port=""):
    return {
        "supplier": supplier,
        "grade": grade,
        "refinery": refinery,
        "proposed_volume_mbd": proposed_volume_mbd,
        "vessel_class": vessel_class,
        "arrival_port": arrival_port,
    }


class TestLayer1Sanctions:
    @pytest.mark.asyncio
    async def test_sanctioned_supplier_is_blocked(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.0):
            result = await validator.validate_candidate(make_candidate(supplier="Iran"))
        assert result["status"] == "BLOCKED"
        assert result["reason"]["rule"] == "OFAC_SDN"
        assert result["adjusted_volume_mbd"] == 0.0

    @pytest.mark.asyncio
    async def test_ofac_check_failure_fails_open_not_blocked(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", side_effect=Exception("OFAC down")), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.0):
            result = await validator.validate_candidate(make_candidate())
        assert result["status"] != "BLOCKED" or result["reason"]["rule"] != "OFAC_SDN"

    @pytest.mark.asyncio
    async def test_ofac_takes_priority_over_grade_incompatibility(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=True), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=False), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.0):
            result = await validator.validate_candidate(make_candidate(supplier="Iran"))
        assert result["reason"]["rule"] == "OFAC_SDN"


class TestLayer2GradeCompatibility:
    @pytest.mark.asyncio
    async def test_incompatible_grade_is_blocked(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=False), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.0):
            result = await validator.validate_candidate(make_candidate(grade="Urals", refinery="Jamnagar RIL"))
        assert result["status"] == "BLOCKED"
        assert result["reason"]["rule"] == "GRADE_INCOMPATIBLE"

    @pytest.mark.asyncio
    async def test_grade_check_failure_fails_open(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", side_effect=Exception("Neo4j down")), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.0):
            result = await validator.validate_candidate(make_candidate())
        assert result["status"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_no_refinery_skips_grade_check_entirely(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility") as mock_grade, \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.0):
            result = await validator.validate_candidate(make_candidate(refinery=""))
        mock_grade.assert_not_called()
        assert result["status"] == "APPROVED"


class TestLayer3DiversificationCap:
    @pytest.mark.asyncio
    async def test_within_cap_is_approved_full_volume(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.10):
            result = await validator.validate_candidate(make_candidate(proposed_volume_mbd=0.15))
        assert result["status"] == "APPROVED"
        assert result["adjusted_volume_mbd"] == 0.15

    @pytest.mark.asyncio
    async def test_already_at_cap_is_blocked(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.40):
            result = await validator.validate_candidate(make_candidate(proposed_volume_mbd=0.15))
        assert result["status"] == "BLOCKED"
        assert result["reason"]["rule"] == "DIVERSIFICATION_CAP"

    @pytest.mark.asyncio
    async def test_partial_headroom_returns_partial_status_with_adjusted_volume(self):
        # current_share=0.35, MAX=0.40 -> headroom=0.05 share = 0.255 mbd
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.35):
            result = await validator.validate_candidate(make_candidate(proposed_volume_mbd=1.0))
        assert result["status"] == "PARTIAL"
        assert result["reason"]["rule"] == "DIVERSIFICATION_CAP"
        expected_volume = 0.05 * 5.1
        assert result["adjusted_volume_mbd"] == pytest.approx(expected_volume, abs=0.001)

    @pytest.mark.asyncio
    async def test_sequential_cap_fix10_second_candidate_sees_updated_share(self):
        """
        Fix 10: diversification cap must be SEQUENTIAL within a single
        run — approving candidate 1 must update run_state so candidate 2
        for the same supplier sees the increased share, not the stale
        Neo4j baseline.
        """
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.30):
            result1 = await validator.validate_candidate(
                make_candidate(supplier="UAE", proposed_volume_mbd=0.4), playbook_id="pb-1"
            )
            assert result1["status"] == "APPROVED"

            result2 = await validator.validate_candidate(
                make_candidate(supplier="UAE", proposed_volume_mbd=0.4), playbook_id="pb-1"
            )
            assert result2["status"] in ("PARTIAL", "BLOCKED")

    @pytest.mark.asyncio
    async def test_different_playbook_ids_have_isolated_run_state(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.35):
            result_a = await validator.validate_candidate(
                make_candidate(supplier="UAE", proposed_volume_mbd=0.2), playbook_id="pb-A"
            )
            result_b = await validator.validate_candidate(
                make_candidate(supplier="UAE", proposed_volume_mbd=0.2), playbook_id="pb-B"
            )
        assert result_a["status"] == "APPROVED"
        assert result_b["status"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_current_share_fetch_failure_defaults_to_zero(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", side_effect=Exception("Neo4j down")):
            result = await validator.validate_candidate(make_candidate(proposed_volume_mbd=0.1))
        assert result["status"] == "APPROVED"


class TestLayer4OperationalChecks:
    @pytest.mark.asyncio
    async def test_port_too_small_for_vlcc_is_blocked(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.0), \
             patch("agents.agent6_fallback_validator.get_port_specs", return_value={"max_vessel_dwt": 150000}):
            result = await validator.validate_candidate(
                make_candidate(vessel_class="VLCC", arrival_port="Kochi")
            )
        assert result["status"] == "BLOCKED"
        assert result["reason"]["rule"] == "PORT_CAPACITY"

    @pytest.mark.asyncio
    async def test_suezmax_fits_smaller_port_than_vlcc(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.0), \
             patch("agents.agent6_fallback_validator.get_port_specs", return_value={"max_vessel_dwt": 200000}):
            result = await validator.validate_candidate(
                make_candidate(vessel_class="Suezmax", arrival_port="Kochi")
            )
        assert result["status"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_no_arrival_port_skips_operational_check(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.0), \
             patch("agents.agent6_fallback_validator.get_port_specs") as mock_port:
            result = await validator.validate_candidate(make_candidate(arrival_port=""))
        mock_port.assert_not_called()
        assert result["status"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_port_specs_fetch_failure_fails_open(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.0), \
             patch("agents.agent6_fallback_validator.get_port_specs", side_effect=Exception("Neo4j down")):
            result = await validator.validate_candidate(
                make_candidate(vessel_class="VLCC", arrival_port="Kochi")
            )
        assert result["status"] == "APPROVED"


class TestShareVolumeConversion:
    def test_volume_to_share_and_back_round_trips(self):
        volume = 0.5
        share = validator._volume_to_share(volume)
        recovered = validator._share_to_volume(share)
        assert recovered == pytest.approx(volume, abs=1e-6)

    def test_zero_total_daily_consumption_returns_zero_share(self):
        assert validator._volume_to_share(0.5, total_daily_mbd=0.0) == 0.0


class TestFullApprovalPath:
    @pytest.mark.asyncio
    async def test_all_layers_pass_returns_approved_with_unmodified_volume(self):
        with patch("agents.agent6_fallback_validator.check_ofac_match", return_value=False), \
             patch("agents.agent6_fallback_validator.check_grade_compatibility", return_value=True), \
             patch("agents.agent6_fallback_validator.get_supplier_current_share", return_value=0.05), \
             patch("agents.agent6_fallback_validator.get_port_specs", return_value={"max_vessel_dwt": 350000}):
            result = await validator.validate_candidate(
                make_candidate(proposed_volume_mbd=0.2, arrival_port="Vadinar")
            )
        assert result["status"] == "APPROVED"
        assert result["reason"] is None
        assert result["adjusted_volume_mbd"] == 0.2