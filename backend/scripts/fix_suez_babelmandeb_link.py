from db.neo4j_queries import _run_query

def fix_suez_babelmandeb_transit():
    query = """
    MATCH (r:Route)-[:PASSES_THROUGH]->(c:Chokepoint {name: "Suez Canal"})
    MATCH (bem:Chokepoint {name: "Bab-el-Mandeb"})
    MERGE (r)-[:PASSES_THROUGH]->(bem)
    RETURN count(r) AS routes_updated
    """
    result = _run_query(query)
    print(f"Updated {result[0]['routes_updated']} Suez routes with Bab-el-Mandeb transit")

if __name__ == "__main__":
    fix_suez_babelmandeb_transit()