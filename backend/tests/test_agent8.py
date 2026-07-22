# tests/test_agent8.py
import json
import math
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agents.agent8 as agent8


def make_procurement_result(evaluated=5, approved=2, partial=1, blocked=2, ranked=None, trace=None, blocked_cps=None):
    return {
        "evaluated_count": evaluated,
        "approved_count": approved,
        "partial_count": partial,
        "blocked_count": blocked,
        "ranked_options": ranked or [{"supplier": "UAE", "adjusted_volume_mbd": 0.3, "confidence": 0.9}],
        "full_rejection_trace": trace or [{"supplier": "Iran", "status": "BLOCKED", "reason": {"rule": "OFAC_SDN"}}],
        "blocked_chokepoints": blocked_cps or ["Hormuz"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def make_spr_result(feasible=True, confidence=0.8, critical_warning=None):
    return {
        "feasible": feasible,
        "daily_drawdown_schedule_mbd": [0.3] * 30,
        "spr_remaining_mb": 20.0,
        "confidence": confidence,
        "critical_warning": critical_warning,
        "inputs_used": {},
    }


def make_simulation_result():
    return {
        "disruption": {"import_gap_mbd": 0.5, "disrupted_share": 0.28, "disrupted_suppliers": ["Iran"]},
        "price": {"new_price_usd": 92.0, "price_delta_pct": 14.0},
        "refineries": [
            {"refinery_name": "Jamnagar RIL", "util_delta_pct": -9.0},
            {"refinery_name": "Vadinar Nayara", "util_delta_pct": -3.0},
        ],
        "meta": {},
    }


class TestCombineConfidence:
    def test_full_approval_and_feasible_spr_gives_high_confidence(self):
        result = agent8._combine_confidence(0.9, make_procurement_result(evaluated=4, approved=4))
        assert result == pytest.approx((0.9 + 1.0) / 2, abs=1e-4)

    def test_zero_evaluated_options_does_not_divide_by_zero(self):
        result = agent8._combine_confidence(0.8, make_procurement_result(evaluated=0, approved=0))
        assert result == pytest.approx((0.8 + 0.0) / 2, abs=1e-4)

    def test_current_implementation_is_arithmetic_not_geometric_mean(self):
        """
        FLAG FOR PERSON B: CLAUDE.md / schedule spec playbook confidence
        as geometric_mean(agent1_confidence, agent3_confidence,
        agent6_top_option_confidence). Current _combine_confidence()
        instead averages spr_confidence with the procurement
        approved/evaluated RATIO — a different formula with different
        inputs entirely (no Agent 1 or Agent 3 confidence used at all).
        This test documents the actual behavior; if the formula gets
        corrected to match spec, this test should be rewritten to
        assert the geometric mean instead.
        """
        spr_confidence = 0.6
        procurement_result = make_procurement_result(evaluated=10, approved=3)
        result = agent8._combine_confidence(spr_confidence, procurement_result)
        arithmetic_expected = (spr_confidence + 0.3) / 2
        assert result == pytest.approx(arithmetic_expected, abs=1e-4)


class TestDetermineStatus:
    def test_infeasible_spr_and_zero_approved_is_critical(self):
        status = agent8._determine_status(
            make_spr_result(feasible=False), make_procurement_result(approved=0)
        )
        assert status == "CRITICAL"

    def test_infeasible_spr_with_some_approved_is_degraded(self):
        status = agent8._determine_status(
            make_spr_result(feasible=False), make_procurement_result(approved=2)
        )
        assert status == "DEGRADED"

    def test_more_blocked_than_approved_is_degraded(self):
        status = agent8._determine_status(
            make_spr_result(feasible=True), make_procurement_result(approved=1, blocked=3)
        )
        assert status == "DEGRADED"

    def test_feasible_spr_and_approved_gte_blocked_is_nominal(self):
        status = agent8._determine_status(
            make_spr_result(feasible=True), make_procurement_result(approved=3, blocked=2)
        )
        assert status == "NOMINAL"


class TestThresholdExplainer:
    def test_chokepoint_above_crisis_threshold_flags_full_crisis(self):
        explainers = agent8._build_threshold_explainer(
            ["Hormuz"], {"Hormuz": 0.82}
        )
        assert len(explainers) == 1
        assert "full crisis" in explainers[0]

    def test_chokepoint_between_route_survival_and_crisis_thresholds(self):
        explainers = agent8._build_threshold_explainer(
            ["Red_Sea"], {"Red_Sea": 0.50}
        )
        assert len(explainers) == 1
        assert "not yet a compound crisis" in explainers[0]

    def test_chokepoint_below_route_survival_threshold_produces_no_explainer(self):
        explainers = agent8._build_threshold_explainer(
            ["Suez"], {"Suez": 0.20}
        )
        assert explainers == []

    def test_boolean_value_in_risk_vector_is_skipped_not_miscounted(self):
        # Same boolean-leak class of bug as Agent 3's _is_numeric_score
        explainers = agent8._build_threshold_explainer(
            ["Cape"], {"Cape": True}
        )
        assert explainers == []

    def test_non_numeric_metadata_keys_are_skipped(self):
        explainers = agent8._build_threshold_explainer(
            ["Hormuz", "updated_at"], {"Hormuz": 0.82, "updated_at": "2026-07-22T10:00:00"}
        )
        assert len(explainers) == 1


class TestBuildMinistryView:
    def test_escalate_emergency_when_no_approvals_and_infeasible_spr(self):
        view = agent8._build_ministry_view(
            ["Strait of Hormuz"], 1.0, make_simulation_result(),
            make_spr_result(feasible=False), make_procurement_result(approved=0)
        )
        assert view["recommended_posture"] == "ESCALATE_EMERGENCY_RATIONING"

    def test_monitor_and_execute_when_approved_and_feasible(self):
        view = agent8._build_ministry_view(
            ["Strait of Hormuz"], 1.0, make_simulation_result(),
            make_spr_result(feasible=True), make_procurement_result(approved=2)
        )
        assert view["recommended_posture"] == "MONITOR_AND_EXECUTE"

    def test_critical_warning_surfaces_when_spr_infeasible(self):
        spr = make_spr_result(feasible=False, critical_warning="Emergency rationing required.")
        view = agent8._build_ministry_view(
            ["Strait of Hormuz"], 1.0, make_simulation_result(), spr, make_procurement_result()
        )
        assert view["critical_warning"] == "Emergency rationing required."

    def test_includes_disrupted_suppliers_from_simulation(self):
        view = agent8._build_ministry_view(
            ["Strait of Hormuz"], 1.0, make_simulation_result(),
            make_spr_result(), make_procurement_result()
        )
        assert view["disrupted_suppliers"] == ["Iran"]


class TestBuildProcurementView:
    def test_includes_threshold_explainer_using_original_risk_vector(self):
        procurement_result = make_procurement_result(blocked_cps=["Hormuz"])
        view = agent8._build_procurement_view(procurement_result, risk_vector={"Hormuz": 0.82})
        assert len(view["threshold_explainer"]) == 1

    def test_missing_risk_vector_defaults_to_empty_no_crash(self):
        procurement_result = make_procurement_result(blocked_cps=["Hormuz"])
        view = agent8._build_procurement_view(procurement_result, risk_vector=None)
        assert view["threshold_explainer"] == []

    def test_top_options_capped_at_five(self):
        ranked = [{"supplier": f"S{i}", "confidence": 0.5} for i in range(10)]
        procurement_result = make_procurement_result(ranked=ranked)
        view = agent8._build_procurement_view(procurement_result)
        assert len(view["top_options"]) == 5

    def test_blocked_summary_only_includes_blocked_status(self):
        trace = [
            {"supplier": "Iran", "status": "BLOCKED", "reason": {"rule": "OFAC_SDN"}},
            {"supplier": "UAE", "status": "APPROVED", "reason": {}},
        ]
        procurement_result = make_procurement_result(trace=trace)
        view = agent8._build_procurement_view(procurement_result)
        assert len(view["blocked_summary"]) == 1
        assert view["blocked_summary"][0]["supplier"] == "Iran"


class TestBuildRefineryView:
    def test_identifies_highest_risk_refinery_by_max_utilization_loss(self):
        view = agent8._build_refinery_view(make_simulation_result())
        assert view["highest_risk_refinery"] == "Jamnagar RIL"
        assert view["max_utilization_loss_pct"] == 9.0

    def test_refineries_with_error_key_are_excluded_from_max_calc(self):
        sim_result = {
            "refineries": [
                {"refinery_name": "Kochi BPCL", "error": "no data"},
                {"refinery_name": "Paradip IOCL", "util_delta_pct": -5.0},
            ]
        }
        view = agent8._build_refinery_view(sim_result)
        assert view["highest_risk_refinery"] == "Paradip IOCL"


class TestSupplierRouteRisks:
    def test_supplier_on_surviving_route_is_excluded_even_if_route_touches_affected_chokepoint(self):
        with patch("db.neo4j_queries.get_surviving_routes", return_value=[{"supplier": "Saudi Arabia"}]), \
             patch("db.neo4j_queries.get_supplier_route_chokepoints", return_value={
                 "Saudi Arabia": ["Hormuz", "Cape"],
                 "Iran": ["Hormuz"],
             }), \
             patch("db.neo4j_queries.get_supplier_current_share", return_value=0.05):
            result = agent8._build_supplier_route_risks(["Strait of Hormuz"], 1.0)
            suppliers = {r["supplier"] for r in result}
            assert "Saudi Arabia" not in suppliers
            assert "Iran" in suppliers

    def test_supplier_with_no_route_data_is_never_disrupted(self):
        with patch("db.neo4j_queries.get_surviving_routes", return_value=[]), \
             patch("db.neo4j_queries.get_supplier_route_chokepoints", return_value={}), \
             patch("db.neo4j_queries.get_supplier_current_share", return_value=0.0):
            result = agent8._build_supplier_route_risks(["Strait of Hormuz"], 1.0)
            assert result == []

    def test_neo4j_failure_returns_empty_list_not_crash(self):
        with patch("db.neo4j_queries.get_surviving_routes", side_effect=Exception("Neo4j down")):
            result = agent8._build_supplier_route_risks(["Strait of Hormuz"], 1.0)
            assert result == []


class TestNonDestructiveRiskStateInjection:
    @pytest.mark.asyncio
    async def test_restores_original_risk_state_after_injection(self):
        original = {"Hormuz": 0.34, "Red_Sea": 0.41}
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(return_value=json.dumps(original))
        fake_redis.set = AsyncMock()
        fake_redis.delete = AsyncMock()

        with patch("agents.agent8.get_redis", AsyncMock(return_value=fake_redis)):
            snapshot = await agent8._snapshot_risk_state()
            assert snapshot == original

            await agent8._restore_risk_state(snapshot)
            fake_redis.set.assert_called_with("risk:state", json.dumps(original))

    @pytest.mark.asyncio
    async def test_no_prior_state_deletes_override_on_restore(self):
        fake_redis = AsyncMock()
        fake_redis.delete = AsyncMock()

        with patch("agents.agent8.get_redis", AsyncMock(return_value=fake_redis)):
            await agent8._restore_risk_state(None)
            fake_redis.delete.assert_called_with("risk:state")


class TestRunAgent8Integration:
    @pytest.mark.asyncio
    async def test_agent6_failure_falls_back_to_empty_procurement_and_restores_risk_state(self):
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(return_value=json.dumps({"Hormuz": 0.34}))
        fake_redis.set = AsyncMock()

        with patch("agents.agent8.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent8.run_simulation", return_value=make_simulation_result()), \
             patch("agents.agent8.run_agent6", AsyncMock(side_effect=Exception("Agent 6 crashed"))), \
             patch("agents.agent8.run_agent5", return_value={"spr_schedule": make_spr_result()}), \
             patch("agents.agent8.insert_playbook", return_value="pb-123"), \
             patch("db.neo4j_queries.get_surviving_routes", return_value=[]), \
             patch("db.neo4j_queries.get_supplier_route_chokepoints", return_value={}):
            result = await agent8.run_agent8(["Strait of Hormuz"])
            assert result["procurement_view"]["evaluated_count"] == 0
            # Restore must still be called even on Agent 6 failure
            fake_redis.set.assert_called_with("risk:state", json.dumps({"Hormuz": 0.34}))

    @pytest.mark.asyncio
    async def test_insert_playbook_failure_does_not_crash_run(self):
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.set = AsyncMock()
        fake_redis.delete = AsyncMock()

        with patch("agents.agent8.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent8.run_simulation", return_value=make_simulation_result()), \
             patch("agents.agent8.run_agent6", AsyncMock(return_value=make_procurement_result())), \
             patch("agents.agent8.run_agent5", return_value={"spr_schedule": make_spr_result()}), \
             patch("agents.agent8.insert_playbook", side_effect=Exception("DB down")), \
             patch("db.neo4j_queries.get_surviving_routes", return_value=[]), \
             patch("db.neo4j_queries.get_supplier_route_chokepoints", return_value={}):
            result = await agent8.run_agent8(["Strait of Hormuz"])
            assert result["playbook_id"] is None
            assert "ministry_view" in result

    @pytest.mark.asyncio
    async def test_compound_severity_dict_normalized_correctly_per_chokepoint(self):
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.set = AsyncMock()
        fake_redis.delete = AsyncMock()

        with patch("agents.agent8.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent8.run_simulation") as mock_sim, \
             patch("agents.agent8.run_agent6", AsyncMock(return_value=make_procurement_result())), \
             patch("agents.agent8.run_agent5", return_value={"spr_schedule": make_spr_result()}), \
             patch("agents.agent8.insert_playbook", return_value="pb-1"), \
             patch("db.neo4j_queries.get_surviving_routes", return_value=[]), \
             patch("db.neo4j_queries.get_supplier_route_chokepoints", return_value={}):
            mock_sim.return_value = make_simulation_result()
            severity = {"Strait of Hormuz": 1.0, "Bab-el-Mandeb": 0.5}
            await agent8.run_agent8(["Strait of Hormuz", "Bab-el-Mandeb"], closure_severity=severity)
            _, kwargs = mock_sim.call_args
            assert kwargs["closure_severity"] == severity