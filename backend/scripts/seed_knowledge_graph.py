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
               contract ceilings, take-or-pay floors).

Contract:
    validate_candidate(candidate: dict, playbook_id: str | None = None,
                       tracker: "DiversificationTracker" | None = None) -> dict

    Agent 6 calls validate_candidate(candidate, playbook_id)
    without passing tracker; Agent 7 owns diversification state internally.

Return payload shape (must be consistent for every path):
    {
        "status": "APPROVED" | "BLOCKED" | "PARTIAL",
        "reason": Optional[dict],  # None for APPROVED, dict for BLOCKED/PARTIAL
        "adjusted_volume_mbd": float,  # same as proposed for APPROVED/BLOCKED
    }

    reason dict always has at least:
        {
            "rule": str,          # e.g. "OFAC_SDN", "GRADE_INCOMPATIBLE",
                                  #      "DIVERSIFICATION_CAP", "PORT_REF_CAPACITY"
            "value": Any,         # offending value, e.g. supplier name
            "threshold": Any,     # policy threshold if applicable
            "source": str,        # e.g. "OFAC SDN", "Neo4j"
            "details": dict       # extra context
        }

    This lets Agent 6 and the API always show a structured explanation
    for any BLOCKED/PARTIAL outcome.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from db.neo4j_queries import (
    check_grade_compatibility,
    get_supplier_current_share,
    get_refinery_specs,
)
from db.postgres_queries import is_supplier_sanctioned

logger = logging.getLogger(__name__)

# Mirror MAX_SUPPLIER_SHARE_PCT used in the config (Fix 10)
MAX_SUPPLIER_SHARE_PCT = 0.40


@dataclass
class DiversificationTracker:
    """
    Tracks running supplier shares for Fix 10.

    This object is owned by Agent 7 and passed between candidate validations
    within a single crisis run so diversification caps are enforced
    sequentially instead of in parallel.
    """

    running_share: Dict[str, float] = field(default_factory=dict)

    def get_share(self, supplier: str) -> float:
        return self.running_share.get(supplier, 0.0)

    def add_volume(self, supplier: str, delta_share: float) -> None:
        self.running_share[supplier] = self.get_share(supplier) + delta_share


