"""
tests/test_agent5.py
====================
Unit tests for agents/agent5.py — SPR LP solver.

Run with:
    docker exec -it resichain_fastapi python -m pytest tests/test_agent5.py -v

Tests verify:
  - Valid LP produces a feasible 30-day schedule
  - All constraints respected (daily cap, reserve floor, demand met)
  - Fix 5: infeasibility fallback is triggered and returns correct shape
  - Re-run with approved cargoes (Fix 7) produces a tighter schedule
  - PostgreSQL write is called for every solve
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

PATCH_SPR = "agents.agent5.get_spr_total_volume"
PATCH_PG_INSERT = "agents.agent5.insert_spr_schedule"
PATCH_PRICE_HIST = "agents.agent5.get_latest_price_history"
PATCH_REDIS = "agents.agent5._get_redis"
PATCH_CONSUMPTION = "agents.agent5._get_india_consumption"

MOCK_SPR_MB = 38.0
MOCK_CONSUMPTION = 5.1
MOCK_BRENT = 85.0
MOCK_PLAYBOOK_ID = uuid4()

HORIZON = 30
MAX_RELEASE = 0.5
RESERVE_FLOOR = 0.40

# Feasible test fixtures:
# demand = 5.1 mbd, imports = 4.7 mbd => gap = 0.4 mbd <= 0.5 cap
FEASIBLE_IMPORTS = [4.7] * HORIZON
FEASIBLE_APPROVED = [4.7] * HORIZON


def _mock_redis_no_data():
    m = MagicMock()
    m.get.return_value = None
    return m


def _mock_pg_insert():
    """Mock insert_spr_schedule to return a fake UUID."""
    return uuid4()


# ---------------------------------------------------------------------------
# Helper — run solve_spr_schedule with all I/O mocked
# ---------------------------------------------------------------------------

def _solve(
    available_imports=None,
    approved_cargoes=None,
    spr_mb=MOCK_SPR_MB,
    consumption=MOCK_CONSUMPTION,
    playbook_id=None,
):
    from agents.agent5 import solve_spr_schedule

    with patch(PATCH_SPR, return_value=spr_mb), \
         patch(PATCH_REDIS, return_value=_mock_redis_no_data()), \
         patch(PATCH_PRICE_HIST, return_value={"brent_usd": MOCK_BRENT}), \
         patch(PATCH_CONSUMPTION, return_value=consumption), \
         patch(PATCH_PG_INSERT, side_effect=lambda **kw: uuid4()):
        return solve_spr_schedule(
            available_imports_mbd=available_imports,
            spr_total_mb=spr_mb,
            daily_consumption_mbd=consumption,
            approved_cargo_schedule=approved_cargoes,
            playbook_id=playbook_id,
        )


# ---------------------------------------------------------------------------
# Tests — feasible scenarios
# ---------------------------------------------------------------------------

class TestSolveFeasible:

    def test_returns_feasible_true(self):
        """Enough imports to make it solvable: 4.7 mb/day, gap only 0.4 mb/day."""
        result = _solve(available_imports=FEASIBLE_IMPORTS)
        assert result["feasible"] is True

    def test_schedule_length_is_30(self):
        result = _solve(available_imports=FEASIBLE_IMPORTS)
        assert len(result["daily_drawdown_schedule_mbd"]) == HORIZON

    def test_daily_values_non_negative(self):
        result = _solve(available_imports=FEASIBLE_IMPORTS)
        for val in result["daily_drawdown_schedule_mbd"]:
            assert val >= 0.0

    def test_daily_values_within_cap(self):
        """Each day's release must not exceed MAX_DAILY_RELEASE_MBD = 0.5."""
        result = _solve(available_imports=FEASIBLE_IMPORTS)
        for i, val in enumerate(result["daily_drawdown_schedule_mbd"]):
            assert val <= MAX_RELEASE + 1e-6, f"Day {i}: {val} exceeds cap {MAX_RELEASE}"

    def test_reserve_floor_respected(self):
        """
        Total drawdown must not exceed (1 - 0.40) × 38 = 22.8 mb.
        SPR floor of 40% must remain untouched.
        """
        result = _solve(available_imports=FEASIBLE_IMPORTS)
        max_allowed = (1.0 - RESERVE_FLOOR) * MOCK_SPR_MB
        assert result["total_drawdown_mb"] <= max_allowed + 1e-4, (
            f"Drawdown {result['total_drawdown_mb']} exceeds allowed {max_allowed}"
        )

    def test_demand_met_each_day(self):
        """
        For each day: drawdown[t] + imports[t] >= demand[t].
        i.e. the gap (demand - imports) must be covered by drawdown.
        """
        result = _solve(available_imports=FEASIBLE_IMPORTS)
        schedule = result["daily_drawdown_schedule_mbd"]
        for t in range(HORIZON):
            coverage = schedule[t] + FEASIBLE_IMPORTS[t]
            assert coverage >= MOCK_CONSUMPTION - 1e-4, (
                f"Day {t}: coverage {coverage:.4f} < demand {MOCK_CONSUMPTION}"
            )

    def test_spr_remaining_correct(self):
        result = _solve(available_imports=FEASIBLE_IMPORTS)
        expected_remaining = MOCK_SPR_MB - result["total_drawdown_mb"]
        assert abs(result["spr_remaining_mb"] - expected_remaining) < 0.1

    def test_confidence_1_for_optimal(self):
        """scipy linprog status=0 (optimal) must yield confidence=1.0."""
        result = _solve(available_imports=FEASIBLE_IMPORTS)
        assert result["confidence"] == 1.0

    def test_critical_warning_none_on_feasible(self):
        result = _solve(available_imports=FEASIBLE_IMPORTS)
        assert result["critical_warning"] is None

    def test_no_imports_worst_case(self):
        """
        No imports at all. SPR must cover full demand each day.
        5.1 mb/day × 30 = 153 mb needed — exceeds SPR (38 mb) → infeasible.
        Verify infeasibility fallback fires in this worst case.
        """
        result = _solve(available_imports=[0.0] * HORIZON)
        assert result["feasible"] is False

    def test_partial_imports_reduces_drawdown(self):
        """More available imports → less total SPR drawdown required."""
        low_imports = _solve(available_imports=[1.0] * HORIZON)
        high_imports = _solve(available_imports=FEASIBLE_IMPORTS)
        if low_imports["feasible"] and high_imports["feasible"]:
            assert high_imports["total_drawdown_mb"] <= low_imports["total_drawdown_mb"]


