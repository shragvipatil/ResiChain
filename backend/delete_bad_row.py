from db.postgres_queries import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT entity_name, aliases, program, date_imposed, last_refreshed_at FROM ofac_sdn WHERE entity_name = %s", ("Russia",))
        print("row before delete:", cur.fetchone())

        cur.execute("DELETE FROM ofac_sdn WHERE entity_name = %s", ("Russia",))
        print("rows deleted:", cur.rowcount)