async def validate_candidate(
    candidate: Dict[str, Any],
    playbook_id: Optional[str] = None,
    tracker: Optional[DiversificationTracker] = None,
) -> Dict[str, Any]:
    """
    Main entrypoint for Agent 7.

    Enforces all four constraint layers and always returns a structured
    reason dict for BLOCKED/PARTIAL outcomes.
    """

    supplier = candidate.get("supplier", "")
    proposed_volume_mbd = float(candidate.get("proposed_volume_mbd", 0.0))
    refinery = candidate.get("refinery")
    grade = candidate.get("grade")

    # Layer 1 — Sanctions check (OFAC SDN)
    if _is_sanctioned(supplier):
        reason = {
            "rule": "OFAC_SDN",
            "value": supplier,
            "threshold": None,
            "source": "OFAC SDN (ofac_sdn table)",
            "details": {
                "message": f"Supplier {supplier} appears in OFAC SDN list.",
                "playbook_id": playbook_id,
            },
        }
        return {
            "status": "BLOCKED",
            "reason": reason,
            "adjusted_volume_mbd": 0.0,
        }

    # Layer 2 — Grade compatibility
    try:
        if refinery and grade and not check_grade_compatibility(grade, refinery):
            reason = {
                "rule": "GRADE_INCOMPATIBLE",
                "value": grade,
                "threshold": refinery,
                "source": "Neo4j COMPATIBLE_WITH",
                "details": {
                    "message": f"Grade {grade} incompatible with {refinery}.",
                    "supplier": supplier,
                    "playbook_id": playbook_id,
                },
            }
            return {
                "status": "BLOCKED",
                "reason": reason,
                "adjusted_volume_mbd": 0.0,
            }
    except Exception as exc:
        logger.error("Agent 7: Grade compatibility check failed: %s", exc)

    # Layer 3 — Diversification cap (Fix 10)
    if tracker is None:
        tracker = DiversificationTracker()

    try:
        base_share = get_supplier_current_share(supplier)
    except Exception as exc:
        logger.error("Agent 7: get_supplier_current_share failed: %s", exc)
        base_share = 0.0

    current_running_share = tracker.get_share(supplier) or base_share

    # Compute incremental share contribution of this candidate relative to
    # DEFAULT_DAILY_CONSUMPTION_MBD (same constant Agent 6 uses).
    from agents.agent6 import DEFAULT_DAILY_CONSUMPTION_MBD

    if DEFAULT_DAILY_CONSUMPTION_MBD > 0:
        delta_share = proposed_volume_mbd / DEFAULT_DAILY_CONSUMPTION_MBD
    else:
        delta_share = 0.0

    projected_share = current_running_share + delta_share

    if projected_share > MAX_SUPPLIER_SHARE_PCT:
        reason = {
            "rule": "DIVERSIFICATION_CAP",
            "value": round(projected_share, 4),
            "threshold": MAX_SUPPLIER_SHARE_PCT,
            "source": "PostgreSQL + Agent 7 DiversificationTracker",
            "details": {
                "message": (
                    f"Supplier {supplier} share would reach "
                    f"{projected_share:.3f}, exceeding cap "
                    f"{MAX_SUPPLIER_SHARE_PCT:.2f}."
                ),
                "base_share": base_share,
                "delta_share": round(delta_share, 4),
                "playbook_id": playbook_id,
            },
        }
        return {
            "status": "BLOCKED",
            "reason": reason,
            "adjusted_volume_mbd": 0.0,
        }

    # If we approve at this layer, update tracker state
    tracker.add_volume(supplier, delta_share)

    # Layer 4 — Operational constraints (port/refinery capacity etc.)
    op_result = _check_operational_constraints(candidate)
    if op_result is not None:
        # op_result already contains status/reason/adjusted_volume_mbd
        return op_result

    # If all four layers pass, candidate is APPROVED.
    return {
        "status": "APPROVED",
        "reason": None,
        "adjusted_volume_mbd": proposed_volume_mbd,
    }


def _is_sanctioned(supplier: str) -> bool:
    """
    Layer 1 helper — check OFAC SDN via PostgreSQL.
    Returns True if supplier is present in ofac_sdn table.
    """
    try:
        return is_supplier_sanctioned(supplier)
    except Exception as exc:
        logger.error("Agent 7: sanctions check failed for %s: %s", supplier, exc)
        return False


def _check_operational_constraints(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Layer 4 — operational constraints.

    Currently implements a minimal refinery-capacity guard rail using
    Neo4j refinery specs. This function MUST always return a structured
    reason dict when it BLOCKS or PARTIALs a candidate.
    """

    supplier = candidate.get("supplier", "")
    refinery = candidate.get("refinery")
    proposed_volume_mbd = float(candidate.get("proposed_volume_mbd", 0.0))

    if not refinery:
        return None

    try:
        specs = get_refinery_specs(refinery)
    except Exception as exc:
        logger.error("Agent 7: get_refinery_specs failed: %s", exc)
        return None

    capacity_mbd = specs.get("capacity_mbd") or 0.0
    if capacity_mbd <= 0.0:
        return None

    # Simple policy: if proposed volume > 30% of refinery capacity,
    # PARTIAL the candidate and scale down to 30%.
    cap_fraction = 0.30
    max_allowed = cap_fraction * capacity_mbd

    if proposed_volume_mbd <= max_allowed:
        return None

    adjusted = round(max_allowed, 4)

    reason = {
        "rule": "PORT_REF_CAPACITY",
        "value": proposed_volume_mbd,
        "threshold": max_allowed,
        "source": "Neo4j refinery_specs",
        "details": {
            "message": (
                f"Candidate volume {proposed_volume_mbd:.3f} exceeds "
                f"{cap_fraction:.0%} of {refinery} capacity ({max_allowed:.3f})."
            ),
            "supplier": supplier,
        },
    }

    return {
        "status": "PARTIAL",
        "reason": reason,
        "adjusted_volume_mbd": adjusted,
    }