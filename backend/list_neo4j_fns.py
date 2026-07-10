
import db.neo4j_queries as n
names = [x for x in dir(n) if not x.startswith("_")]
print(names)