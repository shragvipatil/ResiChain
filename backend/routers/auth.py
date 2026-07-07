"""
routers/auth.py — Day 11: JWT authentication with blacklist + TOTP 2FA

Endpoints:
    POST /api/auth/login    — password (+ TOTP code if enabled) -> JWT in
                              httpOnly cookie (Person C's confirmed contract)
    POST /api/auth/logout   — blacklists the token's jti in Redis (Fix 11)
    POST /api/auth/2fa/setup — generates TOTP secret + QR data URL for
                              authenticator app enrollment
    GET  /api/auth/me       — returns the authenticated user's identity/role

Design notes:
- jti (JWT ID) is a uuid4 embedded in every token. Logout writes
  blacklist:{jti} to Redis with TTL = the token's remaining lifetime, so
  blacklist entries expire exactly when the token would have anyway —
  no unbounded growth (Fix 11 as specified).
- Cookie contract per Person C (frontend already wired for this):
  Set-Cookie: access_token=<jwt>; HttpOnly; SameSite=Lax; Path=/
- TOTP is REQUIRED for ADMIN and MINISTRY_USER once enrolled; other
  roles never need it. First login for those roles returns
  totp_setup_required so the frontend can drive enrollment.
- TOTP secrets are encrypted at rest with Fernet. TOTP_ENCRYPTION_KEY
  must be set in .env (generate once with:
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
"""

from __future__ import annotations

import base64
import io
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pyotp
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel

from db.redis_client import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change_me")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
TOTP_ROLES = {"ADMIN", "MINISTRY_USER"}  # roles that require 2FA once enrolled

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _get_fernet() -> Fernet:
    key = os.getenv("TOTP_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "TOTP_ENCRYPTION_KEY missing from .env — generate with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: Optional[str] = None


class TotpSetupRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _create_token(user: dict) -> tuple[str, str, int]:
    """Returns (token, jti, expires_in_seconds)."""
    jti = str(uuid.uuid4())
    expires_delta = timedelta(minutes=JWT_EXPIRE_MINUTES)
    expire_at = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": user["username"],
        "role": user["role"],
        "user_id": str(user["id"]),
        "jti": jti,
        "exp": expire_at,
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token, jti, int(expires_delta.total_seconds())


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def _extract_token(request: Request) -> str:
    """
    Accepts the token from EITHER the httpOnly cookie (browser flow,
    Person C's contract) or the Authorization: Bearer header (direct API
    calls, /docs testing, Person C's axios interceptor).
    """
    token = request.cookies.get("access_token")
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    raise HTTPException(status_code=401, detail="Not authenticated")


# ---------------------------------------------------------------------------
# The auth dependency — use this on any route that requires login.
# Checks signature, expiry, AND the Redis blacklist (Fix 11).
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> dict:
    token = _extract_token(request)
    payload = _decode_token(token)

    jti = payload.get("jti")
    if jti:
        r = await get_redis()
        if await r.exists(f"blacklist:{jti}"):
            raise HTTPException(status_code=401, detail="Token has been revoked")

    return payload


def require_roles(*roles: str):
    """Dependency factory: require_roles('ADMIN', 'MINISTRY_USER')."""
    async def checker(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user
    return checker


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/login")
async def login(body: LoginRequest, response: Response):
    from db.postgres_queries import get_user_by_username  # Person B helper

    user = get_user_by_username(body.username)
    if not user or not pwd_context.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # TOTP gate for privileged roles
    if user["role"] in TOTP_ROLES:
        if not user.get("totp_enabled"):
            # First login for a privileged role: signal frontend to run
            # 2FA enrollment before a session is granted.
            return {"totp_setup_required": True, "role": user["role"]}
        if not body.totp_code:
            return {"totp_code_required": True}
        secret = _get_fernet().decrypt(
            user["totp_secret_encrypted"].encode()
        ).decode()
        if not pyotp.TOTP(secret).verify(body.totp_code, valid_window=1):
            raise HTTPException(status_code=401, detail="Invalid TOTP code")

    token, jti, expires_in = _create_token(user)

    # Person C's confirmed cookie contract — httpOnly so JS can never
    # read it; SameSite=Lax; Path=/.
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=expires_in,
    )

    return {
        "message": "Login successful",
        "role": user["role"],
        "username": user["username"],
        "expires_in": expires_in,
        # Also returned in body for non-browser clients / direct API use;
        # browser clients should rely on the cookie.
        "access_token": token,
        "token_type": "bearer",
    }


@router.post("/logout")
async def logout(request: Request, response: Response):
    """
    Fix 11: blacklist the token's jti with TTL equal to the token's
    remaining lifetime, then clear the cookie. Any further request with
    this token gets 401 from get_current_user's blacklist check.
    """
    token = _extract_token(request)
    payload = _decode_token(token)

    jti = payload.get("jti")
    exp = payload.get("exp")
    if jti and exp:
        remaining = int(exp - datetime.now(timezone.utc).timestamp())
        if remaining > 0:
            r = await get_redis()
            await r.setex(f"blacklist:{jti}", remaining, "revoked")
            logger.info(f"Auth: blacklisted jti={jti} for {remaining}s")

    response.delete_cookie("access_token", path="/")
    return {"message": "Logged out"}


@router.post("/2fa/setup")
async def totp_setup(body: TotpSetupRequest):
    """
    Generates a TOTP secret for a privileged-role user, stores it
    encrypted, and returns a QR code data URL for authenticator app
    enrollment. Requires password re-verification (not just a session)
    since this changes a security factor.
    """
    from db.postgres_queries import get_user_by_username, set_user_totp

    user = get_user_by_username(body.username)
    if not user or not pwd_context.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if user["role"] not in TOTP_ROLES:
        raise HTTPException(status_code=400, detail="2FA not required for this role")

    secret = pyotp.random_base32()
    encrypted = _get_fernet().encrypt(secret.encode()).decode()
    set_user_totp(user["id"], encrypted, True)

    provisioning_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user["email"], issuer_name="ResiChain"
    )

    # QR code as data URL (spec: "return a QR code data URL")
    try:
        import qrcode
        img = qrcode.make(provisioning_uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        # qrcode package not installed — provisioning URI alone still
        # works (user can enter the secret manually in their app).
        logger.warning("qrcode package missing — returning provisioning URI only")
        qr_data_url = None

    return {
        "provisioning_uri": provisioning_uri,
        "qr_data_url": qr_data_url,
        "message": "Scan with your authenticator app, then log in with the 6-digit code",
    }


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"username": user["sub"], "role": user["role"], "user_id": user["user_id"]} 