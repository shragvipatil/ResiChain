from __future__ import annotations

import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", os.getenv("NEO4JURI", "bolt://neo4j:7687"))
NEO4J_USER = os.getenv("NEO4J_USER", os.getenv("NEO4JUSER", "neo4j"))
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", os.getenv("NEO4JPASSWORD", ""))
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

if not NEO4J_URI or not NEO4J_USER or not NEO4J_PASSWORD:
    raise ValueError("Missing Neo4j credentials in environment variables.")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def create_constraints(tx):
    queries = [
        "CREATE CONSTRAINT supplier_name IF NOT EXISTS FOR (n:Supplier) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT crudegrade_name IF NOT EXISTS FOR (n:CrudeGrade) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT chokepoint_name IF NOT EXISTS FOR (n:Chokepoint) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT route_name IF NOT EXISTS FOR (n:Route) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT port_name IF NOT EXISTS FOR (n:Port) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT refinery_name IF NOT EXISTS FOR (n:Refinery) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT storage_name IF NOT EXISTS FOR (n:StorageFacility) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT contract_reference IF NOT EXISTS FOR (n:Contract) REQUIRE n.reference IS UNIQUE",
        "CREATE CONSTRAINT sanction_entity IF NOT EXISTS FOR (n:Sanction) REQUIRE n.entity IS UNIQUE",
    ]
    for q in queries:
        tx.run(q)


def seed_suppliers(tx):
    suppliers = [
        {"name": "Saudi Arabia", "country": "Saudi Arabia", "import_share_pct": 18.2},
        {"name": "Iraq", "country": "Iraq", "import_share_pct": 22.1},
        {"name": "UAE", "country": "UAE", "import_share_pct": 8.4},
        {"name": "Russia", "country": "Russia", "import_share_pct": 21.3},
        {"name": "USA", "country": "USA", "import_share_pct": 5.7},
        {"name": "Kuwait", "country": "Kuwait", "import_share_pct": 6.8},
        {"name": "Venezuela", "country": "Venezuela", "import_share_pct": 2.5},
        {"name": "Iran", "country": "Iran", "import_share_pct": 1.0},
    ]
    for s in suppliers:
        tx.run("""
            MERGE (n:Supplier {name: $name})
            SET n.country = $country,
                n.import_share_pct = $import_share_pct
        """, s)


def seed_crude_grades(tx):
    grades = [
        {"name": "Arab Light", "api_gravity": 32.8, "sulfur_pct": 1.8, "viscosity": "medium"},
        {"name": "Basra Light", "api_gravity": 29.7, "sulfur_pct": 2.9, "viscosity": "medium"},
        {"name": "Basra Medium", "api_gravity": 29.0, "sulfur_pct": 3.4, "viscosity": "medium"},
        {"name": "Murban", "api_gravity": 40.5, "sulfur_pct": 0.7, "viscosity": "light"},
        {"name": "Urals", "api_gravity": 31.1, "sulfur_pct": 1.5, "viscosity": "medium"},
        {"name": "WTI", "api_gravity": 39.6, "sulfur_pct": 0.24, "viscosity": "light"},
        {"name": "Kuwait Export", "api_gravity": 31.4, "sulfur_pct": 2.5, "viscosity": "medium"},
        {"name": "Venezuelan Merey", "api_gravity": 16.0, "sulfur_pct": 2.5, "viscosity": "heavy"},
    ]
    for g in grades:
        tx.run("""
            MERGE (n:CrudeGrade {name: $name})
            SET n.api_gravity = $api_gravity,
                n.sulfur_pct = $sulfur_pct,
                n.viscosity = $viscosity
        """, g)


def seed_chokepoints(tx):
    points = [
        {"name": "Strait of Hormuz", "daily_capacity_mbd": 21.0},
        {"name": "Bab-el-Mandeb", "daily_capacity_mbd": 8.8},
        {"name": "Suez Canal", "daily_capacity_mbd": 5.5},
        {"name": "Cape of Good Hope", "daily_capacity_mbd": 99.0},
    ]
    for c in points:
        tx.run("""
            MERGE (n:Chokepoint {name: $name})
            SET n.daily_capacity_mbd = $daily_capacity_mbd,
                n.current_risk = 0.0
        """, c)


