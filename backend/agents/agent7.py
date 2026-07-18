from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Dict, List, Optional
from uuid import UUID

import redis as redis_lib
from dotenv import load_dotenv

from db.neo4j_queries import (
    check_grade_compatibility,
    get_contract_headroom,
    get_port_specs,
    get_supplier_current_share,
)
from db.postgres_queries import check_ofac_match, insert_procurement_evaluation

load_dotenv()
logger = logging.getLogger(__name__)

MAX_SUPPLIER_SHARE_PCT: float = float(
    os.getenv("MAX_SUPPLIER_SHARE_PCT", os.getenv("MAXSUPPLIERSHAREPCT", "0.40"))
)
INDIA_DAILY_CONSUMPTION_MBD: float = float(
    os.getenv("INDIA_DAILY_CONSUMPTION_MBD", "5.1")
)

VESSEL_CLASS_MAX_DWT = {
    "VLCC": 320_000,
    "Suezmax": 160_000,
    "Aframax": 120_000,
}

_redis_client: Optional[redis_lib.Redis] = None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(
            os.getenv("REDIS_URL", os.getenv("REDISURL", "redis://redis:6379/0")),
            decode_responses=True,
        )
    return _redis_client


def _get_vessels_near_port(departure_port: str) -> int:
    """
    Counts live vessels positioned at/near the given departure port.

    FIX (Day 18): previously matched on vessel["destination"], which is
    semantically wrong — "destination" means where a vessel is HEADING,
    not where it currently is. Demo seed data originally set destinations
    to Indian arrival ports (SIKKA, VADINAR, PARADIP) since those vessels
    represent tankers already en route TO India, which meant this check
    could never match a Gulf departure port (Ras Tanura, Fujairah, Basra
    Oil Terminal) and every procurement candidate was silently BLOCKED
    on TANKER_UNAVAILABLE regardless of actual chokepoint status.

    Now checks a dedicated "current_port" field (falling back to
    "location" or "destination" for backward compatibility with older
    seed/AIS payloads), which correctly represents where a vessel is
    physically positioned right now — the actual question this Layer 4
    check is trying to answer.
    """
    try:
        r = _get_redis()
        raw = r.get("vessels:live") or r.get("vesselslive")
        if not raw:
            return 0

        vessels = json.loads(raw)
        target = departure_port.strip().lower()
        count = 0
        for v in vessels:
            current_location = (
                v.get("current_port")
                or v.get("location")
                or v.get("destination", "")
            )
            if str(current_location).strip().lower() == target:
                count += 1
        return count
    except Exception as exc:
        logger.warning("Redis vessels read failed in Agent 7: %s", exc)
        return 0


def _reason(rule: str, value: Any, threshold: Any = None, source: str = "") -> Dict[str, Any]:
    return {
        "rule": rule,
        "value": value,
        "threshold": threshold,
        "source": source,
    }


def _result(
    *,
    status: str,
    candidate: Dict[str, Any],
    reason: Optional[Dict[str, Any]],
    playbook_id: Optional[UUID],
    adjusted_volume_mbd: Optional[float] = None,
) -> Dict[str, Any]:
    payload = {
        "option_id": candidate.get("option_id"),
        "supplier": candidate.get("supplier"),
        "grade": candidate.get("grade"),
        "status": status,
        "reason": reason,
        "confidence": candidate.get("confidence"),
        "adjusted_volume_mbd": round(float(adjusted_volume_mbd), 4)
        if adjusted_volume_mbd is not None
        else round(float(candidate.get("proposed_volume_mbd", 0.0)), 4),
    }

    try:
        insert_procurement_evaluation(
            playbook_id=playbook_id,
            option_id=candidate.get("option_id"),
            supplier=candidate.get("supplier"),
            grade=candidate.get("grade"),
            status=status,
            rule_triggered=(reason or {}).get("rule"),
            reason=reason or {},
            confidence=candidate.get("confidence"),
        )
    except Exception as exc:
        logger.error("Failed to log procurement evaluation in Agent 7: %s", exc)

    return payload


