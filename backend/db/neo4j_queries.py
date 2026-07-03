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

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(_NEO4J_URI, auth=(_NEO4J_USER, _NEO4J_PASSWORD))
    return _driver


def _run_query(query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    driver = _get_driver()
    with driver.session() as session:
        result = session.run(query, parameters or {})
        return [dict(record) for record in result]


def get_surviving_routes(blocked_chokepoints: List[str]) -> List[Dict[str, Any]]:
    query = """
    MATCH (s:Supplier)-[:SHIPSVIA]->(r:Route)-[:ARRIVESAT]->(p:Port)
    WHERE NOT EXISTS {
      MATCH (r)-[:PASSESTHROUGH]->(c:Chokepoint)
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
      coalesce(g.api_gravity, g.apigravity, 0.0) AS api_gravity,
      coalesce(g.sulfur_pct, g.sulfurpct, 0.0) AS sulfur_pct,
      coalesce(g.viscosity, 0.0) AS viscosity
    ORDER BY s.name
    """
    return _run_query(query)


def check_grade_compatibility(grade_name: str, refinery_name: str) -> bool:
    query = """
    MATCH (g:CrudeGrade {name: $grade_name})
    MATCH (r:Refinery {name: $refinery_name})
    RETURN EXISTS((g)-[:COMPATIBLEWITH]->(r)) AS compatible
    """
    rows = _run_query(query, {"grade_name": grade_name, "refinery_name": refinery_name})
    return bool(rows[0]["compatible"]) if rows else False


def get_supplier_current_share(supplier_name: str) -> float:
    query = """
    MATCH (s:Supplier {name: $supplier_name})
    RETURN coalesce(s.import_share_pct, s.importsharepct, 0.0) AS share_pct
    """
    rows = _run_query(query, {"supplier_name": supplier_name})
    if not rows:
        return 0.0
    share = float(rows[0]["share_pct"] or 0.0)
    return share / 100.0 if share > 1.0 else share


def get_contract_headroom(supplier_name: str) -> Dict[str, Any]:
    query = """
    MATCH (s:Supplier {name: $supplier_name})-[:HASCONTRACT]->(c:Contract)
    RETURN
      coalesce(c.max_volume_mbd, c.maxvolumembd, 0.0) AS max_volume_mbd,
      coalesce(c.take_or_pay_floor, c.takeorpayfloor, 0.0) AS take_or_pay_floor,
      coalesce(c.current_volume_mbd, c.currentvolumembd, 0.0) AS current_volume_mbd,
      coalesce(c.contract_reference, c.contractreference, c.counterparty, '') AS contract_reference
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
      coalesce(p.max_vessel_dwt, p.maxvesseldwt, 0.0) AS max_vessel_dwt,
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
    OPTIONAL MATCH (sf:StorageFacility)-[:COVERSREFINERY]->(r)
    OPTIONAL MATCH (g:CrudeGrade)-[:COMPATIBLEWITH]->(r)
    RETURN
      r.name AS name,
      coalesce(r.capacity_mbd, r.capacitymbd, 0.0) AS capacity_mbd,
      coalesce(p.name, '') AS port,
      coalesce(sf.name, '') AS spr_site,
      collect(DISTINCT g.name) AS compatible_grades
    LIMIT 1
    """
    rows = _run_query(query, {"refinery_name": refinery_name})
    if not rows:
        return {}
    row = rows[0]
    row["compatibleshare"] = get_compatible_share(refinery_name)
    return row


def get_compatible_share(refinery_name: str) -> float:
    query = """
    MATCH (r:Refinery {name: $refinery_name})
    OPTIONAL MATCH (g:CrudeGrade)-[:COMPATIBLEWITH]->(r)
    RETURN count(DISTINCT g) AS compatible_count
    """
    rows = _run_query(query, {"refinery_name": refinery_name})
    compatible_count = int(rows[0]["compatible_count"]) if rows else 0
    if compatible_count <= 0:
        return 0.0
    return 1.0


def get_spr_total_volume() -> float:
    query = """
    MATCH (s:StorageFacility)
    RETURN sum(coalesce(s.capacity_mb, s.capacitymb, 0.0)) AS total_mb
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


# Contract-compatible aliases
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