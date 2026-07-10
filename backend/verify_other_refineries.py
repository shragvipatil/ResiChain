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
        "MATCH (n:Refinery {name: $name}) RETURN n.capacity_mbd AS cap, n.is_aggregate AS agg",
        name="Other India Refineries",
    )
    records = list(result)
    print(f"Found {len(records)} matching node(s)")
    for rec in records:
        print(dict(rec))

    result2 = session.run(
        "MATCH (g:CrudeGrade)-[:COMPATIBLE_WITH]->(n:Refinery {name: $name}) RETURN count(g) AS grade_count",
        name="Other India Refineries",
    )
    print(dict(result2.single()))

driver.close()