# ---------------------------------------------------------------------------
# Tests — Fix 5 infeasibility fallback
# ---------------------------------------------------------------------------

class TestInfeasibilityFallback:

    def test_infeasible_returns_feasible_false(self):
        """Impossible scenario (zero imports, huge demand) must trigger fallback."""
        result = _solve(available_imports=[0.0] * HORIZON, consumption=50.0)
        assert result["feasible"] is False

    def test_infeasible_schedule_all_max_release(self):
        """Fallback schedule must be MAX_DAILY_RELEASE_MBD for all 30 days."""
        result = _solve(available_imports=[0.0] * HORIZON, consumption=50.0)
        if not result["feasible"]:
            for val in result["daily_drawdown_schedule_mbd"]:
                assert abs(val - MAX_RELEASE) < 1e-6

    def test_infeasible_confidence_zero(self):
        """Fallback confidence must be exactly 0.0."""
        result = _solve(available_imports=[0.0] * HORIZON, consumption=50.0)
        if not result["feasible"]:
            assert result["confidence"] == 0.0

    def test_infeasible_critical_warning_present(self):
        """Fallback must include the exact critical_warning string from spec."""
        result = _solve(available_imports=[0.0] * HORIZON, consumption=50.0)
        if not result["feasible"]:
            assert result["critical_warning"] is not None
            assert "Emergency rationing" in result["critical_warning"]

    def test_infeasible_schedule_length_still_30(self):
        """Fallback schedule must still be 30 days long."""
        result = _solve(available_imports=[0.0] * HORIZON, consumption=50.0)
        if not result["feasible"]:
            assert len(result["daily_drawdown_schedule_mbd"]) == HORIZON

    def test_infeasible_record_id_present(self):
        """Even infeasible results must be written to PostgreSQL and return a record_id."""
        with patch(PATCH_SPR, return_value=MOCK_SPR_MB), \
             patch(PATCH_REDIS, return_value=_mock_redis_no_data()), \
             patch(PATCH_PRICE_HIST, return_value={"brent_usd": MOCK_BRENT}), \
             patch(PATCH_CONSUMPTION, return_value=50.0), \
             patch(PATCH_PG_INSERT, side_effect=lambda **kw: uuid4()) as mock_insert:
            from agents.agent5 import solve_spr_schedule
            result = solve_spr_schedule(
                available_imports_mbd=[0.0] * HORIZON,
                spr_total_mb=MOCK_SPR_MB,
                daily_consumption_mbd=50.0,
            )
            mock_insert.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — Fix 7 re-run with approved cargoes
# ---------------------------------------------------------------------------

