import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD"))
)

query = """
MATCH (n)
RETURN labels(n)[0] AS label, count(n) AS count
ORDER BY label
"""

with driver.session() as session:
    result = session.run(query)
    for record in result:
        print(record["label"], record["count"])

driver.close()