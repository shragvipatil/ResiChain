"""
agents/agent7.py
================
ResiChain AI v2.0 — Agent 7: Constraint Governance Validator

Purpose:
    Deterministic rule engine. No LLM involved. Gates every procurement
    recommendation produced by Agent 6 through four sequential layers.

    Layer 1 — Sanctions check (OFAC SDN, PostgreSQL)
    Layer 2 — Grade compatibility (Neo4j COMPATIBLE_WITH)
    Layer 3 — Diversification cap (sequential running_share, Fix 10)
    Layer 4 — Operational constraints (port capacity, tanker availability,
              contract ceiling, take-or-pay floor)

Every layer produces a structured reason JSON:
    {"rule": "OFAC_SDN", "value": "...", "threshold": null, "source": "..."}

Called as: validator(candidate, playbook_id)

Candidate dict shape (from Agent 6, confirmed):
    {
        "option_id": str,
        "supplier": str,
        "grade": str,
        "refinery": str,
        "proposed_volume_mbd": float,
        "confidence": float,
        "contract_reference": str,
        "vessel_class": str,
        "departure_port": str,
    }

Fix 10 (mandatory):
    Diversification cap validation is SEQUENTIAL, not parallel. A dict of
    running_share is initialised once per playbook run from PostgreSQL and
    updated after each approval, so two recommendations for the same
    supplier are never validated against the same starting share.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional
from uuid import UUID

import redis as redis_lib
from dotenv import load_dotenv

from db.neo4j_queries import (
    check_grade_compatibility,
    get_contract_headroom,
    get_supplier_current_share,
)
from db.postgres_queries import (
    check_ofac_match,
    insert_procurement_evaluation,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — overridable via .env
# ---------------------------------------------------------------------------

MAX_SUPPLIER_SHARE_PCT: float = float(os.getenv("MAX_SUPPLIER_SHARE_PCT", "0.40"))

# Used to convert a proposed cargo volume (mb/day) into a fraction of total
# daily imports for the diversification-cap comparison in Layer 3. Falls
# back to the documented ~5.1 mb/day figure if EIA data isn't wired here;
# Agent 6 may eventually pass total_daily_consumption_mbd explicitly.
INDIA_DAILY_CONSUMPTION_MBD: float = float(os.getenv("INDIA_DAILY_CONSUMPTION_MBD", "5.1"))

# TODO(Person B): vessel_class / departure_port are demo reference lookups,
# not live-sourced fields yet. Move into Neo4j Port/Route seeding when time
# allows — see neo4j_query_request issue if Person A/C need this earlier.
VESSEL_CLASS_MAX_DWT = {
    "VLCC": 320_000,
    "Suezmax": 160_000,
    "Aframax": 120_000,
}

# ---------------------------------------------------------------------------
# Redis helper — shared, lazy-initialised
# ---------------------------------------------------------------------------

_redis_client: Optional[redis_lib.Redis] = None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
    return _redis_client


def _get_vessels_near_port(departure_port: str) -> int:
    """
    Count available VLCC/Suezmax vessels near departure_port from
    Redis vessels:live cache (populated by Person A's AISHub poller).
    Returns 0 (not -1) if the cache is empty/unavailable — a missing
    cache should never silently APPROVE tanker availability.
    """
    try:
        r = _get_redis()
        raw = r.get("vessels:live")
        if not raw:
            return 0
        import json

        vessels = json.loads(raw)
        count = 0
        for v in vessels:
            if str(v.get("nearest_port", "")).lower() == departure_port.lower():
                count += 1
        return count
    except Exception as exc:
        logger.warning("Redis vessels:live read failed in Agent 7: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Reason JSON builder
# ---------------------------------------------------------------------------


def _reason(rule: str, value: Any, threshold: Any = None, source: str = "") -> Dict[str, Any]:
    return {"rule": rule, "value": value, "threshold": threshold, "source": source}


def _result(
    *,
    status: str,
    candidate: Dict[str, Any],
    reason: Dict[str, Any],
    playbook_id: Optional[UUID],
) -> Dict[str, Any]:
    """Build the evaluation result and log it to PostgreSQL immediately."""
    payload = {
        "option_id": candidate.get("option_id"),
        "supplier": candidate.get("supplier"),
        "grade": candidate.get("grade"),
        "status": status,
        "reason": reason,
        "confidence": candidate.get("confidence"),
    }

    try:
        insert_procurement_evaluation(
            playbook_id=playbook_id,
            option_id=candidate.get("option_id"),
            supplier=candidate.get("supplier"),
            grade=candidate.get("grade"),
            status=status,
            rule_triggered=reason.get("rule"),
            reason=reason,
            confidence=candidate.get("confidence"),
        )
    except Exception as exc:
        logger.error("Failed to log procurement evaluation: %s", exc)

    logger.info(
        "Agent 7 [%s] supplier=%s grade=%s rule=%s",
        status,
        candidate.get("supplier"),
        candidate.get("grade"),
        reason.get("rule"),
    )
    return payload


# ---------------------------------------------------------------------------
# Layer 1 — Sanctions check
# ---------------------------------------------------------------------------


def _layer1_sanctions(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    supplier = candidate["supplier"]
    try:
        match = check_ofac_match(supplier)
    except Exception as exc:
        logger.error("OFAC check failed for %s: %s", supplier, exc)
        # Fail closed — never silently approve when the sanctions check itself breaks.
        return _reason(
            rule="OFAC_SDN_CHECK_FAILED",
            value=str(exc),
            threshold=None,
            source="ofac.treasury.gov/SDN.XML",
        )

    if match:
        return _reason(
            rule="OFAC_SDN",
            value=supplier,
            threshold=None,
            source="ofac.treasury.gov/SDN.XML",
        )
    return None


# ---------------------------------------------------------------------------
# Layer 2 — Grade compatibility
# ---------------------------------------------------------------------------


def _layer2_grade_compatibility(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    grade = candidate["grade"]
    refinery = candidate["refinery"]
    try:
        compatible = check_grade_compatibility(grade, refinery)
    except Exception as exc:
        logger.error("Grade compatibility check failed (%s/%s): %s", grade, refinery, exc)
        return _reason(
            rule="GRADE_COMPATIBILITY_CHECK_FAILED",
            value=str(exc),
            threshold=None,
            source="neo4j:COMPATIBLE_WITH",
        )

    if not compatible:
        return _reason(
            rule="GRADE_INCOMPATIBLE",
            value=f"{grade} incompatible with {refinery}",
            threshold=None,
            source="MoPNG refinery technical specifications",
        )
    return None


# ---------------------------------------------------------------------------
# Layer 3 — Diversification cap (Fix 10, sequential)
# ---------------------------------------------------------------------------


class DiversificationTracker:
    """
    Holds running_share state for a single playbook evaluation run.
    Must be instantiated ONCE per Agent 6 -> Agent 7 batch and reused
    across every candidate in sorted-by-confidence order. This is the
    Fix 10 race-condition fix — never re-initialise per candidate.
    """

    def __init__(self) -> None:
        self.running_share: Dict[str, float] = {}
        self._loaded_suppliers: set[str] = set()

    def _ensure_loaded(self, supplier: str) -> None:
        if supplier in self._loaded_suppliers:
            return
        try:
            current = get_supplier_current_share(supplier)
        except Exception as exc:
            logger.error("Failed to load current share for %s: %s", supplier, exc)
            current = 0.0
        self.running_share[supplier] = float(current or 0.0)
        self._loaded_suppliers.add(supplier)

    def check_and_apply(
        self, supplier: str, delta: float
    ) -> Optional[Dict[str, Any]]:
        """
        Returns a reason dict (BLOCKED) if the cap is breached, else None
        and updates running_share in place (sequential mutation).
        """
        self._ensure_loaded(supplier)
        projected = self.running_share[supplier] + float(delta)

        if projected > MAX_SUPPLIER_SHARE_PCT:
            return _reason(
                rule="DIVERSIFICATION_CAP",
                value=round(projected, 4),
                threshold=MAX_SUPPLIER_SHARE_PCT,
                source="PostgreSQL current_supplier_shares",
            )

        # Approved — commit the mutation immediately (sequential, Fix 10)
        self.running_share[supplier] = projected
        return None


# ---------------------------------------------------------------------------
# Layer 4 — Operational constraints
# ---------------------------------------------------------------------------


def _layer4_operational(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    supplier = candidate["supplier"]
    vessel_class = candidate.get("vessel_class", "")
    departure_port = candidate.get("departure_port", "")
    proposed_volume = float(candidate.get("proposed_volume_mbd", 0.0))

    # 4a. Port capacity vs vessel class DWT (demo reference lookup — see TODO above)
    max_dwt = VESSEL_CLASS_MAX_DWT.get(vessel_class)
    if max_dwt is None:
        return _reason(
            rule="UNKNOWN_VESSEL_CLASS",
            value=vessel_class,
            threshold=list(VESSEL_CLASS_MAX_DWT.keys()),
            source="agent7:VESSEL_CLASS_MAX_DWT",
        )

    # 4b. Tanker availability near departure port
    available_vessels = _get_vessels_near_port(departure_port)
    if available_vessels < 1:
        return _reason(
            rule="TANKER_UNAVAILABLE",
            value=available_vessels,
            threshold=1,
            source="redis:vessels:live",
        )

    # 4c. Contract ceiling + take-or-pay floor
    try:
        headroom = get_contract_headroom(supplier)
    except Exception as exc:
        logger.error("Contract headroom check failed for %s: %s", supplier, exc)
        return _reason(
            rule="CONTRACT_HEADROOM_CHECK_FAILED",
            value=str(exc),
            threshold=None,
            source="neo4j:Contract",
        )

    max_volume = float(headroom.get("max_volume_mbd", 0.0))
    take_or_pay_floor = float(headroom.get("take_or_pay_floor", 0.0))
    current_volume = float(headroom.get("current_volume_mbd", 0.0))

    if current_volume + proposed_volume > max_volume:
        return _reason(
            rule="CONTRACT_CEILING",
            value=round(current_volume + proposed_volume, 4),
            threshold=max_volume,
            source="neo4j:Contract.max_volume_mbd",
        )

    if proposed_volume < take_or_pay_floor:
        # PARTIAL, not BLOCKED — flagged with penalty note, still approvable upstream
        return {
            "rule": "TAKE_OR_PAY_PENALTY",
            "value": proposed_volume,
            "threshold": take_or_pay_floor,
            "source": "neo4j:Contract.take_or_pay_floor",
            "partial": True,
        }

    return None


# ---------------------------------------------------------------------------
# Public entry point — validator(candidate, playbook_id)
# ---------------------------------------------------------------------------

_default_tracker = DiversificationTracker()


def new_diversification_tracker() -> DiversificationTracker:
    """
    Call this ONCE at the start of each Agent 6 batch (per playbook run)
    and reuse the same tracker instance across all validator() calls for
    that batch. This is what makes Layer 3 sequential (Fix 10).
    """
    return DiversificationTracker()


def validator(
    candidate: Dict[str, Any],
    playbook_id: Optional[UUID] = None,
    tracker: Optional[DiversificationTracker] = None,
) -> Dict[str, Any]:
    """
    Run a single candidate through all four constraint layers in order.
    Stops at the first BLOCKED layer. Returns APPROVED/PARTIAL/BLOCKED.

    Parameters
    ----------
    candidate : dict
        Shape confirmed with Agent 6 — see module docstring.
    playbook_id : UUID, optional
        Current playbook run, for PostgreSQL audit logging.
    tracker : DiversificationTracker, optional
        Shared sequential state for Layer 3. If not supplied, a module-level
        default tracker is used — callers doing a full batch MUST pass their
        own tracker (via new_diversification_tracker()) to avoid leaking
        state across unrelated batches.
    """
    tracker = tracker or _default_tracker

    # Layer 1
    reason = _layer1_sanctions(candidate)
    if reason:
        return _result(status="BLOCKED", candidate=candidate, reason=reason, playbook_id=playbook_id)

    # Layer 2
    reason = _layer2_grade_compatibility(candidate)
    if reason:
        return _result(status="BLOCKED", candidate=candidate, reason=reason, playbook_id=playbook_id)

    # Layer 3 (sequential, Fix 10)
    # Convert proposed volume (mb/day) into a share-of-total-imports delta,
    # since running_share and MAX_SUPPLIER_SHARE_PCT are both fractions of
    # total daily consumption, not raw mb/day volumes.
    delta_share = float(candidate.get("proposed_volume_mbd", 0.0)) / INDIA_DAILY_CONSUMPTION_MBD
    reason = tracker.check_and_apply(candidate["supplier"], delta_share)
    if reason:
        return _result(status="PARTIAL", candidate=candidate, reason=reason, playbook_id=playbook_id)

    # Layer 4
    reason = _layer4_operational(candidate)
    if reason:
        status = "PARTIAL" if reason.get("partial") else "BLOCKED"
        return _result(status=status, candidate=candidate, reason=reason, playbook_id=playbook_id)

    return _result(
        status="APPROVED",
        candidate=candidate,
        reason=_reason(rule="ALL_LAYERS_PASSED", value=None, threshold=None, source="agent7"),
        playbook_id=playbook_id,
    )


def validate_candidate(
    candidate: Dict[str, Any],
    playbook_id: Optional[UUID] = None,
    tracker: Optional[DiversificationTracker] = None,
) -> Dict[str, Any]:
    """
    Public alias matching Person A's confirmed Agent 6 call site:
        validate_candidate(candidate, playbook_id)

    Agent 6 owns its own loop and its own DiversificationTracker instance
    (create one via new_diversification_tracker() at the start of each
    playbook's evaluation batch, then pass it into every call here so
    Layer 3 stays sequential across the whole batch — Fix 10).

    If Agent 6 does NOT pass a tracker, a module-level default tracker is
    used, which is fine for isolated/manual testing but NOT safe for a
    real multi-candidate batch, since state would persist across unrelated
    playbook runs. Always pass a fresh tracker per playbook.
    """
    return validator(candidate, playbook_id=playbook_id, tracker=tracker)


def validate_batch(
    candidates: List[Dict[str, Any]],
    playbook_id: Optional[UUID] = None,
) -> List[Dict[str, Any]]:
    """
    Convenience wrapper for Agent 6: validates a full rejection-retry batch
    sorted by confidence descending, using ONE shared DiversificationTracker
    so Layer 3 is sequential across the whole batch (Fix 10).
    """
    tracker = new_diversification_tracker()
    sorted_candidates = sorted(
        candidates, key=lambda c: c.get("confidence", 0.0), reverse=True
    )
    return [
        validator(candidate, playbook_id=playbook_id, tracker=tracker)
        for candidate in sorted_candidates
    ]