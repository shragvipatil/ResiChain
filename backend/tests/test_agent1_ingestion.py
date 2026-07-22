# tests/test_agent1_ingestion.py
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

import agents.agent1_ingestion as ingestion


def make_gdelt_event(corridor="Hormuz", severity=6, confidence=0.6, source="GDELT"):
    return {
        "source": source,
        "corridor": corridor,
        "severity": severity,
        "raw_confidence": confidence,
        "headline": "conflict escalation reported",
        "timestamp": datetime.utcnow().isoformat(),
    }


def make_ukmto_event(corridor="Hormuz", severity=7, confidence=0.9):
    return {
        "source": "UKMTO",
        "corridor": corridor,
        "severity": severity,
        "raw_confidence": confidence,
        "headline": "maritime security advisory",
        "timestamp": datetime.utcnow().isoformat(),
    }


def make_price_alert(triggered=False, corridor="Global"):
    return {
        "source": "AlphaVantage_PriceAlert",
        "corridor": corridor,
        "severity": 4,
        "raw_confidence": 0.95,
        "alert_triggered": triggered,
        "timestamp": datetime.utcnow().isoformat(),
    }


class TestDetermineSystemMode:
    def test_no_alerts_returns_normal(self):
        assert ingestion._determine_system_mode([]) == "NORMAL"

    def test_confirmed_stage_returns_crisis(self):
        alerts = [{"stage": "MONITOR"}, {"stage": "CONFIRMED"}]
        assert ingestion._determine_system_mode(alerts) == "CRISIS"

    def test_watch_stage_without_confirmed_returns_watch(self):
        alerts = [{"stage": "MONITOR"}, {"stage": "WATCH"}]
        assert ingestion._determine_system_mode(alerts) == "WATCH"

    def test_all_monitor_returns_normal(self):
        alerts = [{"stage": "MONITOR"}, {"stage": "MONITOR"}]
        assert ingestion._determine_system_mode(alerts) == "NORMAL"

    def test_confirmed_takes_priority_over_watch(self):
        alerts = [{"stage": "WATCH"}, {"stage": "CONFIRMED"}, {"stage": "MONITOR"}]
        assert ingestion._determine_system_mode(alerts) == "CRISIS"


class TestRunAgent1PollStageDetermination:
    @pytest.mark.asyncio
    async def test_single_low_confidence_source_stays_monitor(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[
                 make_gdelt_event(severity=2, confidence=0.2)
             ])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            alerts = await ingestion.run_agent1_poll()
        assert alerts[0]["stage"] == "MONITOR"

    @pytest.mark.asyncio
    async def test_single_source_above_watch_threshold_is_watch(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[
                 make_gdelt_event(severity=3, confidence=0.5)
             ])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            alerts = await ingestion.run_agent1_poll()
        assert alerts[0]["stage"] == "WATCH"

    @pytest.mark.asyncio
    async def test_high_severity_alone_forces_watch_regardless_of_confidence(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[
                 make_gdelt_event(severity=5, confidence=0.1)
             ])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            alerts = await ingestion.run_agent1_poll()
        assert alerts[0]["stage"] == "WATCH"

    @pytest.mark.asyncio
    async def test_two_sources_high_confidence_reaches_confirmed(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[
                 make_gdelt_event(confidence=0.8)
             ])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[
                 make_ukmto_event(confidence=0.9)
             ])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            alerts = await ingestion.run_agent1_poll()
        assert alerts[0]["stage"] == "CONFIRMED"

    @pytest.mark.asyncio
    async def test_two_sources_but_low_confidence_does_not_reach_confirmed(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[
                 make_gdelt_event(confidence=0.5, severity=2)
             ])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[
                 make_ukmto_event(confidence=0.5, severity=2)
             ])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            alerts = await ingestion.run_agent1_poll()
        assert alerts[0]["stage"] != "CONFIRMED"


