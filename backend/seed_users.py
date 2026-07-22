"""
One-off seed script — creates test users with real bcrypt hashes.
Run once against a fresh users table:
    docker exec -it resichain_fastapi python seed_users.py
"""
from passlib.context import CryptContext
from db.postgres_queries import get_connection, init_db, get_user_by_username

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS_TO_SEED = [
    {"username": "admin", "email": "admin@resichain.local", "password": "AdminPass123!", "role": "admin"},
    {"username": "analyst", "email": "analyst@resichain.local", "password": "AnalystPass123!", "role": "analyst"},
    {"username": "procurement", "email": "procurement@resichain.local", "password": "ProcPass123!", "role": "procurement"},
]

def seed():
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cur:
            for u in USERS_TO_SEED:
                existing = get_user_by_username(u["username"])
                if existing:
                    print(f"Skipping '{u['username']}' — already exists (id={existing['id']}).")
                    continue

                password_hash = pwd_context.hash(u["password"])
                cur.execute(
                    """
                    INSERT INTO users (username, email, password_hash, role)
                    VALUES (%(username)s, %(email)s, %(password_hash)s, %(role)s)
                    RETURNING id
                    """,
                    {
                        "username": u["username"],
                        "email": u["email"],
                        "password_hash": password_hash,
                        "role": u["role"],
                    },
                )
                row = cur.fetchone()
                print(f"Created '{u['username']}' (role={u['role']}, id={row['id']}).")

    print("\nSeed complete. Login credentials (plaintext — for local testing only):")
    for u in USERS_TO_SEED:
        print(f"  {u['username']} / {u['password']}  (role: {u['role']})")

if __name__ == "__main__":
    seed()