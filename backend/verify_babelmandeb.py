import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
user = os.getenv("NEO4J_USER", "neo4j")
password = os.getenv("NEO4J_PASSWORD")

driver = GraphDatabase.driver(uri, auth=(user, password))

with driver.session() as session:
    result = session.run(
        "MATCH (r:Route {name: $route_name})-[:PASSES_THROUGH]->(c:Chokepoint {name: $chokepoint_name}) "
        "RETURN r.name AS route, c.name AS chokepoint",
        route_name="Russia to Vadinar via Suez",
        chokepoint_name="Bab-el-Mandeb",
    )
    records = list(result)
    print(f"Found {len(records)} matching relationship(s)")
    for rec in records:
        print(dict(rec))

driver.close()