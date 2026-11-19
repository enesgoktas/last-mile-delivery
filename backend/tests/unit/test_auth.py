import uuid
from datetime import datetime, timedelta

import jwt
import pytest

import app.utils.auth as auth_utils
from app.config import settings
from app.utils.auth import (
    create_access_token,
    create_refresh_token,
    create_tracking_token,
    decode_token,
    decode_tracking_token,
    get_auth_jwt_secret,
    get_tracking_jwt_secret,
    hash_password,
    verify_password,
)
from app.utils.compat import UTC

TEST_TENANT_ID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _configure_jwt_settings(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "unit-auth-secret-32-bytes-long!!!!")
    monkeypatch.setattr(settings, "AUTH_JWT_ACTIVE_KID", "auth-v1")
    monkeypatch.setattr(settings, "AUTH_JWT_PREVIOUS_KEYS", {})
    monkeypatch.setattr(settings, "TRACKING_JWT_SECRET", "unit-tracking-secret-32bytes-long")
    monkeypatch.setattr(settings, "TRACKING_JWT_ACTIVE_KID", "tracking-v1")
    monkeypatch.setattr(settings, "TRACKING_JWT_PREVIOUS_KEYS", {})


class TestPasswordHashing:
    def test_hash_and_verify(self):
        password = "secure_password_123"
        hashed = hash_password(password)
        assert hashed != password
        assert verify_password(password, hashed)

    def test_wrong_password_fails(self):
        hashed = hash_password("correct_password")
        assert not verify_password("wrong_password", hashed)

    def test_different_hashes_each_time(self):
        """bcrypt should produce different hashes due to random salt."""
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2
        # Both should still verify
        assert verify_password("same_password", h1)
        assert verify_password("same_password", h2)


