# ============================================================
# ResiChain — Agent 6 Fallback Validator
# TEMPORARY stand-in for Agent 7, used ONLY if agents.agent7
# has no validate_candidate() function yet.
#
# This implements the same 4-layer spec Person B is building:
#   Layer 1: OFAC sanctions check
#   Layer 2: Grade compatibility check
#   Layer 3: Diversification cap (sequential, Fix 10)
#   Layer 4: Operational checks (port capacity, tanker availability)
#
# UPDATED to match Person B's confirmed Agent 7 contract:
#   validate_candidate(candidate: dict, playbook_id: str | None = None) -> dict
# running_share is no longer passed in — this fallback now owns that
# state internally, the same way Person B's real agent7.py will.
#
# Once Person B's real agents/agent7.py exposes a matching
# validate_candidate() function, Agent 6 prefers that automatically
# and this file is never called.
# ============================================================

import logging
from db.postgres_queries import check_ofac_match
from db.neo4j_queries import check_grade_compatibility, get_port_specs, get_supplier_current_share

logger = logging.getLogger(__name__)

MAX_SUPPLIER_SHARE = 0.40  # Fix 10 — diversification cap

# ---- Internal diversification state (mirrors what Agent 7 will own) ----
# Keyed by playbook_id (a single procurement run). Each run starts fresh —
# suppliers get their real current share pulled from Neo4j the first time
# they're seen in that run, then updated sequentially as candidates pass.
# NOTE: this is a process-memory cache — fine for a single-instance demo,
# not durable across restarts. Person B's real agent7.py may persist this
# differently; not a concern for the fallback's purpose.
_run_state_cache: dict = {}


def _get_run_state(playbook_id) -> dict:
    run_key = playbook_id or "_default"
    if run_key not in _run_state_cache:
        _run_state_cache[run_key] = {}
    return _run_state_cache[run_key]


async def validate_candidate(candidate: dict, playbook_id: str = None) -> dict:
    """
    Fallback 4-layer constraint validator.

    candidate expects keys:
        supplier, grade, refinery, proposed_volume_mbd, vessel_class (optional),
        arrival_port (optional)

    playbook_id: identifies the current procurement run. Used only to key
        this validator's internal running_share cache — no other logic
        depends on it.

    Returns:
        {
            "status": "APPROVED" | "BLOCKED" | "PARTIAL",
            "reason": {"rule": str, "value": Any, "threshold": Any, "source": str} | None,
            "adjusted_volume_mbd": float
        }
    """
    supplier = candidate["supplier"]
    grade = candidate.get("grade", "")
    refinery = candidate.get("refinery", "")
    volume_mbd = candidate.get("proposed_volume_mbd", 0.15)

    run_state = _get_run_state(playbook_id)
    if supplier not in run_state:
        try:
            run_state[supplier] = get_supplier_current_share(supplier)
        except Exception as e:
            logger.error(f"Failed to fetch current share for {supplier}: {e}")
            run_state[supplier] = 0.0

    # ---- Layer 1: Sanctions (OFAC) ---------------------------
    try:
        sanctioned = check_ofac_match(supplier)
    except Exception as e:
        logger.error(f"OFAC check failed for {supplier}: {e}")
        sanctioned = False

    if sanctioned:
        return {
            "status": "BLOCKED",
            "reason": {
                "rule": "OFAC_SDN",
                "value": supplier,
                "threshold": None,
                "source": "ofac.treasury.gov/SDN.XML"
            },
            "adjusted_volume_mbd": 0.0
        }

    # ---- Layer 2: Grade compatibility ------------------------
    if refinery:
        try:
            compatible = check_grade_compatibility(grade, refinery)
        except Exception as e:
            logger.error(f"Grade compatibility check failed: {e}")
            compatible = True  # fail-open for demo stability

        if not compatible:
            return {
                "status": "BLOCKED",
                "reason": {
                    "rule": "GRADE_INCOMPATIBLE",
                    "value": f"{grade} not compatible with {refinery}",
                    "threshold": None,
                    "source": "Neo4j COMPATIBLE_WITH relationship"
                },
                "adjusted_volume_mbd": 0.0
            }

    # ---- Layer 3: Diversification cap (Fix 10 — sequential) --
    current_share = run_state[supplier]
    delta = _volume_to_share(volume_mbd)
    projected_share = current_share + delta

    if projected_share > MAX_SUPPLIER_SHARE:
        headroom = max(0.0, MAX_SUPPLIER_SHARE - current_share)
        if headroom <= 0.001:
            return {
                "status": "BLOCKED",
                "reason": {
                    "rule": "DIVERSIFICATION_CAP",
                    "value": f"{supplier} already at {current_share*100:.1f}%",
                    "threshold": f"{MAX_SUPPLIER_SHARE*100:.0f}%",
                    "source": "MAX_SUPPLIER_SHARE policy"
                },
                "adjusted_volume_mbd": 0.0
            }
        else:
            # PARTIAL — approve only up to the remaining headroom
            adjusted_volume = _share_to_volume(headroom)
            run_state[supplier] = MAX_SUPPLIER_SHARE
            return {
                "status": "PARTIAL",
                "reason": {
                    "rule": "DIVERSIFICATION_CAP",
                    "value": (
                        f"{supplier} at {current_share*100:.1f}%, "
                        f"requested would reach {projected_share*100:.1f}%"
                    ),
                    "threshold": f"{MAX_SUPPLIER_SHARE*100:.0f}%",
                    "source": "MAX_SUPPLIER_SHARE policy"
                },
                "adjusted_volume_mbd": round(adjusted_volume, 4)
            }

    # ---- Layer 4: Operational checks --------------------------
    if refinery:
        port_name = candidate.get("arrival_port", "")
        if port_name:
            try:
                port_specs = get_port_specs(port_name)
            except Exception:
                port_specs = {}

            max_dwt = port_specs.get("max_vessel_dwt")
            vessel_class = candidate.get("vessel_class", "VLCC")
            required_dwt = 320000 if vessel_class == "VLCC" else 160000

            if max_dwt and max_dwt < required_dwt:
                return {
                    "status": "BLOCKED",
                    "reason": {
                        "rule": "PORT_CAPACITY",
                        "value": f"{port_name} max DWT {max_dwt} < required {required_dwt}",
                        "threshold": max_dwt,
                        "source": "Neo4j Port node"
                    },
                    "adjusted_volume_mbd": 0.0
                }

    # ---- All layers passed — fully approved --------------------
    run_state[supplier] = projected_share
    return {
        "status": "APPROVED",
        "reason": None,
        "adjusted_volume_mbd": round(volume_mbd, 4)
    }


def _volume_to_share(volume_mbd: float, total_daily_mbd: float = 5.1) -> float:
    """Converts a volume in mbd to its fraction of total daily consumption."""
    return volume_mbd / total_daily_mbd if total_daily_mbd > 0 else 0.0


def _share_to_volume(share: float, total_daily_mbd: float = 5.1) -> float:
    """Converts a share fraction back to mbd volume."""
    return share * total_daily_mbd 