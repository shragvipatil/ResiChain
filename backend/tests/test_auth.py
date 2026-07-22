# tests/test_auth.py
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from jose import jwt

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-ci-do-not-use-in-prod")
os.environ.setdefault("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())

import routers.auth as auth


def make_user(username="analyst1", role="PROCUREMENT_ANALYST", password="secret123",
              totp_enabled=False, totp_secret_encrypted=None, user_id="user-1",
              email="analyst1@resichain.test"):
    # bcrypt has a hard 72-byte input limit; truncate defensively so test
    # passwords never trip passlib's internal self-test regardless of the
    # installed bcrypt/passlib version combo.
    safe_password = password[:72]
    return {
        "id": user_id,
        "username": username,
        "email": email,
        "role": role,
        "password_hash": auth.pwd_context.hash(safe_password),
        "totp_enabled": totp_enabled,
        "totp_secret_encrypted": totp_secret_encrypted,
    }, safe_password


def make_token(payload_overrides=None, expire_minutes=60):
    jti = "test-jti-123"
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    payload = {
        "sub": "analyst1",
        "role": "PROCUREMENT_ANALYST",
        "user_id": "user-1",
        "jti": jti,
        "exp": expire_at,
    }
    if payload_overrides:
        payload.update(payload_overrides)
    token = jwt.encode(payload, auth.JWT_SECRET_KEY, algorithm=auth.JWT_ALGORITHM)
    return token, jti


class FakeRequest:
    def __init__(self, cookie_token=None, bearer_token=None):
        self.cookies = {"access_token": cookie_token} if cookie_token else {}
        self.headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}


class TestCreateToken:
    def test_returns_token_jti_and_expiry(self):
        user, _ = make_user()
        token, jti, expires_in = auth._create_token(user)
        assert isinstance(token, str)
        assert isinstance(jti, str)
        assert expires_in == auth.JWT_EXPIRE_MINUTES * 60

    def test_token_payload_contains_expected_claims(self):
        user, _ = make_user(username="admin1", role="ADMIN", user_id="u-99")
        token, jti, _ = auth._create_token(user)
        decoded = jwt.decode(token, auth.JWT_SECRET_KEY, algorithms=[auth.JWT_ALGORITHM])
        assert decoded["sub"] == "admin1"
        assert decoded["role"] == "ADMIN"
        assert decoded["user_id"] == "u-99"
        assert decoded["jti"] == jti

    def test_each_token_gets_unique_jti(self):
        user, _ = make_user()
        _, jti1, _ = auth._create_token(user)
        _, jti2, _ = auth._create_token(user)
        assert jti1 != jti2


class TestDecodeToken:
    def test_valid_token_decodes_successfully(self):
        token, jti = make_token()
        decoded = auth._decode_token(token)
        assert decoded["jti"] == jti

    def test_invalid_token_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            auth._decode_token("not.a.valid.token")
        assert exc_info.value.status_code == 401

    def test_expired_token_raises_401(self):
        token, _ = make_token(expire_minutes=-10)
        with pytest.raises(HTTPException) as exc_info:
            auth._decode_token(token)
        assert exc_info.value.status_code == 401

    def test_token_signed_with_wrong_secret_raises_401(self):
        payload = {
            "sub": "x", "role": "ADMIN", "user_id": "1", "jti": "j",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
        }
        bad_token = jwt.encode(payload, "wrong-secret", algorithm=auth.JWT_ALGORITHM)
        with pytest.raises(HTTPException) as exc_info:
            auth._decode_token(bad_token)
        assert exc_info.value.status_code == 401


