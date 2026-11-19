"""OIDC / SSO authentication service (Phase 8.1).

Provides a lightweight, pluggable OIDC provider interface.  Tenants configure
their identity provider via the ``/api/v1/auth/oidc/configure`` endpoint.
The service:
  1. Discovers OIDC endpoints by fetching ``<issuer>/.well-known/openid-configuration``.
  2. Builds the authorization URL to redirect the browser to the IdP.
  3. Exchanges the authorization code for an ID token at the token endpoint.
  4. Verifies the ID token's signature using the IdP's JWKS.
  5. Looks up or provisions the local User record based on the ``email`` claim.

Supported token signing algorithms: RS256, RS384, RS512, ES256, ES384, ES512.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, status
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User
from app.utils.auth import create_access_token, create_refresh_token

logger = logging.getLogger(__name__)

# Allowed ID token signing algorithms (no symmetric algs — IdP must use asymmetric keys)
_ALLOWED_ALGORITHMS = [
    "RS256", "RS384", "RS512",
    "ES256", "ES384", "ES512",
    "PS256", "PS384", "PS512",
]

# In-memory discovery cache keyed by issuer URL — avoids a round-trip on every login
# TTL is intentionally short (5 min) so key rotation is picked up promptly.
_DISCOVERY_CACHE: dict[str, tuple[dict[str, Any], float]] = {}
_DISCOVERY_CACHE_TTL = 300  # seconds

# Per-request HTTP client (shared, no connection pooling issues in async context)
_HTTP_TIMEOUT = httpx.Timeout(10.0)


class OIDCConfiguration:
    """Immutable view of a tenant's OIDC provider settings."""

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: list[str] | None = None,
        role_claim: str = "lmd_role",
        default_role: str = UserRole.DISPATCHER.value,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes or ["openid", "email", "profile"]
        self.role_claim = role_claim
        self.default_role = default_role


async def _fetch_oidc_discovery(issuer: str) -> dict[str, Any]:
    """Fetch and cache the OIDC discovery document from ``<issuer>/.well-known/openid-configuration``."""
    cached = _DISCOVERY_CACHE.get(issuer)
    if cached and time.monotonic() < cached[1]:
        return cached[0]

    url = f"{issuer}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OIDC discovery failed for issuer {issuer!r} (HTTP {resp.status_code})",
        )
    doc: dict[str, Any] = resp.json()
    _DISCOVERY_CACHE[issuer] = (doc, time.monotonic() + _DISCOVERY_CACHE_TTL)
    return doc


async def _fetch_jwks(jwks_uri: str) -> list[dict[str, Any]]:
    """Fetch the JWKS (JSON Web Key Set) from the IdP."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(jwks_uri)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch JWKS from {jwks_uri!r} (HTTP {resp.status_code})",
        )
    return resp.json().get("keys", [])


async def build_authorization_url(config: OIDCConfiguration, state: str) -> str:
    """Return the IdP's authorization endpoint URL for the browser redirect."""
    discovery = await _fetch_oidc_discovery(config.issuer)
    auth_endpoint = discovery["authorization_endpoint"]
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": " ".join(config.scopes),
        "state": state,
        "nonce": str(uuid.uuid4()),
    }
    return f"{auth_endpoint}?{urlencode(params)}"


async def exchange_code_for_id_token(
    config: OIDCConfiguration,
    code: str,
) -> dict[str, Any]:
    """Exchange an authorization code for an ID token; return the verified claims."""
    discovery = await _fetch_oidc_discovery(config.issuer)
    token_endpoint = discovery["token_endpoint"]
    jwks_uri = discovery["jwks_uri"]

    # Exchange code at the IdP token endpoint
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.redirect_uri,
                "client_id": config.client_id,
                "client_secret": config.client_secret,
            },
        )
    if resp.status_code != 200:
        logger.warning("OIDC token exchange failed: %s %s", resp.status_code, resp.text)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OIDC token exchange failed",
        )

    token_response = resp.json()
    id_token_raw = token_response.get("id_token")
    if not id_token_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="IdP did not return an id_token",
        )

    # Verify the ID token signature using the IdP's JWKS
    keys = await _fetch_jwks(jwks_uri)
    claims = _verify_id_token(id_token_raw, keys, config.client_id, config.issuer)
    return claims


def _verify_id_token(
    id_token: str,
    jwks_keys: list[dict[str, Any]],
    audience: str,
    issuer: str,
) -> dict[str, Any]:
    """Verify ID token signature, iss, aud, and exp; return the decoded claims."""
    header = jwt.get_unverified_header(id_token)
    kid = header.get("kid")
    alg = header.get("alg", "RS256")

    if alg not in _ALLOWED_ALGORITHMS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"ID token uses disallowed algorithm {alg!r}",
        )

    # Select the matching JWK by key ID
    matching_keys = [k for k in jwks_keys if kid is None or k.get("kid") == kid]
    if not matching_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No matching JWKS key found for ID token kid",
        )

    last_exc: Exception | None = None
    for raw_key in matching_keys:
        try:
            pub_key = jwt.PyJWK.from_dict(raw_key, algorithm=alg).key
            claims: dict[str, Any] = jwt.decode(
                id_token,
                pub_key,
                algorithms=[alg],
                audience=audience,
                issuer=issuer,
            )
            return claims
        except InvalidTokenError as exc:
            last_exc = exc

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"ID token verification failed: {last_exc}",
    )


async def get_or_provision_user(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    claims: dict[str, Any],
    config: OIDCConfiguration,
) -> User:
    """Look up the local User by email claim; create (just-in-time provision) if absent.

    The user's role is determined by the IdP claim named ``config.role_claim``
    (default ``lmd_role``).  Falls back to ``config.default_role`` if absent.
    """
    email: str | None = claims.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ID token does not include an email claim",
        )

    result = await db.execute(
        select(User).where(User.email == email, User.tenant_id == tenant_id)
    )
    user = result.scalar_one_or_none()

    # Map IdP role claim to local UserRole — default to DISPATCHER if unrecognised
    raw_role = claims.get(config.role_claim, config.default_role)
    try:
        role = UserRole(raw_role.upper())
    except ValueError:
        role = UserRole(config.default_role)

    if user is None:
        # Just-in-time provisioning: create a local account for the IdP user.
        # A dummy (unusable) password hash is stored because SSO users never
        # authenticate with a password.
        import secrets as _secrets  # noqa: PLC0415

        from app.utils.auth import hash_password  # noqa: PLC0415

        user = User(
            email=email,
            hashed_password=hash_password(_secrets.token_hex(32)),
            role=role,
            tenant_id=tenant_id,
            email_verified=True,  # IdP has already verified the email
        )
        db.add(user)
        await db.flush()
        logger.info("JIT-provisioned OIDC user %s for tenant %s", email, tenant_id)
    else:
        # Update role from IdP on every login (keeps roles in sync with the IdP)
        user.role = role

    await db.flush()
    return user


def build_token_response_for_user(user: User) -> dict[str, str]:
    """Return an access + refresh token pair for the local user."""
    return {
        "access_token": create_access_token(str(user.id), user.role.value, str(user.tenant_id)),
        "refresh_token": create_refresh_token(str(user.id), user.role.value, str(user.tenant_id)),
        "token_type": "bearer",
    }
