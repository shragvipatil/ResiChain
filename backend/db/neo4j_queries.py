import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

uri = os.getenv("NEO4J_URI")
user = os.getenv("NEO4J_USER")
password = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

if not uri or not user or not password:
    raise ValueError("Missing Neo4j Aura credentials in environment variables.")

driver = GraphDatabase.driver(uri, auth=(user, password))


async def init_neo4j():
    driver.verify_connectivity()


def close_driver():
    driver.close()


def get_surviving_routes(blocked_chokepoints: list[str]) -> list[dict]:
    query = """
    MATCH (s:Supplier)-[:SHIPS_VIA]->(r:Route)
    WHERE NOT EXISTS {
        MATCH (r)-[:PASSES_THROUGH]->(c:Chokepoint)
        WHERE c.name IN $blocked_chokepoints
    }
    OPTIONAL MATCH (r)-[:ARRIVES_AT]->(p:Port)
    RETURN s.name AS supplier,
           r.name AS route,
           r.avg_transit_days AS avg_transit_days,
           r.distance_km AS distance_km,
           p.name AS arrival_port
    ORDER BY supplier, route
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(query, blocked_chokepoints=blocked_chokepoints)
        return [dict(record) for record in result]


def check_grade_compatibility(grade_name: str, refinery_name: str) -> bool:
    query = """
    MATCH (g:CrudeGrade {name: $grade_name}), (r:Refinery {name: $refinery_name})
    OPTIONAL MATCH (g)-[rel:COMPATIBLE_WITH]->(r)
    RETURN rel IS NOT NULL AS compatible
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        record = session.run(
            query,
            grade_name=grade_name,
            refinery_name=refinery_name
        ).single()
        return bool(record["compatible"]) if record else False


def get_supplier_current_share(supplier_name: str) -> float:
    query = """
    MATCH (s:Supplier {name: $supplier_name})
    RETURN s.import_share_pct AS share
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        record = session.run(query, supplier_name=supplier_name).single()
        if not record or record["share"] is None:
            return 0.0
        return float(record["share"]) / 100.0


def get_contract_headroom(supplier_name: str) -> dict:
    query = """
    MATCH (s:Supplier {name: $supplier_name})-[:HAS_CONTRACT]->(c:Contract)
    RETURN c.reference AS contract_reference,
           c.max_volume_mbd AS max_volume_mbd,
           c.take_or_pay_floor AS take_or_pay_floor,
           c.current_volume_mbd AS current_volume_mbd
    LIMIT 1
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        record = session.run(query, supplier_name=supplier_name).single()
        if not record:
            return {
                "contract_reference": None,
                "max_volume_mbd": 0.0,
                "take_or_pay_floor": 0.0,
                "current_volume_mbd": 0.0,
                "headroom_mbd": 0.0,
            }

        max_volume = float(record["max_volume_mbd"] or 0.0)
        current_volume = float(record["current_volume_mbd"] or 0.0)
        headroom = max(max_volume - current_volume, 0.0)

        return {
            "contract_reference": record["contract_reference"],
            "max_volume_mbd": max_volume,
            "take_or_pay_floor": float(record["take_or_pay_floor"] or 0.0),
            "current_volume_mbd": current_volume,
            "headroom_mbd": headroom,
        }


def get_all_supplier_grades() -> list[dict]:
    query = """
    MATCH (s:Supplier)-[:PRODUCES]->(g:CrudeGrade)
    RETURN s.name AS supplier,
           g.name AS grade,
           g.api_gravity AS api_gravity,
           g.sulfur_pct AS sulfur_pct,
           g.viscosity AS viscosity
    ORDER BY supplier, grade
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(query)
        return [dict(record) for record in result]


def get_refinery_specs(refinery_name: str) -> dict:
    query = """
    MATCH (r:Refinery {name: $refinery_name})
    OPTIONAL MATCH (g:CrudeGrade)-[:COMPATIBLE_WITH]->(r)
    OPTIONAL MATCH (p:Port)-[:SUPPLIES]->(r)
    OPTIONAL MATCH (sf:StorageFacility)-[:COVERS_REFINERY]->(r)
    RETURN r.name AS refinery,
           r.owner AS owner,
           r.capacity_mbd AS capacity_mbd,
           r.location AS location,
           r.compatible_share AS compatible_share,
           collect(DISTINCT g.name) AS compatible_grades,
           collect(DISTINCT p.name) AS ports,
           collect(DISTINCT sf.name) AS spr_sites
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        record = session.run(query, refinery_name=refinery_name).single()
        if not record:
            return {}

        return {
            "refinery": record["refinery"],
            "owner": record["owner"],
            "capacity_mbd": float(record["capacity_mbd"] or 0.0),
            "location": record["location"],
            "compatible_share": float(record["compatible_share"] or 0.0),
            "compatible_grades": [g for g in record["compatible_grades"] if g],
            "ports": [p for p in record["ports"] if p],
            "spr_sites": [s for s in record["spr_sites"] if s],
        }


def get_graph_for_visualization() -> dict:
    node_query = """
    MATCH (n)
    RETURN elementId(n) AS id,
           labels(n)[0] AS label,
           properties(n) AS properties
    """
    edge_query = """
    MATCH (a)-[r]->(b)
    RETURN elementId(r) AS id,
           type(r) AS type,
           elementId(a) AS source,
           elementId(b) AS target,
           properties(r) AS properties
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        nodes = [dict(record) for record in session.run(node_query)]
        edges = [dict(record) for record in session.run(edge_query)]
        return {"nodes": nodes, "edges": edges}


def get_spr_total_volume() -> float:
    query = """
    MATCH (s:StorageFacility)
    RETURN sum(s.capacity_mb) AS total_capacity
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        record = session.run(query).single()
        return float(record["total_capacity"] or 0.0) if record else 0.0


def get_compatible_share(refinery_name: str) -> float:
    query = """
    MATCH (r:Refinery {name: $refinery_name})
    RETURN r.compatible_share AS compatible_share
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        record = session.run(query, refinery_name=refinery_name).single()
        return float(record["compatible_share"] or 0.0) if record else 0.0


def get_all_chokepoints() -> list[dict]:
    query = """
    MATCH (c:Chokepoint)
    RETURN c.name AS name,
           c.daily_capacity_mbd AS daily_capacity_mbd,
           c.current_risk AS current_risk
    ORDER BY c.name
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(query)
        return [dict(record) for record in result]


def get_port_specs(port_name: str) -> dict:
    query = """
    MATCH (p:Port {name: $port_name})
    RETURN p.name AS name,
           p.country AS country,
           p.max_vessel_dwt AS max_vessel_dwt,
           p.latitude AS latitude,
           p.longitude AS longitude
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        record = session.run(query, port_name=port_name).single()
        return dict(record) if record else {}


def get_sanctioned_suppliers() -> list[dict]:
    query = """
    MATCH (s:Supplier)-[:UNDER_SANCTION]->(sn:Sanction)
    RETURN s.name AS supplier,
           sn.issuer AS issuer,
           sn.date_imposed AS date_imposed,
           sn.scope AS scope
    ORDER BY s.name
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(query)
        return [dict(record) for record in result]