def seed_ports(tx):
    ports = [
        {"name": "Jamnagar Sikka", "country": "India", "max_vessel_dwt": 320000, "latitude": 22.4236, "longitude": 69.8222},
        {"name": "Vadinar", "country": "India", "max_vessel_dwt": 320000, "latitude": 22.4670, "longitude": 69.8400},
        {"name": "Kochi", "country": "India", "max_vessel_dwt": 150000, "latitude": 9.9667, "longitude": 76.2667},
        {"name": "Paradip", "country": "India", "max_vessel_dwt": 180000, "latitude": 20.3167, "longitude": 86.6167},
        {"name": "Vizag", "country": "India", "max_vessel_dwt": 200000, "latitude": 17.6868, "longitude": 83.2185},
    ]
    for p in ports:
        tx.run("""
            MERGE (n:Port {name: $name})
            SET n.country = $country,
                n.max_vessel_dwt = $max_vessel_dwt,
                n.latitude = $latitude,
                n.longitude = $longitude
        """, p)


def seed_refineries(tx):
    refineries = [
        {"name": "Jamnagar RIL", "owner": "Reliance", "capacity_mbd": 1.24, "location": "Jamnagar", "compatible_share": 0.90},
        {"name": "Vadinar Nayara", "owner": "Nayara", "capacity_mbd": 0.405, "location": "Vadinar", "compatible_share": 0.85},
        {"name": "Kochi BPCL", "owner": "BPCL", "capacity_mbd": 0.31, "location": "Kochi", "compatible_share": 0.65},
        {"name": "Paradip IOCL", "owner": "IOCL", "capacity_mbd": 0.30, "location": "Paradip", "compatible_share": 0.80},
    ]
    for r in refineries:
        tx.run("""
            MERGE (n:Refinery {name: $name})
            SET n.owner = $owner,
                n.capacity_mbd = $capacity_mbd,
                n.location = $location,
                n.compatible_share = $compatible_share
        """, r)


def seed_other_refineries(tx):
    tx.run("""
        MERGE (n:Refinery {name: 'Other India Refineries'})
        SET n.owner = 'Various (aggregate)',
            n.capacity_mbd = 2.9,
            n.location = 'National (aggregate)',
            n.compatible_share = 1.0,
            n.is_aggregate = true,
            n.note = 'Aggregate node representing remaining Indian refining capacity not individually modeled; used only for national-scope weight normalization in simulation._compute_refinery_weights(), never shown as a dashboard card.'
    """)


def seed_storage(tx):
    sites = [
        {"name": "Visakhapatnam SPR", "type": "SPR", "capacity_mb": 9.0, "location": "Visakhapatnam"},
        {"name": "Mangalore SPR", "type": "SPR", "capacity_mb": 12.0, "location": "Mangalore"},
        {"name": "Padur SPR", "type": "SPR", "capacity_mb": 17.0, "location": "Padur"},
    ]
    for s in sites:
        tx.run("""
            MERGE (n:StorageFacility {name: $name})
            SET n.type = $type,
                n.capacity_mb = $capacity_mb,
                n.location = $location
        """, s)


def seed_routes(tx):
    routes = [
        {"name": "Saudi to Jamnagar via Hormuz", "avg_transit_days": 8, "distance_km": 6500},
        {"name": "UAE to Kochi via Hormuz", "avg_transit_days": 7, "distance_km": 5800},
        {"name": "Russia to Vadinar via Suez", "avg_transit_days": 18, "distance_km": 14000},
        {"name": "Saudi to Kochi via Cape", "avg_transit_days": 12, "distance_km": 9800},
        {"name": "UAE to Paradip via Cape", "avg_transit_days": 15, "distance_km": 12000},
        {"name": "Iraq to Paradip via Hormuz", "avg_transit_days": 9, "distance_km": 7200},
        {"name": "Iraq to Paradip via Cape", "avg_transit_days": 16, "distance_km": 12500},
        {"name": "Kuwait to Vizag via Hormuz", "avg_transit_days": 9, "distance_km": 7100},
    ]
    for r in routes:
        tx.run("""
            MERGE (n:Route {name: $name})
            SET n.avg_transit_days = $avg_transit_days,
                n.distance_km = $distance_km
        """, r)


