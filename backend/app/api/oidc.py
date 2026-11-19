"""OIDC / SSO authentication endpoints (Phase 8.1).

Endpoints:
  POST /auth/oidc/configure      Admin: create/update tenant OIDC config
  GET  /auth/oidc/configure      Admin: read current OIDC config
  DELETE /auth/oidc/configure    Admin: remove OIDC config (disables SSO)
  GET  /auth/oidc/login          Redirect browser to IdP authorization URL
  GET  /auth/oidc/callback       IdP redirects here; exchange code → tokens
"""
from __future__ import annotations

import logging
import secrets
import uuid
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, get_write_db
from app.models.enums import UserRole
from app.models.oidc_provider import OIDCProvider
from app.models.user import User
from app.services import oidc_service
from app.services.audit_service import log_audit_event
from app.utils.auth import require_role
from app.utils.secret_encryption import decrypt_secret, encrypt_secret
from app.utils.tenant_context import get_current_tenant_id

router = APIRouter(prefix="/auth/oidc", tags=["OIDC / SSO"])

AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN))]
logger = logging.getLogger(__name__)
_state_redis: aioredis.Redis | None = None
_STATE_KEY_PREFIX = "oidc:state:"


# ── Schemas ───────────────────────────────────────────────────────────────────

class OIDCConfigureRequest(BaseModel):
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: list[str] = ["openid", "email", "profile"]
    role_claim: str = "lmd_role"
    default_role: str = "DISPATCHER"

    @field_validator("issuer")
    @classmethod
    def _issuer_https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("issuer must be an HTTPS URL")
        return v.rstrip("/")

    @field_validator("redirect_uri")
    @classmethod
    def _redirect_https(cls, v: str) -> str:
        if not v.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("redirect_uri must be HTTPS (or localhost for development)")
        return v

    @field_validator("default_role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        try:
            UserRole(v.upper())
        except ValueError as exc:
            raise ValueError(f"default_role must be one of {[r.value for r in UserRole]}") from exc
        return v.upper()


class OIDCConfigureResponse(BaseModel):
    id: str
    issuer: str
    client_id: str
    redirect_uri: str
    scopes: list[str]
    role_claim: str
    default_role: str
    is_enabled: bool


class OIDCLoginResponse(BaseModel):
    authorization_url: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _encrypt_secret(plain: str) -> str:
    return encrypt_secret(plain, seed=_oidc_secret_seed())


def _decrypt_secret(enc: str) -> str:
    return decrypt_secret(enc, seed=_oidc_secret_seed())


def _oidc_secret_seed() -> str:
    seed = settings.OIDC_SECRET_ENCRYPTION_KEY or settings.WEBHOOK_SECRET_ENCRYPTION_KEY or settings.JWT_SECRET
    if not seed:
        raise RuntimeError("OIDC secret encryption is unavailable because no secret seed is configured")
    return seed


async def _get_state_redis() -> aioredis.Redis:
    global _state_redis
    if _state_redis is None:
        _state_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _state_redis


def _state_key(state: str) -> str:
    return f"{_STATE_KEY_PREFIX}{state}"


async def _store_oidc_state(*, state: str, tenant_id: str) -> None:
    try:
        redis_client = await _get_state_redis()
        await redis_client.set(_state_key(state), tenant_id, ex=settings.OIDC_STATE_TTL_SECONDS)
    except Exception as exc:
        logger.exception("OIDC login state could not be persisted")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC login is temporarily unavailable",
        ) from exc


async def _getdel(redis_client: aioredis.Redis, key: str) -> str | None:
    getdel = getattr(redis_client, "getdel", None)
    if getdel is not None:
        return await getdel(key)

    value = await redis_client.get(key)
    if value is not None:
        await redis_client.delete(key)
    return value


async def _consume_oidc_state(state: str) -> uuid.UUID:
    try:
        redis_client = await _get_state_redis()
        tenant_id_str = await _getdel(redis_client, _state_key(state))
    except Exception as exc:
        logger.exception("OIDC callback state validation failed because Redis is unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC login is temporarily unavailable",
        ) from exc

    if tenant_id_str is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OIDC state parameter",
        )

    try:
        return uuid.UUID(tenant_id_str)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OIDC state parameter",
        ) from exc


async def _get_provider(db: AsyncSession, current_user: User) -> OIDCProvider:
    tenant_id = get_current_tenant_id(current_user)
    result = await db.execute(
        select(OIDCProvider).where(OIDCProvider.tenant_id == tenant_id)
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No OIDC configuration found for this tenant",
        )
    return provider


def _provider_to_response(provider: OIDCProvider) -> OIDCConfigureResponse:
    return OIDCConfigureResponse(
        id=str(provider.id),
        issuer=provider.issuer,
        client_id=provider.client_id,
        redirect_uri=provider.redirect_uri,
        scopes=provider.scopes.split(),
        role_claim=provider.role_claim,
        default_role=provider.default_role,
        is_enabled=provider.is_enabled,
    )