class TestEventGrouping:
    @pytest.mark.asyncio
    async def test_events_grouped_by_corridor_produce_separate_alerts(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[
                 make_gdelt_event(corridor="Hormuz"),
                 make_gdelt_event(corridor="Red_Sea"),
             ])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            alerts = await ingestion.run_agent1_poll()
        corridors = {a["corridor"] for a in alerts}
        assert corridors == {"Hormuz", "Red_Sea"}

    @pytest.mark.asyncio
    async def test_missing_corridor_field_defaults_to_unknown(self):
        event = make_gdelt_event()
        del event["corridor"]
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[event])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            alerts = await ingestion.run_agent1_poll()
        assert alerts[0]["corridor"] == "Unknown"

    @pytest.mark.asyncio
    async def test_max_severity_uses_highest_across_all_events_in_corridor(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[
                 make_gdelt_event(severity=2), make_gdelt_event(severity=9)
             ])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            alerts = await ingestion.run_agent1_poll()
        assert alerts[0]["max_severity"] == 9

    @pytest.mark.asyncio
    async def test_source_count_deduplicates_same_source_multiple_events(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[
                 make_gdelt_event(), make_gdelt_event()
             ])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            alerts = await ingestion.run_agent1_poll()
        assert alerts[0]["source_count"] == 1
        assert alerts[0]["event_count"] == 2


class TestPriceAlertInclusion:
    @pytest.mark.asyncio
    async def test_price_alert_included_only_when_triggered(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(
                 return_value=make_price_alert(triggered=True, corridor="Global")
             )):
            alerts = await ingestion.run_agent1_poll()
        assert any(a["corridor"] == "Global" for a in alerts)

    @pytest.mark.asyncio
    async def test_price_alert_excluded_when_not_triggered(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(
                 return_value=make_price_alert(triggered=False)
             )):
            alerts = await ingestion.run_agent1_poll()
        assert alerts == []


class TestRedisLogging:
    @pytest.mark.asyncio
    async def test_last_run_key_written_with_correct_fields(self):
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[
                 make_gdelt_event(severity=8, confidence=0.9)
             ])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[
                 make_ukmto_event(confidence=0.9)
             ])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            await ingestion.run_agent1_poll()

        fake_redis.setex.assert_called_once()
        args = fake_redis.setex.call_args[0]
        assert args[0] == "agent1:last_run"
        assert args[1] == 600
        payload = json.loads(args[2])
        assert payload["system_mode"] == "CRISIS"
        assert payload["events_found"] == 2
        assert payload["corridors_active"] == 1


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_gdelt_client_failure_returns_empty_list_not_crash(self):
        with patch("agents.agent1_ingestion.get_redis", AsyncMock()), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(side_effect=Exception("GDELT down"))), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            result = await ingestion.run_agent1_poll()
        assert result == []

    @pytest.mark.asyncio
    async def test_redis_failure_returns_empty_list_not_crash(self):
        with patch("agents.agent1_ingestion.get_redis", AsyncMock(side_effect=Exception("Redis down"))), \
             patch("agents.agent1_ingestion.fetch_gdelt_events", AsyncMock(return_value=[make_gdelt_event()])), \
             patch("agents.agent1_ingestion.fetch_ukmto_alerts", AsyncMock(return_value=[])), \
             patch("agents.agent1_ingestion.fetch_brent_price_alert", AsyncMock(return_value=make_price_alert())):
            result = await ingestion.run_agent1_poll()
        assert result == []


class TestDemoInject:
    @pytest.mark.asyncio
    async def test_demo_inject_publishes_gdelt_then_ukmto_events(self):
        with patch("db.redis_client.publish_event", AsyncMock()) as mock_publish, \
             patch("asyncio.sleep", AsyncMock()):
            await ingestion.run_agent1_demo_inject(corridor="Hormuz", severity=8)

        assert mock_publish.call_count == 2
        first_call = mock_publish.call_args_list[0][0][0]
        second_call = mock_publish.call_args_list[1][0][0]
        assert first_call["source"] == "GDELT"
        assert first_call["corridor"] == "Hormuz"
        assert second_call["source"] == "UKMTO"
        assert second_call["corridor"] == "Hormuz"

    @pytest.mark.asyncio
    async def test_demo_inject_second_event_has_slightly_lower_severity(self):
        with patch("db.redis_client.publish_event", AsyncMock()) as mock_publish, \
             patch("asyncio.sleep", AsyncMock()):
            await ingestion.run_agent1_demo_inject(corridor="Hormuz", severity=8)

        first_severity = mock_publish.call_args_list[0][0][0]["severity"]
        second_severity = mock_publish.call_args_list[1][0][0]["severity"]
        assert second_severity == first_severity - 1

    @pytest.mark.asyncio
    async def test_demo_inject_marks_events_as_demo_true(self):
        with patch("db.redis_client.publish_event", AsyncMock()) as mock_publish, \
             patch("asyncio.sleep", AsyncMock()):
            await ingestion.run_agent1_demo_inject()

        for call in mock_publish.call_args_list:
            assert call[0][0]["demo"] is True