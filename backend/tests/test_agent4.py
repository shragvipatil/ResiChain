"""
tests/test_agent4.py

Test suite for Agent 4 (Compound Disruption Analyzer) — agents/agent4.py.

Covers the compound-risk formula, the boolean-leak regression (bool is a
subclass of int in Python), the Day-18 risk_vector-from-state filter fix,
and the surviving-routes Neo4j integration for compound events.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch

import agents.agent4 as agent4


def make_fake_redis(risk_state=None):
    redis_mock = AsyncMock()

    async def fake_get(key):
        if key == "risk:state" and risk_state is not None:
            return json.dumps(risk_state)
        return None

    redis_mock.get.side_effect = fake_get
    return redis_mock


class TestIsNumericScore:

    def test_float_is_numeric(self):
        assert agent4._is_numeric_score(0.82) is True

    def test_int_is_numeric(self):
        assert agent4._is_numeric_score(1) is True

    def test_bool_is_not_numeric(self):
        assert agent4._is_numeric_score(True) is False
        assert agent4._is_numeric_score(False) is False

    def test_string_is_not_numeric(self):
        assert agent4._is_numeric_score("2026-07-18") is False

    def test_list_is_not_numeric(self):
        assert agent4._is_numeric_score(["Hormuz"]) is False


class TestAnalyzeCore:

    def test_single_corridor_above_threshold_not_compound(self):
        result = agent4._analyze({"Hormuz": 0.82})
        assert result["is_compound_event"] is False
        assert result["compound_risk"] is None
        assert result["blocked_chokepoints"] == ["Hormuz"]

    def test_zero_corridors_above_threshold(self):
        result = agent4._analyze({"Hormuz": 0.20, "Suez": 0.10})
        assert result["is_compound_event"] is False
        assert result["blocked_chokepoints"] == []

    def test_two_corridors_above_threshold_is_compound(self):
        with patch("agents.agent4.get_surviving_routes", return_value=[{"supplier": "Iraq"}]):
            result = agent4._analyze({"Hormuz": 0.82, "Red_Sea": 0.87})
        assert result["is_compound_event"] is True
        assert set(result["blocked_chokepoints"]) == {"Hormuz", "Red_Sea"}

    def test_compound_risk_formula_exact(self):
        # 1 - (1-0.82)(1-0.87) = 1 - (0.18 * 0.13) = 0.9766
        with patch("agents.agent4.get_surviving_routes", return_value=[]):
            result = agent4._analyze({"Hormuz": 0.82, "Red_Sea": 0.87})
        assert result["compound_risk"] == pytest.approx(0.9766, abs=1e-4)

    def test_three_corridor_compound_event(self):
        with patch("agents.agent4.get_surviving_routes", return_value=[]):
            result = agent4._analyze({"Hormuz": 0.82, "Red_Sea": 0.87, "Suez": 0.70})
        assert result["is_compound_event"] is True
        assert len(result["blocked_chokepoints"]) == 3
        # 1 - (0.18 * 0.13 * 0.30) = 1 - 0.00702 = 0.99298
        assert result["compound_risk"] == pytest.approx(0.99298, abs=1e-4)

    def test_bool_value_excluded_from_compound_calc(self):
        """Regression: a boolean scenario_override marker must not be
        treated as a corridor risk score even if it's truthy/1-like."""
        with patch("agents.agent4.get_surviving_routes", return_value=[]):
            result = agent4._analyze({"Hormuz": 0.82, "Red_Sea": 0.87, "scenario_override": True})
        assert result["is_compound_event"] is True
        assert "scenario_override" not in result["blocked_chokepoints"]
        assert result["compound_risk"] == pytest.approx(0.9766, abs=1e-4)

    def test_no_surviving_routes_returns_empty_list_not_crash(self):
        with patch("agents.agent4.get_surviving_routes", return_value=[]):
            result = agent4._analyze({"Hormuz": 0.90, "Red_Sea": 0.90})
        assert result["surviving_routes"] == []
        assert result["is_compound_event"] is True

    def test_neo4j_failure_does_not_crash_analyze(self):
        with patch("agents.agent4.get_surviving_routes", side_effect=Exception("Neo4j down")):
            result = agent4._analyze({"Hormuz": 0.90, "Red_Sea": 0.90})
        assert result["surviving_routes"] == []
        assert result["is_compound_event"] is True

    def test_empty_risk_vector_returns_no_compound(self):
        result = agent4._analyze({})
        assert result["is_compound_event"] is False
        assert result["blocked_chokepoints"] == []