class TestExtractToken:
    def test_prefers_cookie_over_header(self):
        req = FakeRequest(cookie_token="cookie-tok", bearer_token="header-tok")
        assert auth._extract_token(req) == "cookie-tok"

    def test_falls_back_to_bearer_header_when_no_cookie(self):
        req = FakeRequest(bearer_token="header-tok")
        assert auth._extract_token(req) == "header-tok"

    def test_no_cookie_and_no_header_raises_401(self):
        req = FakeRequest()
        with pytest.raises(HTTPException) as exc_info:
            auth._extract_token(req)
        assert exc_info.value.status_code == 401

    def test_malformed_authorization_header_raises_401(self):
        req = FakeRequest()
        req.headers = {"Authorization": "NotBearer sometoken"}
        with pytest.raises(HTTPException) as exc_info:
            auth._extract_token(req)
        assert exc_info.value.status_code == 401


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_valid_non_blacklisted_token_returns_payload(self):
        token, jti = make_token()
        req = FakeRequest(cookie_token=token)
        fake_redis = AsyncMock()
        fake_redis.exists = AsyncMock(return_value=0)
        with patch("routers.auth.get_redis", AsyncMock(return_value=fake_redis)):
            user = await auth.get_current_user(req)
        assert user["jti"] == jti
        fake_redis.exists.assert_called_once_with(f"blacklist:{jti}")

    @pytest.mark.asyncio
    async def test_blacklisted_token_raises_401(self):
        token, jti = make_token()
        req = FakeRequest(cookie_token=token)
        fake_redis = AsyncMock()
        fake_redis.exists = AsyncMock(return_value=1)
        with patch("routers.auth.get_redis", AsyncMock(return_value=fake_redis)):
            with pytest.raises(HTTPException) as exc_info:
                await auth.get_current_user(req)
        assert exc_info.value.status_code == 401
        assert "revoked" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_no_token_raises_401_before_touching_redis(self):
        req = FakeRequest()
        with patch("routers.auth.get_redis", AsyncMock()) as mock_get_redis:
            with pytest.raises(HTTPException):
                await auth.get_current_user(req)
        mock_get_redis.assert_not_called()


class TestRequireRoles:
    @pytest.mark.asyncio
    async def test_matching_role_passes_through(self):
        checker = auth.require_roles("ADMIN", "MINISTRY_USER")
        user = {"role": "ADMIN", "sub": "x"}
        result = await checker(user)
        assert result == user

    @pytest.mark.asyncio
    async def test_non_matching_role_raises_403(self):
        checker = auth.require_roles("ADMIN")
        user = {"role": "VIEWER", "sub": "x"}
        with pytest.raises(HTTPException) as exc_info:
            await checker(user)
        assert exc_info.value.status_code == 403