class TestJWTTokens:
    def test_access_token_has_correct_claims(self):
        token = create_access_token("user-123", "ADMIN", TEST_TENANT_ID)
        assert jwt.get_unverified_header(token)["kid"] == settings.AUTH_JWT_ACTIVE_KID
        payload = jwt.decode(
            token,
            get_auth_jwt_secret(),
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_ACCESS_JWT_AUDIENCE,
        )
        assert payload["sub"] == "user-123"
        assert payload["role"] == "ADMIN"
        assert payload["tenant_id"] == TEST_TENANT_ID
        assert payload["type"] == "access"
        assert payload["iss"] == settings.AUTH_JWT_ISSUER
        assert payload["aud"] == settings.AUTH_ACCESS_JWT_AUDIENCE
        assert "exp" in payload

    def test_refresh_token_has_correct_claims(self):
        token = create_refresh_token("user-456", "COURIER", TEST_TENANT_ID)
        assert jwt.get_unverified_header(token)["kid"] == settings.AUTH_JWT_ACTIVE_KID
        payload = jwt.decode(
            token,
            get_auth_jwt_secret(),
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_REFRESH_JWT_AUDIENCE,
        )
        assert payload["sub"] == "user-456"
        assert payload["role"] == "COURIER"
        assert payload["tenant_id"] == TEST_TENANT_ID
        assert payload["type"] == "refresh"
        assert payload["iss"] == settings.AUTH_JWT_ISSUER
        assert payload["aud"] == settings.AUTH_REFRESH_JWT_AUDIENCE

    def test_access_token_expiry(self):
        token = create_access_token("user-123", "ADMIN", TEST_TENANT_ID)
        payload = jwt.decode(
            token,
            get_auth_jwt_secret(),
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_ACCESS_JWT_AUDIENCE,
        )
        exp = datetime.utcfromtimestamp(payload["exp"])
        now = datetime.now(UTC).replace(tzinfo=None)
        # Should expire within configured minutes (± 5 seconds tolerance)
        delta = exp - now
        expected_seconds = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        assert abs(delta.total_seconds() - expected_seconds) < 5

    def test_refresh_token_expiry(self):
        token = create_refresh_token("user-123", "ADMIN", TEST_TENANT_ID)
        payload = jwt.decode(
            token,
            get_auth_jwt_secret(),
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_REFRESH_JWT_AUDIENCE,
        )
        exp = datetime.utcfromtimestamp(payload["exp"])
        now = datetime.now(UTC).replace(tzinfo=None)
        delta = exp - now
        expected_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
        assert abs(delta.total_seconds() - expected_seconds) < 5

    def test_decode_token_accepts_previous_auth_key_with_matching_kid(self, monkeypatch):
        old_secret = "old-auth-secret-for-rotation-32b!"
        monkeypatch.setattr(settings, "AUTH_JWT_PREVIOUS_KEYS", {"auth-v0": old_secret})
        payload = {
            "sub": "user-rotate",
            "role": "ADMIN",
            "tenant_id": TEST_TENANT_ID,
            **auth_utils._build_registered_claims(
                token_type="access",
                expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
                issuer=settings.AUTH_JWT_ISSUER,
                audience=settings.AUTH_ACCESS_JWT_AUDIENCE,
            ),
        }
        token = jwt.encode(
            payload,
            old_secret,
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": "auth-v0"},
        )

        decoded = decode_token(token, expected_type="access")

        assert decoded["sub"] == "user-rotate"
        assert decoded["tenant_id"] == TEST_TENANT_ID

    def test_decode_token_accepts_legacy_auth_token_without_kid_from_previous_key(self, monkeypatch):
        old_secret = "legacy-auth-secret-for-rotation-32!"
        monkeypatch.setattr(settings, "JWT_SECRET", "new-auth-secret-for-rotation-32b!")
        monkeypatch.setattr(settings, "AUTH_JWT_ACTIVE_KID", "auth-v2")
        monkeypatch.setattr(settings, "AUTH_JWT_PREVIOUS_KEYS", {"auth-v1": old_secret})
        payload = {
            "sub": "legacy-user",
            "role": "ADMIN",
            "tenant_id": TEST_TENANT_ID,
            **auth_utils._build_registered_claims(
                token_type="access",
                expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
                issuer=settings.AUTH_JWT_ISSUER,
                audience=settings.AUTH_ACCESS_JWT_AUDIENCE,
            ),
        }
        token = jwt.encode(payload, old_secret, algorithm=settings.JWT_ALGORITHM)

        decoded = decode_token(token, expected_type="access")

        assert decoded["sub"] == "legacy-user"
        assert decoded["tenant_id"] == TEST_TENANT_ID

    def test_decode_token_rejects_unknown_kid(self, monkeypatch):
        payload = {
            "sub": "user-unknown-kid",
            "role": "ADMIN",
            "tenant_id": TEST_TENANT_ID,
            **auth_utils._build_registered_claims(
                token_type="access",
                expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
                issuer=settings.AUTH_JWT_ISSUER,
                audience=settings.AUTH_ACCESS_JWT_AUDIENCE,
            ),
        }
        token = jwt.encode(
            payload,
            get_auth_jwt_secret(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": "auth-unknown"},
        )

        with pytest.raises(auth_utils.HTTPException) as exc_info:
            decode_token(token, expected_type="access")

        assert exc_info.value.status_code == 401


class TestTrackingToken:
    def test_tracking_token_create_and_decode(self):
        token = create_tracking_token("order-abc")
        assert jwt.get_unverified_header(token)["kid"] == settings.TRACKING_JWT_ACTIVE_KID
        result = decode_tracking_token(token)
        assert result["order_id"] == "order-abc"
        assert result["type"] == "tracking"
        assert result["iss"] == settings.TRACKING_JWT_ISSUER
        assert result["aud"] == settings.TRACKING_JWT_AUDIENCE

    def test_invalid_tracking_token_raises(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            decode_tracking_token("garbage-token")
        assert exc_info.value.status_code == 401

    def test_access_token_rejected_as_tracking(self):
        """An access token should not be accepted as a tracking token."""
        from fastapi import HTTPException
        access_token = create_access_token("user-123", "ADMIN", TEST_TENANT_ID)
        with pytest.raises(HTTPException):
            decode_tracking_token(access_token)

    def test_tracking_token_uses_dedicated_secret_when_configured(self, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(settings, "TRACKING_JWT_SECRET", "tracking-secret-only-32-bytes-long!!!")
        token = create_tracking_token("order-abc")

        with pytest.raises(HTTPException):
            auth_utils.decode_token(token, expected_type="access")

        payload = jwt.decode(
            token,
            get_tracking_jwt_secret(),
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.TRACKING_JWT_ISSUER,
            audience=settings.TRACKING_JWT_AUDIENCE,
        )
        assert payload["order_id"] == "order-abc"

    def test_tracking_token_accepts_previous_key_with_matching_kid(self, monkeypatch):
        old_secret = "tracking-secret-old-rotation-value!"
        monkeypatch.setattr(settings, "TRACKING_JWT_PREVIOUS_KEYS", {"tracking-v0": old_secret})
        payload = {
            "order_id": "order-rotated",
            **auth_utils._build_registered_claims(
                token_type="tracking",
                expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
                issuer=settings.TRACKING_JWT_ISSUER,
                audience=settings.TRACKING_JWT_AUDIENCE,
            ),
        }
        token = jwt.encode(
            payload,
            old_secret,
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": "tracking-v0"},
        )

        decoded = decode_tracking_token(token)

        assert decoded["order_id"] == "order-rotated"


@pytest.mark.asyncio
async def test_revoke_token_persists_to_durable_store_when_redis_unavailable(monkeypatch):
    token = create_access_token("user-123", "ADMIN", TEST_TENANT_ID)
    payload = jwt.decode(
        token,
        get_auth_jwt_secret(),
        algorithms=[settings.JWT_ALGORITHM],
        issuer=settings.AUTH_JWT_ISSUER,
        audience=settings.AUTH_ACCESS_JWT_AUDIENCE,
    )
    stored: dict[str, object] = {}

    async def _redis_unavailable():
        raise RuntimeError("redis unavailable")

    async def _capture_store(jti: str, expires_at: datetime):
        stored["jti"] = jti
        stored["expires_at"] = expires_at

    monkeypatch.setattr(auth_utils, "_get_redis", _redis_unavailable)
    monkeypatch.setattr(auth_utils, "_store_revoked_token_db", _capture_store)

    await auth_utils.revoke_token(token)
    assert stored["jti"] == payload["jti"]
    assert isinstance(stored["expires_at"], datetime)


@pytest.mark.asyncio
async def test_is_token_revoked_uses_durable_store_when_redis_is_unavailable(monkeypatch):
    async def _redis_unavailable():
        raise RuntimeError("redis unavailable")

    async def _future_expiry(_jti: str):
        return datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)

    monkeypatch.setattr(auth_utils, "_get_redis", _redis_unavailable)
    monkeypatch.setattr(auth_utils, "_get_revoked_token_expiry_db", _future_expiry)

    assert await auth_utils.is_token_revoked("durable-jti") is True


@pytest.mark.asyncio
async def test_is_token_revoked_deletes_expired_durable_entries(monkeypatch):
    deleted: list[str] = []

    class _RedisMiss:
        async def exists(self, _key: str) -> int:
            return 0

    async def _expired(_jti: str):
        return datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)

    async def _capture_delete(jti: str):
        deleted.append(jti)

    async def _get_redis():
        return _RedisMiss()

    monkeypatch.setattr(auth_utils, "_get_redis", _get_redis)
    monkeypatch.setattr(auth_utils, "_get_revoked_token_expiry_db", _expired)
    monkeypatch.setattr(auth_utils, "_delete_revoked_token_db", _capture_delete)

    assert await auth_utils.is_token_revoked("expired-jti") is False
    assert deleted == ["expired-jti"]


@pytest.mark.asyncio
async def test_is_token_revoked_rehydrates_redis_from_durable_store(monkeypatch):
    class _FakeRedis:
        def __init__(self) -> None:
            self.set_calls: list[tuple[str, str, int]] = []

        async def exists(self, _key: str) -> int:
            return 0

        async def set(self, key: str, value: str, ex: int) -> None:
            self.set_calls.append((key, value, ex))

    fake_redis = _FakeRedis()

    async def _future_expiry(_jti: str):
        return datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)

    async def _get_redis():
        return fake_redis

    monkeypatch.setattr(auth_utils, "_get_redis", _get_redis)
    monkeypatch.setattr(auth_utils, "_get_revoked_token_expiry_db", _future_expiry)

    assert await auth_utils.is_token_revoked("rehydrate-jti") is True
    assert fake_redis.set_calls