class TestGetRiskVector:

    @pytest.mark.asyncio
    async def test_filters_non_numeric_keys(self):
        risk_state = {"Hormuz": 0.82, "updated_at": "2026-07-18", "updated_corridors": ["Hormuz"]}
        fake_redis = make_fake_redis(risk_state=risk_state)
        with patch("agents.agent4.get_redis", AsyncMock(return_value=fake_redis)):
            vector = await agent4._get_risk_vector()
        assert vector == {"Hormuz": 0.82}

    @pytest.mark.asyncio
    async def test_filters_boolean_scenario_override(self):
        risk_state = {"Hormuz": 0.82, "scenario_override": True}
        fake_redis = make_fake_redis(risk_state=risk_state)
        with patch("agents.agent4.get_redis", AsyncMock(return_value=fake_redis)):
            vector = await agent4._get_risk_vector()
        assert vector == {"Hormuz": 0.82}

    @pytest.mark.asyncio
    async def test_empty_state_returns_empty_dict(self):
        fake_redis = make_fake_redis(risk_state=None)
        with patch("agents.agent4.get_redis", AsyncMock(return_value=fake_redis)):
            vector = await agent4._get_risk_vector()
        assert vector == {}

    @pytest.mark.asyncio
    async def test_redis_failure_returns_empty_dict_not_exception(self):
        broken_redis = AsyncMock()
        broken_redis.get.side_effect = Exception("Redis connection refused")
        with patch("agents.agent4.get_redis", AsyncMock(return_value=broken_redis)):
            vector = await agent4._get_risk_vector()
        assert vector == {}


class TestRunAgent4Analysis:

    @pytest.mark.asyncio
    async def test_standalone_entrypoint_reads_redis_and_analyzes(self):
        risk_state = {"Hormuz": 0.82, "Red_Sea": 0.87}
        fake_redis = make_fake_redis(risk_state=risk_state)
        with patch("agents.agent4.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent4.get_surviving_routes", return_value=[{"supplier": "Iraq"}]):
            result = await agent4.run_agent4_analysis()
        assert result["is_compound_event"] is True
        assert result["compound_risk"] == pytest.approx(0.9766, abs=1e-4)


class TestRunAgent4GraphNode:

    @pytest.mark.asyncio
    async def test_falls_back_to_redis_when_state_has_no_risk_vector(self):
        risk_state = {"Hormuz": 0.82, "Red_Sea": 0.87}
        fake_redis = make_fake_redis(risk_state=risk_state)
        with patch("agents.agent4.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent4.get_surviving_routes", return_value=[]):
            result = await agent4.run_agent4({})
        assert result["is_compound_event"] is True
        assert result["compound_risk"] == pytest.approx(0.9766, abs=1e-4)

    @pytest.mark.asyncio
    async def test_uses_risk_vector_already_in_state_without_redis_call(self):
        state = {"risk_vector": {"Hormuz": 0.82, "Red_Sea": 0.87}}
        with patch("agents.agent4.get_surviving_routes", return_value=[]):
            result = await agent4.run_agent4(state)
        assert result["is_compound_event"] is True
        assert result["risk_vector"] == state["risk_vector"]

    @pytest.mark.asyncio
    async def test_day18_fix_filters_non_numeric_from_state_risk_vector(self):
        """Regression: risk_vector passed directly via /api/crisis/trigger
        was NOT filtered the same way as the Redis-fetch path, causing a
        TypeError when non-numeric keys hit the >= comparison."""
        state = {
            "risk_vector": {
                "Hormuz": 0.82, "Red_Sea": 0.87,
                "updated_at": "2026-07-18", "updated_corridors": ["Hormuz"],
                "scenario_override": True,
            }
        }
        with patch("agents.agent4.get_surviving_routes", return_value=[]):
            result = await agent4.run_agent4(state)
        assert result["is_compound_event"] is True
        assert result["compound_risk"] == pytest.approx(0.9766, abs=1e-4)

    @pytest.mark.asyncio
    async def test_preserves_other_state_keys(self):
        state = {"risk_vector": {"Hormuz": 0.30}, "playbook_id": "abc123"}
        with patch("agents.agent4.get_surviving_routes", return_value=[]):
            result = await agent4.run_agent4(state)
        assert result["playbook_id"] == "abc123"
        assert result["is_compound_event"] is False