def _build_oidc_config(provider: OIDCProvider) -> oidc_service.OIDCConfiguration:
    return oidc_service.OIDCConfiguration(
        issuer=provider.issuer,
        client_id=provider.client_id,
        client_secret=_decrypt_secret(provider.client_secret_enc),
        redirect_uri=provider.redirect_uri,
        scopes=provider.scopes.split(),
        role_claim=provider.role_claim,
        default_role=provider.default_role,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/configure", response_model=OIDCConfigureResponse, status_code=status.HTTP_201_CREATED)
async def configure_oidc(
    payload: OIDCConfigureRequest,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
) -> OIDCConfigureResponse:
    """Create or replace the OIDC provider configuration for the current tenant."""
    tenant_id = get_current_tenant_id(current_user)
    result = await db.execute(
        select(OIDCProvider).where(OIDCProvider.tenant_id == tenant_id)
    )
    provider = result.scalar_one_or_none()

    encrypted = _encrypt_secret(payload.client_secret)

    if provider is None:
        provider = OIDCProvider(
            tenant_id=tenant_id,
            issuer=payload.issuer,
            client_id=payload.client_id,
            client_secret_enc=encrypted,
            redirect_uri=payload.redirect_uri,
            scopes=" ".join(payload.scopes),
            role_claim=payload.role_claim,
            default_role=payload.default_role,
        )
        db.add(provider)
    else:
        provider.issuer = payload.issuer
        provider.client_id = payload.client_id
        provider.client_secret_enc = encrypted
        provider.redirect_uri = payload.redirect_uri
        provider.scopes = " ".join(payload.scopes)
        provider.role_claim = payload.role_claim
        provider.default_role = payload.default_role
        provider.is_enabled = True

    await db.flush()
    await log_audit_event(
        db,
        action="oidc.configure",
        actor_user_id=current_user.id,
        tenant_id=tenant_id,
        resource_type="oidc_provider",
        resource_id=str(provider.id),
        details={"issuer": payload.issuer, "client_id": payload.client_id},
    )
    return _provider_to_response(provider)


@router.get("/configure", response_model=OIDCConfigureResponse)
async def get_oidc_configuration(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: AdminDep,
) -> OIDCConfigureResponse:
    """Return the current OIDC configuration (client_secret is never returned)."""
    provider = await _get_provider(db, current_user)
    return _provider_to_response(provider)


@router.delete("/configure", status_code=status.HTTP_204_NO_CONTENT)
async def delete_oidc_configuration(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Disable and remove the OIDC configuration for the current tenant."""
    provider = await _get_provider(db, current_user)
    await db.delete(provider)
    await log_audit_event(
        db,
        action="oidc.configure_deleted",
        actor_user_id=current_user.id,
        tenant_id=get_current_tenant_id(current_user),
        resource_type="oidc_provider",
        resource_id=str(provider.id),
    )


@router.get("/login", response_model=OIDCLoginResponse)
async def oidc_login(
    tenant_slug: Annotated[str, Query(description="Tenant slug to identify the OIDC provider")],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OIDCLoginResponse:
    """Return the IdP authorization URL. The frontend should redirect the browser there."""
    from sqlalchemy import select as _select  # noqa: PLC0415

    from app.models.tenant import Tenant  # noqa: PLC0415

    tenant_result = await db.execute(
        _select(OIDCProvider)
        .join(Tenant, Tenant.id == OIDCProvider.tenant_id)
        .where(Tenant.slug == tenant_slug, OIDCProvider.is_enabled.is_(True))
    )
    provider = tenant_result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No OIDC provider configured for this tenant",
    )

    config = _build_oidc_config(provider)
    state = secrets.token_urlsafe(32)
    auth_url = await oidc_service.build_authorization_url(config, state)
    await _store_oidc_state(state=state, tenant_id=str(provider.tenant_id))
    return OIDCLoginResponse(authorization_url=auth_url)


@router.get("/callback")
async def oidc_callback(
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> dict[str, str]:
    """IdP redirects here after user authenticates. Exchange code for local tokens."""
    tenant_id = await _consume_oidc_state(state)
    result = await db.execute(
        select(OIDCProvider).where(
            OIDCProvider.tenant_id == tenant_id,
            OIDCProvider.is_enabled.is_(True),
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC provider not found",
        )

    config = _build_oidc_config(provider)
    claims = await oidc_service.exchange_code_for_id_token(config, code)
    user = await oidc_service.get_or_provision_user(db, tenant_id=tenant_id, claims=claims, config=config)

    await log_audit_event(
        db,
        action="auth.oidc_login",
        actor_user_id=user.id,
        tenant_id=tenant_id,
        resource_type="user",
        resource_id=str(user.id),
        details={"issuer": provider.issuer},
    )

    return oidc_service.build_token_response_for_user(user)
