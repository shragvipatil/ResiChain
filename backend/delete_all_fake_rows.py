from db.postgres_queries import get_connection

FAKE_ROWS = ["Russia", "Iran", "Venezuela"]

with get_connection() as conn:
    with conn.cursor() as cur:
        for name in FAKE_ROWS:
            cur.execute("SELECT entity_name, aliases, program, date_imposed FROM ofac_sdn WHERE entity_name = %s", (name,))
            print("before:", cur.fetchone())
        cur.execute("DELETE FROM ofac_sdn WHERE entity_name = ANY(%s)", (FAKE_ROWS,))
        print("total rows deleted:", cur.rowcount)
