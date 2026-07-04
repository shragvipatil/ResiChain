from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
logger = logging.getLogger(__name__)

_NEO4J_URI = os.getenv("NEO4JURI", os.getenv("NEO4J_URI", "bolt://neo4j:7687"))
_NEO4J_USER = os.getenv("NEO4JUSER", os.getenv("NEO4J_USER", "neo4j"))
_NEO4J_PASSWORD = os.getenv("NEO4JPASSWORD", os.getenv("NEO4J_PASSWORD", "password"))
_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            _NEO4J_URI,
            auth=(_NEO4J_USER, _NEO4J_PASSWORD),
        )
    return _driver


async def init_neo4j() -> None:
    driver = _get_driver()
    driver.verify_connectivity()
    logger.info("Neo4j connectivity verified")


def close_neo4j() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
        logger.info("Neo4j driver closed")


def _run_query(
    query: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    driver = _get_driver()
    with driver.session(database=_NEO4J_DATABASE) as session:
        result = session.run(query, parameters or {})
        return [dict(record) for record in result]


def get_surviving_routes(blocked_chokepoints: List[str]) -> List[Dict[str, Any]]:
    query = """
    MATCH (s:Supplier)-[:SHIPS_VIA]->(r:Route)-[:ARRIVES_AT]->(p:Port)
    WHERE NOT EXISTS {
      MATCH (r)-[:PASSES_THROUGH]->(c:Chokepoint)
      WHERE c.name IN $blocked_chokepoints
    }
    RETURN
      s.name AS supplier,
      r.name AS route,
      p.name AS arrival_port,
      coalesce(r.avg_transit_days, 0) AS avg_transit_days,
      coalesce(r.distance_km, 0) AS distance_km
    ORDER BY s.name, r.name
    """
    return _run_query(query, {"blocked_chokepoints": blocked_chokepoints or []})


def get_all_supplier_grades() -> List[Dict[str, Any]]:
    query = """
    MATCH (s:Supplier)-[:PRODUCES]->(g:CrudeGrade)
    RETURN
      s.name AS supplier,
      g.name AS grade,
      coalesce(g.api_gravity, 0.0) AS api_gravity,
      coalesce(g.sulfur_pct, 0.0) AS sulfur_pct,
      coalesce(g.viscosity, '') AS viscosity
    ORDER BY s.name
    """
    return _run_query(query)


def check_grade_compatibility(grade_name: str, refinery_name: str) -> bool:
    query = """
    MATCH (g:CrudeGrade {name: $grade_name})
    MATCH (r:Refinery {name: $refinery_name})
    RETURN EXISTS((g)-[:COMPATIBLE_WITH]->(r)) AS compatible
    """
    rows = _run_query(
        query,
        {"grade_name": grade_name, "refinery_name": refinery_name},
    )
    return bool(rows[0]["compatible"]) if rows else False


def get_supplier_current_share(supplier_name: str) -> float:
    query = """
    MATCH (s:Supplier {name: $supplier_name})
    RETURN coalesce(s.import_share_pct, 0.0) AS share_pct
    """
    rows = _run_query(query, {"supplier_name": supplier_name})
    if not rows:
        return 0.0
    share = float(rows[0]["share_pct"] or 0.0)
    return share / 100.0 if share > 1.0 else share


def get_contract_headroom(supplier_name: str) -> Dict[str, Any]:
    query = """
    MATCH (s:Supplier {name: $supplier_name})-[:HAS_CONTRACT]->(c:Contract)
    RETURN
      coalesce(c.max_volume_mbd, 0.0) AS max_volume_mbd,
      coalesce(c.take_or_pay_floor, 0.0) AS take_or_pay_floor,
      coalesce(c.current_volume_mbd, 0.0) AS current_volume_mbd,
      coalesce(c.reference, c.counterparty, '') AS contract_reference
    ORDER BY coalesce(c.expiry, '') DESC
    LIMIT 1
    """
    rows = _run_query(query, {"supplier_name": supplier_name})
    if not rows:
        return {
            "max_volume_mbd": 0.0,
            "take_or_pay_floor": 0.0,
            "current_volume_mbd": 0.0,
            "headroom_mbd": 0.0,
            "contract_reference": "",
        }

    row = rows[0]
    max_volume = float(row.get("max_volume_mbd", 0.0) or 0.0)
    current_volume = float(row.get("current_volume_mbd", 0.0) or 0.0)
    headroom = max(0.0, max_volume - current_volume)

    return {
        "max_volume_mbd": max_volume,
        "take_or_pay_floor": float(row.get("take_or_pay_floor", 0.0) or 0.0),
        "current_volume_mbd": current_volume,
        "headroom_mbd": headroom,
        "contract_reference": row.get("contract_reference", "") or "",
    }


def get_port_specs(port_name: str) -> Dict[str, Any]:
    query = """
    MATCH (p:Port {name: $port_name})
    RETURN
      p.name AS name,
      coalesce(p.max_vessel_dwt, 0.0) AS max_vessel_dwt,
      coalesce(p.country, '') AS country,
      coalesce(p.latitude, 0.0) AS latitude,
      coalesce(p.longitude, 0.0) AS longitude
    LIMIT 1
    """
    rows = _run_query(query, {"port_name": port_name})
    return rows[0] if rows else {}


def get_refinery_specs(refinery_name: str) -> Dict[str, Any]:
    query = """
    MATCH (r:Refinery {name: $refinery_name})
    OPTIONAL MATCH (p:Port)-[:SUPPLIES]->(r)
    OPTIONAL MATCH (sf:StorageFacility)-[:COVERS_REFINERY]->(r)
    OPTIONAL MATCH (g:CrudeGrade)-[:COMPATIBLE_WITH]->(r)
    RETURN
      r.name AS name,
      coalesce(r.capacity_mbd, 0.0) AS capacity_mbd,
      coalesce(r.owner, '') AS owner,
      coalesce(r.location, '') AS location,
      coalesce(r.compatible_share, 0.0) AS compatible_share,
      coalesce(p.name, '') AS port,
      coalesce(sf.name, '') AS spr_site,
      collect(DISTINCT g.name) AS compatible_grades
    LIMIT 1
    """
    rows = _run_query(query, {"refinery_name": refinery_name})
    return rows[0] if rows else {}


def get_compatible_share(refinery_name: str) -> float:
    query = """
    MATCH (r:Refinery {name: $refinery_name})
    RETURN coalesce(r.compatible_share, 0.0) AS compatible_share
    LIMIT 1
    """
    rows = _run_query(query, {"refinery_name": refinery_name})
    return float(rows[0]["compatible_share"] or 0.0) if rows else 0.0


def get_spr_total_volume() -> float:
    query = """
    MATCH (s:StorageFacility)
    RETURN sum(coalesce(s.capacity_mb, 0.0)) AS total_mb
    """
    rows = _run_query(query)
    return float(rows[0]["total_mb"] or 0.0) if rows else 0.0


def get_graph_for_visualization() -> Dict[str, Any]:
    nodes_query = """
    MATCH (n)
    RETURN id(n) AS id, labels(n) AS labels, properties(n) AS props
    """
    rels_query = """
    MATCH (a)-[r]->(b)
    RETURN id(a) AS source, id(b) AS target, type(r) AS type, properties(r) AS props
    """
    return {
        "nodes": _run_query(nodes_query),
        "edges": _run_query(rels_query),
    }


def getsurvivingroutes(blockedchokepoints: List[str]) -> List[Dict[str, Any]]:
    return get_surviving_routes(blockedchokepoints)


def checkgradecompatibility(gradename: str, refineryname: str) -> bool:
    return check_grade_compatibility(gradename, refineryname)


def getsuppliercurrentshare(suppliername: str) -> float:
    return get_supplier_current_share(suppliername)


def getcontractheadroom(suppliername: str) -> Dict[str, Any]:
    return get_contract_headroom(suppliername)


def getallsuppliergrades() -> List[Dict[str, Any]]:
    return get_all_supplier_grades()


def getrefineryspecs(refineryname: str) -> Dict[str, Any]:
    return get_refinery_specs(refineryname)


def getgraphforvisualization() -> Dict[str, Any]:
    return get_graph_for_visualization()


def getsprtotalvolume() -> float:
    return get_spr_total_volume()


def getcompatibleshare(refineryname: str) -> float:
    return get_compatible_share(refineryname)