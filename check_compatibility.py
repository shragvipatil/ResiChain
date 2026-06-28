import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD"))
)

query = """
MATCH (g:CrudeGrade {name: 'Venezuelan Merey'}), (r:Refinery {name: 'Kochi BPCL'})
OPTIONAL MATCH (g)-[rel:COMPATIBLE_WITH]->(r)
RETURN g.name AS grade, r.name AS refinery, rel IS NOT NULL AS compatible
"""

with driver.session() as session:
    result = session.run(query).single()
    print(result["grade"], "->", result["refinery"], "compatible =", result["compatible"])

driver.close()