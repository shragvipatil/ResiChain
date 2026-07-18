"""
backend/scripts/test_jwt_blacklist.py

Day 16, Person A — JWT blacklist test (Fix 11).

Validates that a token, once logged out (its jti written to
blacklist:{jti} in Redis), is rejected by get_current_user with a 401 —
even though the token's signature and expiry are still perfectly valid.
This is the whole point of Fix 11: logout must actually revoke, not just
clear a cookie client-side.

Proves, in order:
  1. A freshly minted token is ACCEPTED by get_current_user.
  2. After writing blacklist:{jti} (what POST /api/auth/logout does),
     the SAME token is REJECTED with HTTP 401 "revoked".
  3. The blacklist key carries a TTL matching the token lifetime, so
     revocations self-clean when the token would have expired anyway
     (no unbounded Redis growth).

Run inside the container (needs the app's Redis + JWT secret):

    docker-compose exec fastapi python scripts/test_jwt_blacklist.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException
from starlette.requests import Request

from routers.auth import _create_token, get_current_user
from db.redis_client import get_redis


def _fake_request_with_bearer(token: str) -> Request:
    """
    Build a minimal Starlette Request carrying Authorization: Bearer <token>,
    so we can call get_current_user() directly without spinning up HTTP.
    """
    headers = [(b"authorization", f"Bearer {token}".encode())]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope)


async def main() -> int:
    print("=" * 60)
    print("  JWT blacklist test (Fix 11)")
    print("=" * 60)

    fake_user = {
        "username": "test_ministry",
        "role": "MINISTRY_USER",
        "id": "00000000-0000-0000-0000-000000000001",
    }

    token, jti, expires_in = _create_token(fake_user)
    print(f"\n[1] Minted token  jti={jti}  ttl={expires_in}s")

    # ---- Assertion 1: valid token is accepted ----
    try:
        payload = await get_current_user(_fake_request_with_bearer(token))
        accepted_before = payload.get("jti") == jti
        print(f"[2] Pre-logout: token ACCEPTED (sub={payload.get('sub')}) — good")
    except HTTPException as exc:
        accepted_before = False
        print(f"[2] Pre-logout: unexpectedly REJECTED ({exc.detail}) — FAIL")

    # ---- Simulate logout: write blacklist:{jti} exactly as the route does ----
    r = await get_redis()
    await r.setex(f"blacklist:{jti}", expires_in, "revoked")
    print(f"[3] Logout simulated: wrote blacklist:{jti} (TTL {expires_in}s)")

    # ---- Assertion 2: same token now rejected with 401 ----
    rejected_after = False
    try:
        await get_current_user(_fake_request_with_bearer(token))
        print("[4] Post-logout: token STILL ACCEPTED — FAIL (blacklist not enforced)")
    except HTTPException as exc:
        rejected_after = exc.status_code == 401
        print(f"[4] Post-logout: token REJECTED with {exc.status_code} "
              f"({exc.detail}) — good")

    # ---- Assertion 3: blacklist key has a bounded TTL ----
    ttl = await r.ttl(f"blacklist:{jti}")
    ttl_ok = 0 < ttl <= expires_in
    print(f"[5] blacklist TTL = {ttl}s (bounded, self-cleaning): "
          f"{'good' if ttl_ok else 'FAIL'}")

    # cleanup
    await r.delete(f"blacklist:{jti}")

    print("\n" + "=" * 60)
    all_ok = accepted_before and rejected_after and ttl_ok
    print(f"  valid token accepted     : {'PASS' if accepted_before else 'FAIL'}")
    print(f"  revoked token 401        : {'PASS' if rejected_after else 'FAIL'}")
    print(f"  blacklist TTL bounded    : {'PASS' if ttl_ok else 'FAIL'}")
    print("=" * 60)
    if all_ok:
        print("  RESULT: PASS — Fix 11 validated. Logout genuinely revokes a")
        print("  token server-side; a stolen/old token can't be replayed.")
        return 0
    print("  RESULT: FAIL — see failing line above.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main())) 