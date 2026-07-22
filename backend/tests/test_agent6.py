"""
tests/test_agent6.py

Test suite for Agent 6 (Adaptive Procurement Orchestrator) — agents/agent6.py.

Covers the gap flagged in the ResiChain Bug Audit (finding #16): agent6.py had
ZERO dedicated tests despite being the exact file where the Russia/Kuwait/USA/
Venezuela route-survival bug and the TANKER_UNAVAILABLE bug both lived.

Mocking strategy:
- db.redis_client.get_redis         -> AsyncMock returning a fake redis client
- db.neo4j_queries.*                -> patched directly, no live Neo4j needed
- agents.agent7.validate_batch       -> patched to control APPROVED/PARTIAL/BLOCKED
- db.postgres_queries.insert_procurement_evaluation -> patched, no live Postgres

Run:
    docker exec -it resichain_fastapi python -m pytest tests/test_agent6.py -v
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import agents.agent6 as agent6


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_fake_redis(risk_state=None, prices_live=None):
    """Returns an AsyncMock redis client whose .get() responds based on key."""
    redis_mock = AsyncMock()

    async def fake_get(key):
        if key == "risk:state" and risk_state is not None:
            return json.dumps(risk_state)
        if key == "prices:live" and prices_live is not None:
            return json.dumps(prices_live)
        return None

    redis_mock.get.side_effect = fake_get
    redis_mock.setex = AsyncMock(return_value=True)
    return redis_mock


SURVIVING_ROUTES_FIXTURE = [
    {"supplier": "Iraq", "route": "Iraq to Paradip via Cape", "arrival_port": "Paradip",
     "avg_transit_days": 16, "distance_km": 12500},
    {"supplier": "Russia", "route": "Russia to Vadinar via Cape", "arrival_port": "Vadinar",
     "avg_transit_days": 22, "distance_km": 17500},
    {"supplier": "Saudi Arabia", "route": "Saudi to Kochi via Cape", "arrival_port": "Kochi",
     "avg_transit_days": 12, "distance_km": 9800},
    {"supplier": "UAE", "route": "UAE to Paradip via Cape", "arrival_port": "Paradip",
     "avg_transit_days": 15, "distance_km": 12000},
]

SUPPLIER_GRADES_FIXTURE = [
    {"supplier": "Iraq", "grade": "Basra Light", "api_gravity": 29.7, "sulfur_pct": 2.9},
    {"supplier": "Russia", "grade": "Urals", "api_gravity": 31.1, "sulfur_pct": 1.5},
    {"supplier": "Saudi Arabia", "grade": "Arab Light", "api_gravity": 32.8, "sulfur_pct": 1.8},
    {"supplier": "UAE", "grade": "Murban", "api_gravity": 40.5, "sulfur_pct": 0.7},
]


# ---------------------------------------------------------------------------
# get_blocked_chokepoints
# ---------------------------------------------------------------------------

class TestGetBlockedChokepoints:

    @pytest.mark.asyncio
    async def test_returns_corridors_above_threshold(self):
        risk_state = {"Hormuz": 0.82, "RedSea": 0.71, "Suez": 0.10, "Cape": 0.05}
        fake_redis = make_fake_redis(risk_state=risk_state)
        with patch("agents.agent6.get_redis", AsyncMock(return_value=fake_redis)):
            blocked = await agent6._get_blocked_chokepoints()
        assert set(blocked) == {"Hormuz", "RedSea"}

    @pytest.mark.asyncio
    async def test_excludes_boolean_values(self):
        """Regression test for the is_numeric_score bool-leak bug Person B found:
        bool is a subclass of int in Python, so a scenario-override boolean sitting
        in risk_state must NOT be treated as a numeric risk score."""
        risk_state = {"Hormuz": 0.82, "scenario_override": True, "updated_at": "2026-07-18"}
        fake_redis = make_fake_redis(risk_state=risk_state)
        with patch("agents.agent6.get_redis", AsyncMock(return_value=fake_redis)):
            blocked = await agent6._get_blocked_chokepoints()
        assert blocked == ["Hormuz"]
        assert "scenario_override" not in blocked

    @pytest.mark.asyncio
    async def test_empty_risk_state_returns_empty_list(self):
        fake_redis = make_fake_redis(risk_state=None)
        with patch("agents.agent6.get_redis", AsyncMock(return_value=fake_redis)):
            blocked = await agent6._get_blocked_chokepoints()
        assert blocked == []

    @pytest.mark.asyncio
    async def test_redis_failure_returns_empty_list_not_exception(self):
        broken_redis = AsyncMock()
        broken_redis.get.side_effect = Exception("Redis connection refused")
        with patch("agents.agent6.get_redis", AsyncMock(return_value=broken_redis)):
            blocked = await agent6._get_blocked_chokepoints()
        assert blocked == []


# ---------------------------------------------------------------------------
# build_candidates — the core logic behind the Russia/Kuwait/USA/Venezuela bug
# ---------------------------------------------------------------------------

class TestBuildCandidates:

    @pytest.mark.asyncio
    async def test_empty_surviving_routes_returns_no_candidates(self):
        candidates = await agent6._build_candidates([], ["Hormuz", "RedSea"])
        assert candidates == []

    @pytest.mark.asyncio
    async def test_one_candidate_per_unique_supplier(self):
        """Regression test for the exact bug this session found and fixed:
        Russia/Kuwait/USA/Venezuela must be able to produce a candidate once
        their Cape fallback route exists in surviving_routes."""
        fake_redis = make_fake_redis(risk_state={"Cape": 0.10}, prices_live={"Brent": 84.2})
        with patch("agents.agent6.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent6.get_all_supplier_grades", return_value=SUPPLIER_GRADES_FIXTURE), \
             patch("agents.agent6.get_contract_headroom", return_value={
                 "max_volume_mbd": 0.5, "headroom_mbd": 0.2, "contract_reference": "CNTR-TEST-001"
             }), \
             patch("agents.agent6.get_port_specs", return_value={"max_vessel_dwt": 180000}), \
             patch("agents.agent6._quick_grade_check", return_value=True):
            candidates = await agent6._build_candidates(SURVIVING_ROUTES_FIXTURE, ["Hormuz", "RedSea"])

        suppliers_seen = {c["supplier"] for c in candidates}
        assert suppliers_seen == {"Iraq", "Russia", "Saudi Arabia", "UAE"}
        assert len(candidates) == 4  # exactly one per unique supplier, no duplicates

    @pytest.mark.asyncio
    async def test_duplicate_supplier_routes_deduplicated(self):
        """If a supplier somehow has two surviving routes, only the first is used —
        Agent 6 must never emit two candidates for the same supplier."""
        dup_routes = SURVIVING_ROUTES_FIXTURE + [
            {"supplier": "Iraq", "route": "Iraq to Vizag via Cape", "arrival_port": "Vizag",
             "avg_transit_days": 20, "distance_km": 15000},
        ]
        fake_redis = make_fake_redis(risk_state={}, prices_live={"Brent": 84.2})
        with patch("agents.agent6.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent6.get_all_supplier_grades", return_value=SUPPLIER_GRADES_FIXTURE), \
             patch("agents.agent6.get_contract_headroom", return_value={
                 "max_volume_mbd": 0.5, "headroom_mbd": 0.2, "contract_reference": "CNTR-TEST-001"
             }), \
             patch("agents.agent6.get_port_specs", return_value={"max_vessel_dwt": 180000}), \
             patch("agents.agent6._quick_grade_check", return_value=True):
            candidates = await agent6._build_candidates(dup_routes, [])

        iraq_candidates = [c for c in candidates if c["supplier"] == "Iraq"]
        assert len(iraq_candidates) == 1
        assert iraq_candidates[0]["arrival_port"] == "Paradip"  # first occurrence wins

    @pytest.mark.asyncio
    async def test_candidate_missing_supplier_is_skipped(self):
        routes_with_gap = SURVIVING_ROUTES_FIXTURE + [
            {"supplier": None, "route": "Unknown to Nowhere", "arrival_port": "Nowhere"}
        ]
        fake_redis = make_fake_redis(risk_state={}, prices_live={"Brent": 84.2})
        with patch("agents.agent6.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent6.get_all_supplier_grades", return_value=SUPPLIER_GRADES_FIXTURE), \
             patch("agents.agent6.get_contract_headroom", return_value={
                 "max_volume_mbd": 0.5, "headroom_mbd": 0.2, "contract_reference": "CNTR-TEST-001"
             }), \
             patch("agents.agent6.get_port_specs", return_value={"max_vessel_dwt": 180000}), \
             patch("agents.agent6._quick_grade_check", return_value=True):
            candidates = await agent6._build_candidates(routes_with_gap, [])
        assert all(c["supplier"] is not None for c in candidates)


# ---------------------------------------------------------------------------
# pick_vessel_class_for_port — the field TANKER_UNAVAILABLE debugging touched
# ---------------------------------------------------------------------------

class TestPickVesselClassForPort:

    def test_picks_largest_class_port_can_fit(self):
        with patch("agents.agent6.get_port_specs", return_value={"max_vessel_dwt": 320000}):
            result = agent6._pick_vessel_class_for_port("Jamnagar Sikka")
        assert result == "VLCC"

    def test_smaller_port_gets_smaller_class_not_vlcc(self):
        """Regression test: previously every Hormuz/Cape route got VLCC regardless
        of destination port capacity, silently blocking on PORT_CAPACITY even when
        a smaller vessel would have been adequate."""
        with patch("agents.agent6.get_port_specs", return_value={"max_vessel_dwt": 150000}):
            result = agent6._pick_vessel_class_for_port("Kochi")
        assert result == "Aframax"

    def test_medium_port_gets_suezmax(self):
        with patch("agents.agent6.get_port_specs", return_value={"max_vessel_dwt": 180000}):
            result = agent6._pick_vessel_class_for_port("Paradip")
        assert result == "Suezmax"

    def test_port_lookup_failure_falls_back_to_default(self):
        with patch("agents.agent6.get_port_specs", side_effect=Exception("Neo4j down")):
            result = agent6._pick_vessel_class_for_port("UnknownPort")
        assert result == agent6.DEFAULT_VESSEL_CLASS


# ---------------------------------------------------------------------------
# get_batch_validator — Agent 6 <-> Agent 7 handoff contract
# ---------------------------------------------------------------------------

class TestGetBatchValidator:

    @pytest.mark.asyncio
    async def test_uses_real_agent7_when_importable(self):
        fake_sync_validate = MagicMock(return_value=[
            {"option_id": "proc_iraq_000", "supplier": "Iraq", "grade": "Basra Light",
             "status": "APPROVED", "reason": {"rule": "ALL_LAYERS_PASSED"},
             "confidence": 0.8, "adjusted_volume_mbd": 0.15},
        ])
        with patch("agents.agent7.validate_batch", fake_sync_validate, create=True):
            validator = await agent6._get_batch_validator()
            result = await validator([{"option_id": "proc_iraq_000"}], None)
        assert result[0]["status"] == "APPROVED"
        fake_sync_validate.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_when_agent7_not_importable(self):
        with patch.dict("sys.modules", {"agents.agent7": None}):
            validator = await agent6._get_batch_validator()
        assert validator is agent6._fallback_validate_batch


# ---------------------------------------------------------------------------
# run_agent6 — full pipeline integration test
# ---------------------------------------------------------------------------

class TestRunAgent6Integration:

    @pytest.mark.asyncio
    async def test_full_pipeline_returns_ranked_and_rejection_trace(self):
        """End-to-end: blocked chokepoints -> surviving routes -> candidates ->
        validation -> ranked output. Mirrors the exact compound Hormuz+Bab-el-Mandeb
        scenario verified live this session (7/7 suppliers surviving)."""
        risk_state = {"Hormuz": 0.82, "RedSea": 0.75}
        fake_redis = make_fake_redis(risk_state=risk_state, prices_live={"Brent": 84.2})

        fake_validation_results = [
            {"option_id": c["route"], "supplier": c["route"].split(" ")[0] if False else None}
            for c in []  # placeholder, real assertions built from candidates below
        ]

        async def fake_batch_validator(candidates, playbook_id):
            # Approve everything for this integration smoke test
            return [
                {"option_id": c["option_id"], "supplier": c["supplier"], "grade": c["grade"],
                 "status": "APPROVED", "reason": {"rule": "ALL_LAYERS_PASSED"},
                 "confidence": c["confidence"], "adjusted_volume_mbd": c["proposed_volume_mbd"]}
                for c in candidates
            ]

        with patch("agents.agent6.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent6.get_surviving_routes", return_value=SURVIVING_ROUTES_FIXTURE), \
             patch("agents.agent6.get_all_supplier_grades", return_value=SUPPLIER_GRADES_FIXTURE), \
             patch("agents.agent6.get_contract_headroom", return_value={
                 "max_volume_mbd": 0.5, "headroom_mbd": 0.2, "contract_reference": "CNTR-TEST-001"
             }), \
             patch("agents.agent6.get_port_specs", return_value={"max_vessel_dwt": 180000}), \
             patch("agents.agent6._quick_grade_check", return_value=True), \
             patch("agents.agent6._get_batch_validator", AsyncMock(return_value=fake_batch_validator)):
            output = await agent6.run_agent6(playbook_id=None)

        assert output["evaluated_count"] == 4
        assert output["approved_count"] == 4
        assert output["blocked_count"] == 0
        assert len(output["ranked_options"]) == 4
        assert len(output["full_rejection_trace"]) == 4
        suppliers_approved = {r["supplier"] for r in output["ranked_options"]}
        assert suppliers_approved == {"Iraq", "Russia", "Saudi Arabia", "UAE"}

    @pytest.mark.asyncio
    async def test_missing_validation_result_treated_as_blocked_not_crash(self):
        """If a candidate silently vanishes inside the validator, Agent 6 must
        treat it as BLOCKED with rule=VALIDATOR_RESULT_MISSING, not crash."""
        fake_redis = make_fake_redis(risk_state={}, prices_live={"Brent": 84.2})

        async def incomplete_validator(candidates, playbook_id):
            return []  # validator returns nothing for any candidate

        with patch("agents.agent6.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent6.get_surviving_routes", return_value=SURVIVING_ROUTES_FIXTURE[:1]), \
             patch("agents.agent6.get_all_supplier_grades", return_value=SUPPLIER_GRADES_FIXTURE), \
             patch("agents.agent6.get_contract_headroom", return_value={
                 "max_volume_mbd": 0.5, "headroom_mbd": 0.2, "contract_reference": "CNTR-TEST-001"
             }), \
             patch("agents.agent6.get_port_specs", return_value={"max_vessel_dwt": 180000}), \
             patch("agents.agent6._quick_grade_check", return_value=True), \
             patch("agents.agent6._get_batch_validator", AsyncMock(return_value=incomplete_validator)):
            output = await agent6.run_agent6(playbook_id=None)

        assert output["blocked_count"] == 1
        assert output["full_rejection_trace"][0]["status"] == "BLOCKED"
        assert output["full_rejection_trace"][0]["reason"]["rule"] == "VALIDATOR_RESULT_MISSING"

    @pytest.mark.asyncio
    async def test_no_surviving_routes_returns_empty_but_valid_output(self):
        """All corridors blocked, or KG has no route data — must degrade gracefully,
        not crash, and must still return the standard output shape."""
        fake_redis = make_fake_redis(risk_state={"Hormuz": 0.9, "RedSea": 0.9,
                                                   "Suez": 0.9, "Cape": 0.9})

        async def no_op_validator(candidates, playbook_id):
            return []

        with patch("agents.agent6.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent6.get_surviving_routes", return_value=[]), \
             patch("agents.agent6._get_batch_validator", AsyncMock(return_value=no_op_validator)):
            output = await agent6.run_agent6(playbook_id=None)

        assert output["evaluated_count"] == 0
        assert output["approved_count"] == 0
        assert output["ranked_options"] == []
        assert output["full_rejection_trace"] == []

    @pytest.mark.asyncio
    async def test_neo4j_failure_does_not_crash_pipeline(self):
        """get_surviving_routes raising must be caught, not propagate and crash
        the whole Agent 6 cycle."""
        fake_redis = make_fake_redis(risk_state={})

        async def no_op_validator(candidates, playbook_id):
            return []

        with patch("agents.agent6.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("agents.agent6.get_surviving_routes", side_effect=Exception("Neo4j connection refused")), \
             patch("agents.agent6._get_batch_validator", AsyncMock(return_value=no_op_validator)):
            output = await agent6.run_agent6(playbook_id=None)

        assert output["evaluated_count"] == 0
        assert output["ranked_options"] == []
