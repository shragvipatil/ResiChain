from db.postgres_queries import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT entity_name, aliases, program, date_imposed FROM ofac_sdn
            WHERE date_imposed IN ('2024-01-01', '2024-1-1')
               OR entity_name IN ('Russia', 'Iran', 'Venezuela', 'North Korea', 'Cuba', 'Syria')
               OR LENGTH(entity_name) < 15
            ORDER BY date_imposed
        """)
        rows = cur.fetchall()
        for r in rows:
            print(r)
        print("total suspicious rows:", len(rows))