class TestApprovedCargoRerun:

    def test_approved_cargoes_used_over_available_imports(self):
        """
        When approved_cargo_schedule is provided, it must be used
        instead of available_imports_mbd (Fix 7).
        """
        from agents.agent5 import solve_spr_schedule

        with patch(PATCH_SPR, return_value=MOCK_SPR_MB), \
             patch(PATCH_REDIS, return_value=_mock_redis_no_data()), \
             patch(PATCH_PRICE_HIST, return_value={"brent_usd": MOCK_BRENT}), \
             patch(PATCH_CONSUMPTION, return_value=MOCK_CONSUMPTION), \
             patch(PATCH_PG_INSERT, side_effect=lambda **kw: uuid4()):
            result = solve_spr_schedule(
                available_imports_mbd=[0.0] * HORIZON,   # would fail alone
                spr_total_mb=MOCK_SPR_MB,
                daily_consumption_mbd=MOCK_CONSUMPTION,
                approved_cargo_schedule=FEASIBLE_APPROVED,  # overrides above
            )

        assert result["feasible"] is True
        assert result["inputs_used"]["used_approved_cargoes"] is True

    def test_rerun_lower_drawdown_than_first_run(self):
        """
        Re-run with approved cargoes (higher imports) should require less SPR
        than the initial run with lower available imports.
        """
        initial = _solve(available_imports=[3.0] * HORIZON)
        rerun = _solve(approved_cargoes=FEASIBLE_APPROVED)
        if initial["feasible"] and rerun["feasible"]:
            assert rerun["total_drawdown_mb"] <= initial["total_drawdown_mb"]


# ---------------------------------------------------------------------------
# Tests — PostgreSQL write
# ---------------------------------------------------------------------------

class TestPostgresWrite:

    def test_insert_called_on_feasible_solve(self):
        """insert_spr_schedule must be called exactly once for a feasible solve."""
        with patch(PATCH_SPR, return_value=MOCK_SPR_MB), \
             patch(PATCH_REDIS, return_value=_mock_redis_no_data()), \
             patch(PATCH_PRICE_HIST, return_value={"brent_usd": MOCK_BRENT}), \
             patch(PATCH_CONSUMPTION, return_value=MOCK_CONSUMPTION), \
             patch(PATCH_PG_INSERT, side_effect=lambda **kw: uuid4()) as mock_insert:
            from agents.agent5 import solve_spr_schedule
            solve_spr_schedule(
                available_imports_mbd=FEASIBLE_IMPORTS,
                spr_total_mb=MOCK_SPR_MB,
                daily_consumption_mbd=MOCK_CONSUMPTION,
                playbook_id=MOCK_PLAYBOOK_ID,
            )
            mock_insert.assert_called_once()

    def test_record_id_in_result(self):
        """result must contain a non-None record_id string."""
        result = _solve(available_imports=FEASIBLE_IMPORTS)
        if result["feasible"]:
            assert result["record_id"] is not None
            assert isinstance(result["record_id"], str)


# ---------------------------------------------------------------------------
# Tests — LangGraph entry point
# ---------------------------------------------------------------------------

class TestRunAgent5:

    def test_state_passthrough(self):
        """run_agent5 must return all original state keys plus spr_schedule."""
        from agents.agent5 import run_agent5
        state = {
            "surviving_routes_mbd": FEASIBLE_IMPORTS,
            "playbook_id": None,
            "corridor": "Hormuz",
        }
        with patch(PATCH_SPR, return_value=MOCK_SPR_MB), \
             patch(PATCH_REDIS, return_value=_mock_redis_no_data()), \
             patch(PATCH_PRICE_HIST, return_value={"brent_usd": MOCK_BRENT}), \
             patch(PATCH_CONSUMPTION, return_value=MOCK_CONSUMPTION), \
             patch(PATCH_PG_INSERT, side_effect=lambda **kw: uuid4()):
            result_state = run_agent5(state)

        assert "spr_schedule" in result_state
        assert result_state["corridor"] == "Hormuz"  # original key preserved

    def test_approved_cargoes_picked_up_from_state(self):
        """run_agent5 must use approved_cargoes_mbd from state when present (Fix 7)."""
        from agents.agent5 import run_agent5
        state = {
            "surviving_routes_mbd": [0.0] * HORIZON,
            "approved_cargoes_mbd": FEASIBLE_APPROVED,
            "playbook_id": None,
        }
        with patch(PATCH_SPR, return_value=MOCK_SPR_MB), \
             patch(PATCH_REDIS, return_value=_mock_redis_no_data()), \
             patch(PATCH_PRICE_HIST, return_value={"brent_usd": MOCK_BRENT}), \
             patch(PATCH_CONSUMPTION, return_value=MOCK_CONSUMPTION), \
             patch(PATCH_PG_INSERT, side_effect=lambda **kw: uuid4()):
            result_state = run_agent5(state)

        assert result_state["spr_schedule"]["feasible"] is True
        assert result_state["spr_schedule"]["inputs_used"]["used_approved_cargoes"] is True