def _layer1_sanctions(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    supplier = candidate["supplier"]
    try:
        match = check_ofac_match(supplier)
    except Exception as exc:
        return _reason("OFAC_SDN_CHECK_FAILED", str(exc), None, "ofac_sdn")

    if match:
        return _reason("OFAC_SDN", supplier, None, "ofac_sdn")
    return None


def _layer2_grade_compatibility(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    grade = candidate["grade"]
    refinery = candidate["refinery"]
    try:
        compatible = check_grade_compatibility(grade, refinery)
    except Exception as exc:
        return _reason(
            "GRADE_COMPATIBILITY_CHECK_FAILED",
            str(exc),
            None,
            "neo4j:COMPATIBLEWITH",
        )

    if not compatible:
        return _reason(
            "GRADE_INCOMPATIBLE",
            f"{grade} incompatible with {refinery}",
            None,
            "neo4j:COMPATIBLEWITH",
        )
    return None


class DiversificationTracker:
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
        self,
        supplier: str,
        delta_share: float,
    ) -> tuple[Optional[Dict[str, Any]], float]:
        self._ensure_loaded(supplier)

        current = self.running_share[supplier]
        projected = current + float(delta_share)

        if projected > MAX_SUPPLIER_SHARE_PCT:
            headroom = max(0.0, MAX_SUPPLIER_SHARE_PCT - current)
            if headroom <= 1e-9:
                return (
                    _reason(
                        "DIVERSIFICATION_CAP",
                        round(projected, 4),
                        MAX_SUPPLIER_SHARE_PCT,
                        "neo4j:get_supplier_current_share",
                    ),
                    0.0,
                )

            self.running_share[supplier] = MAX_SUPPLIER_SHARE_PCT
            return (
                _reason(
                    "DIVERSIFICATION_CAP",
                    round(projected, 4),
                    MAX_SUPPLIER_SHARE_PCT,
                    "neo4j:get_supplier_current_share",
                ),
                headroom,
            )

        self.running_share[supplier] = projected
        return None, delta_share


def _layer4_operational(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    supplier = candidate["supplier"]
    arrival_port = candidate.get("arrival_port", "")
    departure_port = candidate.get("departure_port", "")
    vessel_class = candidate.get("vessel_class", "")
    proposed_volume = float(candidate.get("proposed_volume_mbd", 0.0))

    required_dwt = VESSEL_CLASS_MAX_DWT.get(vessel_class)
    if required_dwt is None:
        return _reason(
            "UNKNOWN_VESSEL_CLASS",
            vessel_class,
            list(VESSEL_CLASS_MAX_DWT.keys()),
            "agent7:VESSEL_CLASS_MAX_DWT",
        )

    if arrival_port:
        try:
            port_specs = get_port_specs(arrival_port)
        except Exception as exc:
            return _reason(
                "PORT_CAPACITY_CHECK_FAILED",
                str(exc),
                None,
                "neo4j:Port.max_vessel_dwt",
            )

        max_dwt = float(port_specs.get("max_vessel_dwt", 0.0) or 0.0)
        if max_dwt > 0 and max_dwt < required_dwt:
            return _reason(
                "PORT_CAPACITY",
                required_dwt,
                max_dwt,
                "neo4j:Port.max_vessel_dwt",
            )

    available_vessels = _get_vessels_near_port(departure_port)
    if available_vessels < 1:
        return _reason(
            "TANKER_UNAVAILABLE",
            available_vessels,
            1,
            "redis:vessels:live",
        )

    try:
        headroom = get_contract_headroom(supplier)
    except Exception as exc:
        return _reason(
            "CONTRACT_HEADROOM_CHECK_FAILED",
            str(exc),
            None,
            "neo4j:Contract",
        )

    max_volume = float(headroom.get("max_volume_mbd", 0.0) or 0.0)
    take_or_pay_floor = float(headroom.get("take_or_pay_floor", 0.0) or 0.0)
    current_volume = float(headroom.get("current_volume_mbd", 0.0) or 0.0)

    if max_volume > 0 and current_volume + proposed_volume > max_volume:
        return _reason(
            "CONTRACT_CEILING",
            round(current_volume + proposed_volume, 4),
            max_volume,
            "neo4j:Contract.max_volume_mbd",
        )

    if take_or_pay_floor > 0 and proposed_volume < take_or_pay_floor:
        return {
            "rule": "TAKE_OR_PAY_PENALTY",
            "value": proposed_volume,
            "threshold": take_or_pay_floor,
            "source": "neo4j:Contract.take_or_pay_floor",
            "partial": True,
        }

    return None


_default_tracker = DiversificationTracker()


def new_diversification_tracker() -> DiversificationTracker:
    return DiversificationTracker()


def validator(
    candidate: Dict[str, Any],
    playbook_id: Optional[UUID] = None,
    tracker: Optional[DiversificationTracker] = None,
) -> Dict[str, Any]:
    tracker = tracker or _default_tracker

    reason = _layer1_sanctions(candidate)
    if reason:
        return _result(
            status="BLOCKED",
            candidate=candidate,
            reason=reason,
            playbook_id=playbook_id,
            adjusted_volume_mbd=0.0,
        )

    reason = _layer2_grade_compatibility(candidate)
    if reason:
        return _result(
            status="BLOCKED",
            candidate=candidate,
            reason=reason,
            playbook_id=playbook_id,
            adjusted_volume_mbd=0.0,
        )

    proposed_volume = float(candidate.get("proposed_volume_mbd", 0.0))
    delta_share = (
        proposed_volume / INDIA_DAILY_CONSUMPTION_MBD
        if INDIA_DAILY_CONSUMPTION_MBD > 0
        else 0.0
    )
    diversification_reason, allowed_share = tracker.check_and_apply(
        candidate["supplier"],
        delta_share,
    )

    working_candidate = candidate
    diversification_partial = False
    adjusted_volume = proposed_volume

    if diversification_reason:
        adjusted_volume = allowed_share * INDIA_DAILY_CONSUMPTION_MBD

        if adjusted_volume <= 1e-9:
            return _result(
                status="BLOCKED",
                candidate=candidate,
                reason=diversification_reason,
                playbook_id=playbook_id,
                adjusted_volume_mbd=0.0,
            )

        diversification_partial = True
        working_candidate = copy.deepcopy(candidate)
        working_candidate["proposed_volume_mbd"] = adjusted_volume

    operational_reason = _layer4_operational(working_candidate)
    if operational_reason:
        status = "PARTIAL" if operational_reason.get("partial") else "BLOCKED"

        if diversification_partial:
            if status == "PARTIAL":
                final_adjusted_volume = adjusted_volume
            else:
                final_adjusted_volume = 0.0

            combined_reason = {
                "rule": "MULTI_LAYER_CONSTRAINT",
                "value": {
                    "diversification": diversification_reason,
                    "operational": {
                        k: v
                        for k, v in operational_reason.items()
                        if k != "partial"
                    },
                },
                "threshold": None,
                "source": "agent7",
            }
        else:
            final_adjusted_volume = proposed_volume if status == "PARTIAL" else 0.0
            combined_reason = {
                k: v for k, v in operational_reason.items() if k != "partial"
            }

        return _result(
            status=status,
            candidate=candidate,
            reason=combined_reason,
            playbook_id=playbook_id,
            adjusted_volume_mbd=final_adjusted_volume,
        )

    if diversification_partial:
        return _result(
            status="PARTIAL",
            candidate=candidate,
            reason=diversification_reason,
            playbook_id=playbook_id,
            adjusted_volume_mbd=adjusted_volume,
        )

    return _result(
        status="APPROVED",
        candidate=candidate,
        reason=_reason("ALL_LAYERS_PASSED", None, None, "agent7"),
        playbook_id=playbook_id,
        adjusted_volume_mbd=proposed_volume,
    )


def validate_candidate(
    candidate: Dict[str, Any],
    playbook_id: Optional[UUID] = None,
    tracker: Optional[DiversificationTracker] = None,
) -> Dict[str, Any]:
    return validator(candidate, playbook_id=playbook_id, tracker=tracker)


def validate_batch(
    candidates: List[Dict[str, Any]],
    playbook_id: Optional[UUID] = None,
) -> List[Dict[str, Any]]:
    tracker = new_diversification_tracker()
    sorted_candidates = sorted(
        candidates,
        key=lambda c: c.get("confidence", 0.0),
        reverse=True,
    )
    return [
        validator(candidate, playbook_id=playbook_id, tracker=tracker)
        for candidate in sorted_candidates
    ]