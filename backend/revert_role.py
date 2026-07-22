from db.postgres_queries import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET role = %(role)s WHERE username = %(username)s",
            {"role": "admin", "username": "admin"},
        )
print("reverted to lowercase for bypass test")