"""
tests/test_agent3.py

Test suite for Agent 3 (Corridor Risk Engine) — agents/agent3_risk_engine.py.

Covers the 5-factor weighted risk formula, Fix 8 (risk capped at 1.0),
the boolean-leak regression in _determine_system_mode, the demo:risk_freeze
guard (Day 12 race condition), and graceful degradation on Redis/Postgres
failures.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import agents.agent3_risk_engine as agent3


def make_fake_redis(xread_results=None, brent_data=None, freeze=None):
    redis_mock = AsyncMock()

    async def fake_get(key):
        if key == "brent:price:latest" and brent_data is not None:
            return json.dumps(brent_data)
        if key == "demo:risk_freeze":
            return freeze
        return None

    redis_mock.get.side_effect = fake_get
    redis_mock.xread = AsyncMock(return_value=xread_results or [])
    redis_mock.xrevrange = AsyncMock(return_value=[])
    redis_mock.setex = AsyncMock(return_value=True)
    return redis_mock


def make_fake_pg_connection(ofac_count=0):
    cursor_mock = MagicMock()
    cursor_mock.execute = MagicMock()
    cursor_mock.fetchone = MagicMock(return_value={"count": ofac_count})
    cursor_mock.__enter__ = MagicMock(return_value=cursor_mock)
    cursor_mock.__exit__ = MagicMock(return_value=False)

    conn_mock = MagicMock()
    conn_mock.cursor = MagicMock(return_value=cursor_mock)
    conn_mock.__enter__ = MagicMock(return_value=conn_mock)
    conn_mock.__exit__ = MagicMock(return_value=False)
    return conn_mock


class TestIsNumericScore:

    def test_float_is_numeric(self):
        assert agent3._is_numeric_score(0.5) is True

    def test_bool_is_not_numeric(self):
        assert agent3._is_numeric_score(True) is False
        assert agent3._is_numeric_score(False) is False

    def test_string_is_not_numeric(self):
        assert agent3._is_numeric_score("2026-07-18") is False


class TestDetermineSystemMode:

    def test_below_watch_threshold_is_normal(self):
        assert agent3._determine_system_mode({"Hormuz": 0.30}) == "NORMAL"

    def test_watch_threshold_boundary(self):
        assert agent3._determine_system_mode({"Hormuz": 0.45}) == "WATCH"

    def test_crisis_threshold_boundary(self):
        assert agent3._determine_system_mode({"Hormuz": 0.65}) == "CRISIS"

    def test_excludes_metadata_keys(self):
        vector = {"Hormuz": 0.30, "updated_at": "2026-07-18", "updated_corridors": ["Hormuz"]}
        assert agent3._determine_system_mode(vector) == "NORMAL"

    def test_boolean_scenario_override_excluded_from_mode_calc(self):
        """Regression: a boolean marker sitting alongside real scores
        must never be picked up by max(scores) as if it were 1.0 risk."""
        vector = {"Hormuz": 0.30, "scenario_override": True}
        assert agent3._determine_system_mode(vector) == "NORMAL"

    def test_empty_vector_returns_normal(self):
        assert agent3._determine_system_mode({}) == "NORMAL"

    def test_max_score_used_not_average(self):
        vector = {"Hormuz": 0.90, "Suez": 0.10}
        assert agent3._determine_system_mode(vector) == "CRISIS"


class TestScoreSeasonalRisk:

    def test_known_corridor_returns_table_value(self):
        with patch("agents.agent3_risk_engine.datetime") as mock_dt:
            mock_dt.utcnow.return_value.month = 7
            score = agent3._score_seasonal_risk("Hormuz")
        assert score == 0.5

    def test_unknown_corridor_returns_zero_default(self):
        with patch("agents.agent3_risk_engine.datetime") as mock_dt:
            mock_dt.utcnow.return_value.month = 7
            score = agent3._score_seasonal_risk("UnknownCorridor")
        assert score == 0.1


class TestScoreMarketVolatility:

    @pytest.mark.asyncio
    async def test_no_price_data_returns_default(self):
        fake_redis = make_fake_redis(brent_data=None)
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)):
            score = await agent3._score_market_volatility()
        assert score == 0.1

    @pytest.mark.asyncio
    async def test_high_change_pct_scales_score(self):
        fake_redis = make_fake_redis(brent_data={"change_pct": 5.0})
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)):
            score = await agent3._score_market_volatility()
        assert score == pytest.approx(0.5, abs=1e-6)

    @pytest.mark.asyncio
    async def test_score_capped_at_1(self):
        fake_redis = make_fake_redis(brent_data={"change_pct": 25.0})
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)):
            score = await agent3._score_market_volatility()
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_redis_failure_returns_default(self):
        broken_redis = AsyncMock()
        broken_redis.get.side_effect = Exception("Redis down")
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=broken_redis)):
            score = await agent3._score_market_volatility()
        assert score == 0.1


class TestScoreSanctionsChange:

    @pytest.mark.asyncio
    async def test_no_new_entries_returns_baseline(self):
        fake_conn = make_fake_pg_connection(ofac_count=0)
        with patch("agents.agent3_risk_engine.get_connection", return_value=fake_conn):
            score = await agent3._score_sanctions_change("Hormuz")
        assert score == pytest.approx(0.1, abs=1e-6)

    @pytest.mark.asyncio
    async def test_new_entries_increase_score(self):
        fake_conn = make_fake_pg_connection(ofac_count=50)
        with patch("agents.agent3_risk_engine.get_connection", return_value=fake_conn):
            score = await agent3._score_sanctions_change("Hormuz")
        assert score == pytest.approx(0.6, abs=1e-6)

    @pytest.mark.asyncio
    async def test_score_capped_at_1(self):
        fake_conn = make_fake_pg_connection(ofac_count=1000)
        with patch("agents.agent3_risk_engine.get_connection", return_value=fake_conn):
            score = await agent3._score_sanctions_change("Hormuz")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_postgres_failure_returns_default(self):
        with patch("agents.agent3_risk_engine.get_connection", side_effect=Exception("PG down")):
            score = await agent3._score_sanctions_change("Hormuz")
        assert score == 0.1


class TestScoreMilitaryIncidents:

    @pytest.mark.asyncio
    async def test_ukmto_source_scores_full_weight(self):
        events = [(b"1-1", {"data": json.dumps({
            "corridor": "Hormuz", "sources_confirming": ["UKMTO"], "max_severity": 10
        })})]
        fake_redis = make_fake_redis()
        fake_redis.xrevrange = AsyncMock(return_value=events)
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)):
            score = await agent3._score_military_incidents("Hormuz")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_gdelt_source_scores_partial_weight(self):
        events = [(b"1-1", {"data": json.dumps({
            "corridor": "Hormuz", "sources_confirming": ["GDELT"], "max_severity": 10
        })})]
        fake_redis = make_fake_redis()
        fake_redis.xrevrange = AsyncMock(return_value=events)
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)):
            score = await agent3._score_military_incidents("Hormuz")
        assert score == pytest.approx(0.8, abs=1e-6)

    @pytest.mark.asyncio
    async def test_different_corridor_events_ignored(self):
        events = [(b"1-1", {"data": json.dumps({
            "corridor": "Suez", "sources_confirming": ["UKMTO"], "max_severity": 10
        })})]
        fake_redis = make_fake_redis()
        fake_redis.xrevrange = AsyncMock(return_value=events)
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)):
            score = await agent3._score_military_incidents("Hormuz")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_redis_failure_returns_zero(self):
        broken_redis = AsyncMock()
        broken_redis.xrevrange = AsyncMock(side_effect=Exception("Redis down"))
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=broken_redis)):
            score = await agent3._score_military_incidents("Hormuz")
        assert score == 0.0


class TestCalculateCorridorRisk:

    @pytest.mark.asyncio
    async def test_risk_capped_at_1_fix8(self):
        """Fix 8 regression: corridor_risk = min(1.0, raw_risk)."""
        with patch("agents.agent3_risk_engine._score_military_incidents", AsyncMock(return_value=1.0)), \
             patch("agents.agent3_risk_engine._score_conflict_escalation", AsyncMock(return_value=1.0)), \
             patch("agents.agent3_risk_engine._score_sanctions_change", AsyncMock(return_value=1.0)), \
             patch("agents.agent3_risk_engine._score_market_volatility", AsyncMock(return_value=1.0)), \
             patch("agents.agent3_risk_engine._score_seasonal_risk", return_value=1.0), \
             patch("agents.agent3_risk_engine._get_days_since_last_event", AsyncMock(return_value=0.0)):
            risk = await agent3._calculate_corridor_risk("Hormuz")
        assert risk <= 1.0

    @pytest.mark.asyncio
    async def test_zero_factors_gives_zero_risk(self):
        with patch("agents.agent3_risk_engine._score_military_incidents", AsyncMock(return_value=0.0)), \
             patch("agents.agent3_risk_engine._score_conflict_escalation", AsyncMock(return_value=0.0)), \
             patch("agents.agent3_risk_engine._score_sanctions_change", AsyncMock(return_value=0.0)), \
             patch("agents.agent3_risk_engine._score_market_volatility", AsyncMock(return_value=0.0)), \
             patch("agents.agent3_risk_engine._score_seasonal_risk", return_value=0.0), \
             patch("agents.agent3_risk_engine._get_days_since_last_event", AsyncMock(return_value=0.0)):
            risk = await agent3._calculate_corridor_risk("Hormuz")
        assert risk == 0.0

    @pytest.mark.asyncio
    async def test_temporal_decay_reduces_old_events(self):
        with patch("agents.agent3_risk_engine._score_military_incidents", AsyncMock(return_value=1.0)), \
             patch("agents.agent3_risk_engine._score_conflict_escalation", AsyncMock(return_value=0.0)), \
             patch("agents.agent3_risk_engine._score_sanctions_change", AsyncMock(return_value=0.0)), \
             patch("agents.agent3_risk_engine._score_market_volatility", AsyncMock(return_value=0.0)), \
             patch("agents.agent3_risk_engine._score_seasonal_risk", return_value=0.0):
            with patch("agents.agent3_risk_engine._get_days_since_last_event", AsyncMock(return_value=0.0)):
                risk_fresh = await agent3._calculate_corridor_risk("Hormuz")
            with patch("agents.agent3_risk_engine._get_days_since_last_event", AsyncMock(return_value=30.0)):
                risk_old = await agent3._calculate_corridor_risk("Hormuz")
        assert risk_old < risk_fresh


class TestGetDaysSinceLastEvent:

    @pytest.mark.asyncio
    async def test_no_matching_event_returns_default_7_days(self):
        fake_redis = make_fake_redis()
        fake_redis.xrevrange = AsyncMock(return_value=[])
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)):
            days = await agent3._get_days_since_last_event("Hormuz")
        assert days == 7.0

    @pytest.mark.asyncio
    async def test_redis_exception_returns_default(self):
        broken_redis = AsyncMock()
        broken_redis.xrevrange = AsyncMock(side_effect=Exception("Redis down"))
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=broken_redis)):
            days = await agent3._get_days_since_last_event("Hormuz")
        assert days == 7.0


class TestRunAgent3Integration:

    @pytest.mark.asyncio
    async def test_writes_risk_vector_for_all_four_corridors(self):
        fake_redis = make_fake_redis()
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent3_risk_engine.update_risk_state", AsyncMock(return_value=True)) as mock_update, \
             patch("agents.agent3_risk_engine._calculate_corridor_risk", AsyncMock(return_value=0.3)), \
             patch("agents.agent3_risk_engine._emit_risk_update", AsyncMock(return_value=None)):
            result = await agent3.run_agent3()

        assert set(["Hormuz", "Red_Sea", "Suez", "Cape"]).issubset(result.keys())
        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_demo_freeze_guard_skips_update_risk_state(self):
        """Day 12 regression: while demo:risk_freeze is set, Agent 3 must
        NOT overwrite risk:state, even though it still computes the vector."""
        fake_redis = make_fake_redis(freeze="1")
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent3_risk_engine.update_risk_state", AsyncMock(return_value=True)) as mock_update, \
             patch("agents.agent3_risk_engine._calculate_corridor_risk", AsyncMock(return_value=0.3)), \
             patch("agents.agent3_risk_engine._emit_risk_update", AsyncMock(return_value=None)):
            result = await agent3.run_agent3()

        mock_update.assert_not_called()
        assert "Hormuz" in result

    @pytest.mark.asyncio
    async def test_no_freeze_calls_update_risk_state(self):
        fake_redis = make_fake_redis(freeze=None)
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent3_risk_engine.update_risk_state", AsyncMock(return_value=True)) as mock_update, \
             patch("agents.agent3_risk_engine._calculate_corridor_risk", AsyncMock(return_value=0.3)), \
             patch("agents.agent3_risk_engine._emit_risk_update", AsyncMock(return_value=None)):
            await agent3.run_agent3()

        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_failure_returns_empty_dict_not_crash(self):
        broken_redis = AsyncMock()
        broken_redis.xread = AsyncMock(side_effect=Exception("Redis connection refused"))
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=broken_redis)):
            result = await agent3.run_agent3()
        assert result == {}

    @pytest.mark.asyncio
    async def test_websocket_broadcast_failure_does_not_crash_run(self):
        fake_redis = make_fake_redis()
        with patch("agents.agent3_risk_engine.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent3_risk_engine.update_risk_state", AsyncMock(return_value=True)), \
             patch("agents.agent3_risk_engine._calculate_corridor_risk", AsyncMock(return_value=0.3)), \
             patch("agents.agent3_risk_engine._emit_risk_update", AsyncMock(side_effect=Exception("WS down"))):
            result = await agent3.run_agent3()
        assert "Hormuz" in result


class TestUpdateRiskWeights:

    @pytest.mark.asyncio
    async def test_updates_global_weights_and_recalculates(self):
        new_weights = {"military_incidents": 0.5, "conflict_escalation": 0.2,
                        "sanctions_change": 0.2, "market_volatility": 0.05, "seasonal_risk": 0.05}
        fake_conn = make_fake_pg_connection()
        with patch("agents.agent3_risk_engine.get_connection", return_value=fake_conn), \
             patch("agents.agent3_risk_engine.run_agent3", AsyncMock(return_value={"Hormuz": 0.3})):
            result = await agent3.update_risk_weights(new_weights)
        assert agent3._current_weights == new_weights
        assert result == {"Hormuz": 0.3}

    @pytest.mark.asyncio
    async def test_postgres_audit_failure_does_not_block_recalc(self):
        new_weights = {"military_incidents": 0.5, "conflict_escalation": 0.2,
                        "sanctions_change": 0.2, "market_volatility": 0.05, "seasonal_risk": 0.05}
        with patch("agents.agent3_risk_engine.get_connection", side_effect=Exception("PG down")), \
             patch("agents.agent3_risk_engine.run_agent3", AsyncMock(return_value={"Hormuz": 0.3})):
            result = await agent3.update_risk_weights(new_weights)
        assert result == {"Hormuz": 0.3}