class TestLoginEndpoint:
    @pytest.mark.asyncio
    async def test_valid_credentials_no_totp_role_returns_token_and_sets_cookie(self):
        user, pw = make_user(role="PROCUREMENT_ANALYST", password="secret123")
        mock_response = MagicMock()
        with patch("db.postgres_queries.get_user_by_username", return_value=user):
            result = await auth.login(
                auth.LoginRequest(username="analyst1", password=pw),
                mock_response,
            )
        assert result["access_token"]
        assert result["role"] == "PROCUREMENT_ANALYST"
        mock_response.set_cookie.assert_called_once()
        call_kwargs = mock_response.set_cookie.call_args.kwargs
        assert call_kwargs["httponly"] is True
        assert call_kwargs["samesite"] == "lax"
        assert call_kwargs["path"] == "/"

    @pytest.mark.asyncio
    async def test_wrong_password_raises_401(self):
        user, _ = make_user(password="secret123")
        mock_response = MagicMock()
        with patch("db.postgres_queries.get_user_by_username", return_value=user):
            with pytest.raises(HTTPException) as exc_info:
                await auth.login(
                    auth.LoginRequest(username="analyst1", password="wrongpass"),
                    mock_response,
                )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_username_raises_401(self):
        mock_response = MagicMock()
        with patch("db.postgres_queries.get_user_by_username", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await auth.login(
                    auth.LoginRequest(username="ghost", password="whatever"),
                    mock_response,
                )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_privileged_role_not_enrolled_returns_setup_required(self):
        user, pw = make_user(role="ADMIN", password="adminpass", totp_enabled=False)
        mock_response = MagicMock()
        with patch("db.postgres_queries.get_user_by_username", return_value=user):
            result = await auth.login(
                auth.LoginRequest(username="analyst1", password=pw),
                mock_response,
            )
        assert result == {"totp_setup_required": True, "role": "ADMIN"}
        mock_response.set_cookie.assert_not_called()

    @pytest.mark.asyncio
    async def test_privileged_role_enrolled_no_code_returns_code_required(self):
        user, pw = make_user(role="MINISTRY_USER", password="minpass", totp_enabled=True,
                              totp_secret_encrypted="encrypted-blob")
        mock_response = MagicMock()
        with patch("db.postgres_queries.get_user_by_username", return_value=user):
            result = await auth.login(
                auth.LoginRequest(username="analyst1", password=pw),
                mock_response,
            )
        assert result == {"totp_code_required": True}

    @pytest.mark.asyncio
    async def test_privileged_role_valid_totp_code_returns_token(self):
        import pyotp
        secret = pyotp.random_base32()
        encrypted = auth._get_fernet().encrypt(secret.encode()).decode()
        user, pw = make_user(role="ADMIN", password="adminpass", totp_enabled=True,
                              totp_secret_encrypted=encrypted)
        valid_code = pyotp.TOTP(secret).now()
        mock_response = MagicMock()
        with patch("db.postgres_queries.get_user_by_username", return_value=user):
            result = await auth.login(
                auth.LoginRequest(username="analyst1", password=pw, totp_code=valid_code),
                mock_response,
            )
        assert result["access_token"]
        mock_response.set_cookie.assert_called_once()

    @pytest.mark.asyncio
    async def test_privileged_role_invalid_totp_code_raises_401(self):
        import pyotp
        secret = pyotp.random_base32()
        encrypted = auth._get_fernet().encrypt(secret.encode()).decode()
        user, pw = make_user(role="ADMIN", password="adminpass", totp_enabled=True,
                              totp_secret_encrypted=encrypted)
        mock_response = MagicMock()
        with patch("db.postgres_queries.get_user_by_username", return_value=user):
            with pytest.raises(HTTPException) as exc_info:
                await auth.login(
                    auth.LoginRequest(username="analyst1", password=pw, totp_code="000000"),
                    mock_response,
                )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_non_privileged_role_ignores_totp_code_entirely(self):
        user, pw = make_user(role="PROCUREMENT_ANALYST", password="pw12345")
        mock_response = MagicMock()
        with patch("db.postgres_queries.get_user_by_username", return_value=user):
            result = await auth.login(
                auth.LoginRequest(username="analyst1", password=pw, totp_code="999999"),
                mock_response,
            )
        assert result["access_token"]


class TestLogoutEndpoint:
    @pytest.mark.asyncio
    async def test_logout_blacklists_jti_with_remaining_ttl(self):
        token, jti = make_token(expire_minutes=30)
        req = FakeRequest(cookie_token=token)
        mock_response = MagicMock()
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("routers.auth.get_redis", AsyncMock(return_value=fake_redis)):
            result = await auth.logout(req, mock_response)
        assert result == {"message": "Logged out"}
        fake_redis.setex.assert_called_once()
        args = fake_redis.setex.call_args[0]
        assert args[0] == f"blacklist:{jti}"
        assert 1700 <= args[1] <= 1800
        assert args[2] == "revoked"

    @pytest.mark.asyncio
    async def test_logout_clears_cookie(self):
        token, _ = make_token()
        req = FakeRequest(cookie_token=token)
        mock_response = MagicMock()
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("routers.auth.get_redis", AsyncMock(return_value=fake_redis)):
            await auth.logout(req, mock_response)
        mock_response.delete_cookie.assert_called_once_with("access_token", path="/")

    @pytest.mark.asyncio
    async def test_logout_already_expired_token_raises_401(self):
        token, _ = make_token(expire_minutes=-5)
        req = FakeRequest(cookie_token=token)
        mock_response = MagicMock()
        fake_redis = AsyncMock()
        fake_redis.setex = AsyncMock()
        with patch("routers.auth.get_redis", AsyncMock(return_value=fake_redis)):
            with pytest.raises(HTTPException):
                await auth.logout(req, mock_response)
        fake_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_logout_no_token_raises_401(self):
        req = FakeRequest()
        mock_response = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            await auth.logout(req, mock_response)
        assert exc_info.value.status_code == 401


class TestTotpSetupEndpoint:
    @pytest.mark.asyncio
    async def test_valid_admin_credentials_returns_provisioning_uri(self):
        user, pw = make_user(role="ADMIN", password="adminpass")
        with patch("db.postgres_queries.get_user_by_username", return_value=user), \
             patch("db.postgres_queries.set_user_totp") as mock_set_totp:
            result = await auth.totp_setup(
                auth.TotpSetupRequest(username="admin1", password=pw)
            )
        assert "provisioning_uri" in result
        assert result["provisioning_uri"].startswith("otpauth://")
        mock_set_totp.assert_called_once()
        assert mock_set_totp.call_args[0][2] is True

    @pytest.mark.asyncio
    async def test_non_privileged_role_raises_400(self):
        user, pw = make_user(role="VIEWER", password="pw12345")
        with patch("db.postgres_queries.get_user_by_username", return_value=user):
            with pytest.raises(HTTPException) as exc_info:
                await auth.totp_setup(
                    auth.TotpSetupRequest(username="viewer1", password=pw)
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_wrong_password_raises_401(self):
        user, _ = make_user(role="ADMIN", password="adminpass")
        with patch("db.postgres_queries.get_user_by_username", return_value=user):
            with pytest.raises(HTTPException) as exc_info:
                await auth.totp_setup(
                    auth.TotpSetupRequest(username="admin1", password="wrongpass")
                )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_qrcode_missing_falls_back_to_uri_only(self):
        user, pw = make_user(role="ADMIN", password="adminpass")
        with patch("db.postgres_queries.get_user_by_username", return_value=user), \
             patch("db.postgres_queries.set_user_totp"), \
             patch.dict("sys.modules", {"qrcode": None}):
            result = await auth.totp_setup(
                auth.TotpSetupRequest(username="admin1", password=pw)
            )
        assert result["qr_data_url"] is None
        assert "provisioning_uri" in result

    @pytest.mark.asyncio
    async def test_qrcode_present_returns_data_url(self):
        pytest.importorskip("qrcode")
        user, pw = make_user(role="ADMIN", password="adminpass")
        with patch("db.postgres_queries.get_user_by_username", return_value=user), \
             patch("db.postgres_queries.set_user_totp"):
            result = await auth.totp_setup(
                auth.TotpSetupRequest(username="admin1", password=pw)
            )
        assert result["qr_data_url"] is not None
        assert result["qr_data_url"].startswith("data:image/png;base64,")


class TestMeEndpoint:
    @pytest.mark.asyncio
    async def test_returns_identity_from_authenticated_payload(self):
        payload = {"sub": "analyst1", "role": "PROCUREMENT_ANALYST", "user_id": "user-1"}
        result = await auth.me(payload)
        assert result == {
            "username": "analyst1",
            "role": "PROCUREMENT_ANALYST",
            "user_id": "user-1",
        }


class TestFernetEncryption:
    def test_missing_totp_encryption_key_raises_runtime_error(self):
        original = os.environ.pop("TOTP_ENCRYPTION_KEY", None)
        try:
            with pytest.raises(RuntimeError):
                auth._get_fernet()
        finally:
            if original is not None:
                os.environ["TOTP_ENCRYPTION_KEY"] = original

    def test_valid_key_encrypts_and_decrypts_round_trip(self):
        f = auth._get_fernet()
        secret = "JBSWY3DPEHPK3PXP"
        encrypted = f.encrypt(secret.encode()).decode()
        decrypted = f.decrypt(encrypted.encode()).decode()
        assert decrypted == secret