def seed_contracts(tx):
    contracts = [
        {"reference": "CNTR-SAUDI-001", "counterparty": "Saudi Arabia", "max_volume_mbd": 0.60, "current_volume_mbd": 0.32, "take_or_pay_floor": 0.20, "expiry": "2027-12-31"},
        {"reference": "CNTR-UAE-001", "counterparty": "UAE", "max_volume_mbd": 0.40, "current_volume_mbd": 0.18, "take_or_pay_floor": 0.10, "expiry": "2027-12-31"},
        {"reference": "CNTR-RUSSIA-001", "counterparty": "Russia", "max_volume_mbd": 0.40, "current_volume_mbd": 0.38, "take_or_pay_floor": 0.15, "expiry": "2027-12-31"},
        {"reference": "CNTR-IRAQ-001", "counterparty": "Iraq", "max_volume_mbd": 0.55, "current_volume_mbd": 0.28, "take_or_pay_floor": 0.18, "expiry": "2027-12-31"},
    ]
    for c in contracts:
        tx.run("""
            MERGE (n:Contract {reference: $reference})
            SET n.counterparty = $counterparty,
                n.max_volume_mbd = $max_volume_mbd,
                n.current_volume_mbd = $current_volume_mbd,
                n.take_or_pay_floor = $take_or_pay_floor,
                n.expiry = $expiry
        """, c)


def seed_sanctions(tx):
    sanctions = [
        {"entity": "Iran", "issuer": "OFAC", "date_imposed": "2018-11-05", "scope": "Crude exports restrictions"},
    ]
    for s in sanctions:
        tx.run("""
            MERGE (n:Sanction {entity: $entity})
            SET n.issuer = $issuer,
                n.date_imposed = $date_imposed,
                n.scope = $scope
        """, s)


