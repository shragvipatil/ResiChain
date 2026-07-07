"""
agents/agent4.py
================
ResiChain AI v2.0 — Agent 4: Compound Disruption Analyzer

Purpose:
    Detect when multiple corridors are simultaneously at crisis-level risk
    (>= 0.65) and calculate the compound probability that at least one
    critical corridor fails. This is a single deterministic formula,
    not an LLM call — kept intentionally simple and auditable, since this
    number gets shown directly on the Ministry dashboard.

Formula:
    compound_risk = 1 - Π(1 - corridor_risk_i)   for all i where risk_i >= CRISIS_THRESHOLD

    Example: Hormuz 0.82, Red Sea 0.87 both above threshold —
             compound_risk = 1 - (1 - 0.82)(1 - 0.87) = 1 - (0.18 * 0.13) = 0.9766 ≈ 0.977

Architecture:
    - Triggered by LangGraph's conditional edge into crisis mode, same
      >= 0.65 threshold Agent 3 already uses for CRISIS system mode.
    - Reads the live risk vector from Redis risk:state (written by Agent 3).
    - When 2+ corridors are simultaneously critical, queries Neo4j via
      Person B's get_surviving_routes() to find which routes survive
      when ALL critical corridors are blocked at once — not just the
      single-worst one.
    - Returns a structured result that LangGraph carries forward into
      the Agent 5 / Agent 6 parallel branch.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from db.redis_client import get_redis
from db.neo4j_queries import get_surviving_routes

logger = logging.getLogger(__name__)

CRISIS_THRESHOLD = 0.65  # matches Agent 3's CRISIS system-mode threshold

def _is_numeric_score(value) -> bool:
    """
    True for real int/float values, EXCLUDING bool (bool is a subclass
    of int in Python — isinstance(True, (int, float)) is True). Found
    by Person B: a boolean metadata marker in risk:state was silently
    passing the old isinstance(v, (int, float)) filter as if it were a
    real corridor risk score.
    """
    return type(value) in (int, float)


# Short Redis code -> full Neo4j Chokepoint.name.
# Values match agent6.py's CHOKEPOINT_SHORT_TO_FULL exactly, as pulled from
# Person B's pushed fix — keeping both files' mappings identical matters,
# since a mismatch here would silently break compound-event route lookups
# even though single-corridor Agent 6 lookups work fine.
CHOKEPOINT_SHORT_TO_FULL = {
    "Hormuz":  "Strait of Hormuz",
    "Suez":    "Suez Canal",
    "Cape":    "Cape of Good Hope",
    "Red_Sea": "Red Sea",
}


async def run_agent4_analysis() -> Dict[str, Any]:
    """
    Standalone entry point — reads live risk:state and returns the compound
    disruption result directly. Useful for calling from a test endpoint or
    from a demo-inject flow without going through the full LangGraph.
    """
    risk_vector = await _get_risk_vector()
    return _analyze(risk_vector)


async def run_agent4(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph node function for Agent 4. ASYNC — matches Agent 1, Agent 3,
    and Agent 6's pattern.

    IMPORTANT — sync/async mismatch to resolve during graph wiring:
    Agent 5's run_agent5() is currently a SYNC function, while Agent 1,
    Agent 3, Agent 6, and this Agent 4 are all ASYNC. When the actual
    LangGraph graph gets built, this needs a deliberate decision — either
    wrap Agent 5 in a small async adapter node, or confirm the installed
    LangGraph version's executor handles mixed sync/async nodes cleanly.
    Flagging now rather than guessing, since getting this wrong produces a
    RuntimeError at graph invocation time, not at import time — much
    harder to debug live.

    Reads from LangGraph state:
        - risk_vector (optional) — reused if Agent 3 already populated it
          earlier in this same graph run. Falls back to reading Redis
          risk:state directly if not present, so this node also works
          when invoked standalone (e.g. in a test).

    Writes to LangGraph state:
        - compound_risk       : float | None (None if not a compound event)
        - blocked_chokepoints : list[str]  (short codes, e.g. ["Hormuz", "Red_Sea"])
        - is_compound_event   : bool
        - surviving_routes    : list[dict] from Neo4j — only populated
                                 when is_compound_event is True
    """
    risk_vector = state.get("risk_vector")
    if not risk_vector:
        risk_vector = await _get_risk_vector()

    result = _analyze(risk_vector)

    return {
        **state,
        "compound_risk": result["compound_risk"],
        "blocked_chokepoints": result["blocked_chokepoints"],
        "is_compound_event": result["is_compound_event"],
        "surviving_routes": result.get("surviving_routes", []),
    }


async def _get_risk_vector() -> Dict[str, float]:
    """Reads the live risk vector from Redis risk:state (written by Agent 3)."""
    try:
        r = await get_redis()
        data = await r.get("risk:state")
        if not data:
            logger.warning("Agent 4: risk:state empty in Redis — nothing to analyze")
            return {}
        raw = json.loads(data)
        # risk:state also carries "updated_at" / "updated_corridors" strings —
        # filter to numeric corridor scores only (and exclude bool, which
        # isinstance(v, (int, float)) alone would wrongly accept).
        return {
            k: v for k, v in raw.items()
            if _is_numeric_score(v)
        }
    except Exception as e:
        logger.error(f"Agent 4: Failed to read risk:state: {e}")
        return {}


def _analyze(risk_vector: Dict[str, float]) -> Dict[str, Any]:
    """
    Core compound-risk calculation. Pure function, no I/O for the math
    itself — easy to unit test independently of Redis.
    """
    blocked = [
        corridor for corridor, score in risk_vector.items()
        if score >= CRISIS_THRESHOLD
    ]

    if len(blocked) < 2:
        # 0 or 1 corridor critical is a normal single-corridor crisis,
        # not a compound event — Agent 4 has nothing to add here.
        logger.info(
            f"Agent 4: {len(blocked)} corridor(s) at/above {CRISIS_THRESHOLD} "
            f"— not a compound event"
        )
        return {
            "compound_risk": None,
            "blocked_chokepoints": blocked,
            "is_compound_event": False,
            "surviving_routes": [],
        }

    # compound_risk = 1 - Π(1 - risk_i)
    product_survival = 1.0
    for corridor in blocked:
        product_survival *= (1.0 - risk_vector[corridor])
    compound_risk = round(1.0 - product_survival, 4)

    logger.warning(
        f"Agent 4: COMPOUND DISRUPTION DETECTED — "
        f"{blocked} simultaneously critical, compound_risk={compound_risk}"
    )

    full_names = [CHOKEPOINT_SHORT_TO_FULL.get(c, c) for c in blocked]

    try:
        surviving_routes = get_surviving_routes(full_names)
    except Exception as e:
        logger.error(f"Agent 4: get_surviving_routes failed: {e}")
        surviving_routes = []

    if not surviving_routes:
        logger.critical(
            f"Agent 4: NO surviving routes with {blocked} all blocked — "
            f"total import disruption on all monitored corridors"
        )

    return {
        "compound_risk": compound_risk,
        "blocked_chokepoints": blocked,
        "is_compound_event": True,
        "surviving_routes": surviving_routes,
    } 