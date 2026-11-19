from __future__ import annotations

import uuid

import jwt
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select

from app.config import settings
from app.database import get_async_session_factory
from app.models.revoked_token import RevokedToken
from app.models.user import User
from app.utils.auth import decode_token
from tests.integration.live_test_utils import run_async

pytestmark = pytest.mark.live_infra


def _signup_payload(*, email: str, password: str, company_name: str) -> dict:
    return {
        "email": email,
        "password": password,
        "company_name": company_name,
        "full_name": "Integration Owner",
    }


async def _get_user_by_email(email: str) -> User | None:
    async with get_async_session_factory()() as db:
        result = await db.execute(
            select(User)
            .where(User.email == email)
            .execution_options(skip_tenant_scope=True)
        )
        return result.scalar_one_or_none()


async def _list_revoked_token_jtis() -> set[str]:
    async with get_async_session_factory()() as db:
        result = await db.execute(select(RevokedToken.jti))
        return set(result.scalars().all())


async def _redis_key_exists(key: str) -> bool:
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        return await client.exists(key) == 1
    finally:
        await client.aclose()


async def _get_single_password_reset_token() -> str:
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        keys = sorted(await client.keys("pw_reset:*"))
    finally:
        await client.aclose()

    assert len(keys) == 1
    return keys[0].split(":", maxsplit=1)[1]


def test_signup_refresh_logout_round_trip_persists_revocations_in_postgres_and_redis(live_test_client):
    email = f"owner-{uuid.uuid4().hex[:10]}@example.com"
    password = "Password1!"
    company_name = f"Tenant-{uuid.uuid4().hex[:8]}"

    signup_response = live_test_client.post(
        "/api/v1/auth/signup",
        json=_signup_payload(email=email, password=password, company_name=company_name),
    )

    assert signup_response.status_code == 201
    token_response = signup_response.json()

    me_response = live_test_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_response['access_token']}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email

    created_user = run_async(_get_user_by_email(email))
    assert created_user is not None
    assert created_user.email_verification_token is not None
    assert created_user.email_verified is False

    first_refresh_payload = decode_token(token_response["refresh_token"], expected_type="refresh")

    refresh_response = live_test_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": token_response["refresh_token"]},
    )

    assert refresh_response.status_code == 200
    rotated_tokens = refresh_response.json()
    second_access_payload = decode_token(rotated_tokens["access_token"], expected_type="access")
    second_refresh_payload = decode_token(rotated_tokens["refresh_token"], expected_type="refresh")

    assert run_async(_redis_key_exists(f"revoked_token:{first_refresh_payload['jti']}")) is True
    assert first_refresh_payload["jti"] in run_async(_list_revoked_token_jtis())

    reused_refresh_response = live_test_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": token_response["refresh_token"]},
    )
    assert reused_refresh_response.status_code == 401
    assert reused_refresh_response.json()["detail"] == "Refresh token revoked"

    logout_response = live_test_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {rotated_tokens['access_token']}"},
        json={"refresh_token": rotated_tokens["refresh_token"]},
    )
    assert logout_response.status_code == 200

    revoked_jtis = run_async(_list_revoked_token_jtis())
    assert {
        first_refresh_payload["jti"],
        second_access_payload["jti"],
        second_refresh_payload["jti"],
    }.issubset(revoked_jtis)
    assert run_async(_redis_key_exists(f"revoked_token:{second_access_payload['jti']}")) is True
    assert run_async(_redis_key_exists(f"revoked_token:{second_refresh_payload['jti']}")) is True

    revoked_access_response = live_test_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {rotated_tokens['access_token']}"},
    )
    assert revoked_access_response.status_code == 401
    assert revoked_access_response.json()["detail"] == "Access token revoked"

    revoked_refresh_response = live_test_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": rotated_tokens["refresh_token"]},
    )
    assert revoked_refresh_response.status_code == 401
    assert revoked_refresh_response.json()["detail"] == "Refresh token revoked"


def test_refresh_accepts_previous_auth_key_during_rotation_and_issues_new_kid(
    monkeypatch,
    live_test_client,
):
    email = f"rotate-{uuid.uuid4().hex[:10]}@example.com"
    old_password = "Password1!"
    company_name = f"Tenant-{uuid.uuid4().hex[:8]}"

    monkeypatch.setattr(settings, "AUTH_JWT_ACTIVE_KID", "auth-v1")
    monkeypatch.setattr(settings, "AUTH_JWT_PREVIOUS_KEYS", {})
    signup_response = live_test_client.post(
        "/api/v1/auth/signup",
        json=_signup_payload(email=email, password=old_password, company_name=company_name),
    )
    assert signup_response.status_code == 201
    original_tokens = signup_response.json()
    original_refresh_payload = decode_token(
        original_tokens["refresh_token"],
        expected_type="refresh",
    )
    assert jwt.get_unverified_header(original_tokens["refresh_token"])["kid"] == "auth-v1"

    old_secret = settings.JWT_SECRET
    monkeypatch.setattr(settings, "JWT_SECRET", "integration-rotated-auth-secret-v2!!")
    monkeypatch.setattr(settings, "AUTH_JWT_ACTIVE_KID", "auth-v2")
    monkeypatch.setattr(settings, "AUTH_JWT_PREVIOUS_KEYS", {"auth-v1": old_secret})

    refresh_response = live_test_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original_tokens["refresh_token"]},
    )

    assert refresh_response.status_code == 200
    rotated_tokens = refresh_response.json()
    assert jwt.get_unverified_header(rotated_tokens["access_token"])["kid"] == "auth-v2"
    assert jwt.get_unverified_header(rotated_tokens["refresh_token"])["kid"] == "auth-v2"
    assert decode_token(rotated_tokens["access_token"], expected_type="access")["sub"]
    assert decode_token(rotated_tokens["refresh_token"], expected_type="refresh")["sub"]
    assert run_async(_redis_key_exists(f"revoked_token:{original_refresh_payload['jti']}")) is True


def test_password_reset_round_trip_uses_real_redis_token_storage(live_test_client):
    email = f"reset-{uuid.uuid4().hex[:10]}@example.com"
    old_password = "Password1!"
    new_password = "Password2!"
    company_name = f"Tenant-{uuid.uuid4().hex[:8]}"

    signup_response = live_test_client.post(
        "/api/v1/auth/signup",
        json=_signup_payload(email=email, password=old_password, company_name=company_name),
    )
    assert signup_response.status_code == 201

    forgot_password_response = live_test_client.post(
        "/api/v1/auth/forgot-password",
        json={"email": email},
    )
    assert forgot_password_response.status_code == 202

    reset_token = run_async(_get_single_password_reset_token())
    assert reset_token
    assert run_async(_redis_key_exists(f"pw_reset:{reset_token}")) is True

    reset_password_response = live_test_client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "new_password": new_password},
    )
    assert reset_password_response.status_code == 200
    assert reset_password_response.json()["status"] == "ok"
    assert run_async(_redis_key_exists(f"pw_reset:{reset_token}")) is False

    old_password_login = live_test_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": old_password},
    )
    assert old_password_login.status_code == 401
    assert old_password_login.json()["detail"] == "Invalid credentials"

    new_password_login = live_test_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": new_password},
    )
    assert new_password_login.status_code == 200
    assert "access_token" in new_password_login.json()