def seed_relationships(tx):
    queries = [
        "MATCH (s:Supplier {name:'Saudi Arabia'}), (g:CrudeGrade {name:'Arab Light'}) MERGE (s)-[:PRODUCES]->(g)",
        "MATCH (s:Supplier {name:'Iraq'}), (g:CrudeGrade {name:'Basra Light'}) MERGE (s)-[:PRODUCES]->(g)",
        "MATCH (s:Supplier {name:'Iraq'}), (g:CrudeGrade {name:'Basra Medium'}) MERGE (s)-[:PRODUCES]->(g)",
        "MATCH (s:Supplier {name:'UAE'}), (g:CrudeGrade {name:'Murban'}) MERGE (s)-[:PRODUCES]->(g)",
        "MATCH (s:Supplier {name:'Russia'}), (g:CrudeGrade {name:'Urals'}) MERGE (s)-[:PRODUCES]->(g)",
        "MATCH (s:Supplier {name:'USA'}), (g:CrudeGrade {name:'WTI'}) MERGE (s)-[:PRODUCES]->(g)",
        "MATCH (s:Supplier {name:'Kuwait'}), (g:CrudeGrade {name:'Kuwait Export'}) MERGE (s)-[:PRODUCES]->(g)",
        "MATCH (s:Supplier {name:'Venezuela'}), (g:CrudeGrade {name:'Venezuelan Merey'}) MERGE (s)-[:PRODUCES]->(g)",

        "MATCH (g:CrudeGrade), (r:Refinery) WHERE NOT (g.name = 'Venezuelan Merey' AND r.name = 'Kochi BPCL') AND r.name <> 'Other India Refineries' MERGE (g)-[:COMPATIBLE_WITH]->(r)",

        "MATCH (g:CrudeGrade), (r:Refinery {name:'Other India Refineries'}) MERGE (g)-[:COMPATIBLE_WITH]->(r)",

        "MATCH (s:Supplier {name:'Saudi Arabia'}), (r:Route {name:'Saudi to Jamnagar via Hormuz'}) MERGE (s)-[:SHIPS_VIA]->(r)",
        "MATCH (s:Supplier {name:'Saudi Arabia'}), (r:Route {name:'Saudi to Kochi via Cape'}) MERGE (s)-[:SHIPS_VIA]->(r)",
        "MATCH (s:Supplier {name:'UAE'}), (r:Route {name:'UAE to Kochi via Hormuz'}) MERGE (s)-[:SHIPS_VIA]->(r)",
        "MATCH (s:Supplier {name:'UAE'}), (r:Route {name:'UAE to Paradip via Cape'}) MERGE (s)-[:SHIPS_VIA]->(r)",
        "MATCH (s:Supplier {name:'Russia'}), (r:Route {name:'Russia to Vadinar via Suez'}) MERGE (s)-[:SHIPS_VIA]->(r)",
        "MATCH (s:Supplier {name:'Iraq'}), (r:Route {name:'Iraq to Paradip via Hormuz'}) MERGE (s)-[:SHIPS_VIA]->(r)",
        "MATCH (s:Supplier {name:'Iraq'}), (r:Route {name:'Iraq to Paradip via Cape'}) MERGE (s)-[:SHIPS_VIA]->(r)",
        "MATCH (s:Supplier {name:'Kuwait'}), (r:Route {name:'Kuwait to Vizag via Hormuz'}) MERGE (s)-[:SHIPS_VIA]->(r)",

        "MATCH (r:Route {name:'Saudi to Jamnagar via Hormuz'}), (c:Chokepoint {name:'Strait of Hormuz'}) MERGE (r)-[:PASSES_THROUGH]->(c)",
        "MATCH (r:Route {name:'UAE to Kochi via Hormuz'}), (c:Chokepoint {name:'Strait of Hormuz'}) MERGE (r)-[:PASSES_THROUGH]->(c)",
        "MATCH (r:Route {name:'Iraq to Paradip via Hormuz'}), (c:Chokepoint {name:'Strait of Hormuz'}) MERGE (r)-[:PASSES_THROUGH]->(c)",
        "MATCH (r:Route {name:'Kuwait to Vizag via Hormuz'}), (c:Chokepoint {name:'Strait of Hormuz'}) MERGE (r)-[:PASSES_THROUGH]->(c)",
        "MATCH (r:Route {name:'Russia to Vadinar via Suez'}), (c:Chokepoint {name:'Suez Canal'}) MERGE (r)-[:PASSES_THROUGH]->(c)",
        "MATCH (r:Route {name:'Russia to Vadinar via Suez'}), (c:Chokepoint {name:'Bab-el-Mandeb'}) MERGE (r)-[:PASSES_THROUGH]->(c)",
        "MATCH (r:Route {name:'Saudi to Kochi via Cape'}), (c:Chokepoint {name:'Cape of Good Hope'}) MERGE (r)-[:PASSES_THROUGH]->(c)",
        "MATCH (r:Route {name:'UAE to Paradip via Cape'}), (c:Chokepoint {name:'Cape of Good Hope'}) MERGE (r)-[:PASSES_THROUGH]->(c)",
        "MATCH (r:Route {name:'Iraq to Paradip via Cape'}), (c:Chokepoint {name:'Cape of Good Hope'}) MERGE (r)-[:PASSES_THROUGH]->(c)",

        "MATCH (r:Route {name:'Saudi to Jamnagar via Hormuz'}), (p:Port {name:'Jamnagar Sikka'}) MERGE (r)-[:ARRIVES_AT]->(p)",
        "MATCH (r:Route {name:'UAE to Kochi via Hormuz'}), (p:Port {name:'Kochi'}) MERGE (r)-[:ARRIVES_AT]->(p)",
        "MATCH (r:Route {name:'Russia to Vadinar via Suez'}), (p:Port {name:'Vadinar'}) MERGE (r)-[:ARRIVES_AT]->(p)",
        "MATCH (r:Route {name:'Saudi to Kochi via Cape'}), (p:Port {name:'Kochi'}) MERGE (r)-[:ARRIVES_AT]->(p)",
        "MATCH (r:Route {name:'UAE to Paradip via Cape'}), (p:Port {name:'Paradip'}) MERGE (r)-[:ARRIVES_AT]->(p)",
        "MATCH (r:Route {name:'Iraq to Paradip via Hormuz'}), (p:Port {name:'Paradip'}) MERGE (r)-[:ARRIVES_AT]->(p)",
        "MATCH (r:Route {name:'Iraq to Paradip via Cape'}), (p:Port {name:'Paradip'}) MERGE (r)-[:ARRIVES_AT]->(p)",
        "MATCH (r:Route {name:'Kuwait to Vizag via Hormuz'}), (p:Port {name:'Vizag'}) MERGE (r)-[:ARRIVES_AT]->(p)",

        "MATCH (p:Port {name:'Jamnagar Sikka'}), (r:Refinery {name:'Jamnagar RIL'}) MERGE (p)-[:SUPPLIES]->(r)",
        "MATCH (p:Port {name:'Vadinar'}), (r:Refinery {name:'Vadinar Nayara'}) MERGE (p)-[:SUPPLIES]->(r)",
        "MATCH (p:Port {name:'Kochi'}), (r:Refinery {name:'Kochi BPCL'}) MERGE (p)-[:SUPPLIES]->(r)",
        "MATCH (p:Port {name:'Paradip'}), (r:Refinery {name:'Paradip IOCL'}) MERGE (p)-[:SUPPLIES]->(r)",
        "MATCH (p:Port {name:'Vizag'}), (r:Refinery {name:'Paradip IOCL'}) MERGE (p)-[:SUPPLIES]->(r)",

        "MATCH (s:Supplier {name:'Iran'}), (n:Sanction {entity:'Iran'}) MERGE (s)-[:UNDER_SANCTION]->(n)",

        "MATCH (s:Supplier {name:'Saudi Arabia'}), (c:Contract {reference:'CNTR-SAUDI-001'}) MERGE (s)-[:HAS_CONTRACT]->(c)",
        "MATCH (s:Supplier {name:'UAE'}), (c:Contract {reference:'CNTR-UAE-001'}) MERGE (s)-[:HAS_CONTRACT]->(c)",
        "MATCH (s:Supplier {name:'Russia'}), (c:Contract {reference:'CNTR-RUSSIA-001'}) MERGE (s)-[:HAS_CONTRACT]->(c)",
        "MATCH (s:Supplier {name:'Iraq'}), (c:Contract {reference:'CNTR-IRAQ-001'}) MERGE (s)-[:HAS_CONTRACT]->(c)",

        "MATCH (sf:StorageFacility {name:'Visakhapatnam SPR'}), (r:Refinery {name:'Paradip IOCL'}) MERGE (sf)-[:COVERS_REFINERY]->(r)",
        "MATCH (sf:StorageFacility {name:'Mangalore SPR'}), (r:Refinery {name:'Kochi BPCL'}) MERGE (sf)-[:COVERS_REFINERY]->(r)",
        "MATCH (sf:StorageFacility {name:'Padur SPR'}), (r:Refinery {name:'Jamnagar RIL'}) MERGE (sf)-[:COVERS_REFINERY]->(r)",
    ]
    for q in queries:
        tx.run(q)


def print_counts(session):
    node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
    rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    print("Knowledge Graph seeded successfully.", flush=True)
    print(f"Neo4j database: {NEO4J_DATABASE}", flush=True)
    print(f"Node count: {node_count}", flush=True)
    print(f"Relationship count: {rel_count}", flush=True)


def main():
    driver.verify_connectivity()
    with driver.session(database=NEO4J_DATABASE) as session:
        session.execute_write(create_constraints)
        session.execute_write(seed_suppliers)
        session.execute_write(seed_crude_grades)
        session.execute_write(seed_chokepoints)
        session.execute_write(seed_ports)
        session.execute_write(seed_refineries)
        session.execute_write(seed_other_refineries)
        session.execute_write(seed_storage)
        session.execute_write(seed_routes)
        session.execute_write(seed_contracts)
        session.execute_write(seed_sanctions)
        session.execute_write(seed_relationships)
        print_counts(session)
    driver.close()


if __name__ == "__main__":
    main()