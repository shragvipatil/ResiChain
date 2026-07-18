from db.postgres_queries import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT entity_name, aliases, program, date_imposed FROM ofac_sdn WHERE length(entity_name) < 20 ORDER BY entity_name")
        for r in cur.fetchall():
            print(r)
