import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

import jwt
import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from passlib.context import CryptContext
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_async_session_factory, get_db
from app.models.enums import ADMIN_LIKE_ROLES, UserRole
from app.models.revoked_token import RevokedToken
from app.models.user import User
from app.utils.compat import UTC
from app.utils.tenant_context import (
    get_current_tenant_id,
    set_current_tenant_scope,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
_redis: aioredis.Redis | None = None
logger = logging.getLogger(__name__)

_AUTH_TOKEN_AUDIENCES = {
    "access": lambda: settings.AUTH_ACCESS_JWT_AUDIENCE,
    "refresh": lambda: settings.AUTH_REFRESH_JWT_AUDIENCE,
}


@dataclass(frozen=True)
class JWTSigningKey:
    kid: str
    secret: str


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_key_id(kid: str, *, label: str) -> str:
    normalized = kid.strip()
    if not normalized:
        raise RuntimeError(f"{label} is empty")
    return normalized


def _normalize_secret(secret: str, *, label: str) -> str:
    normalized = secret.strip()
    if not normalized:
        raise RuntimeError(f"{label} is empty")
    return normalized


def _build_verification_keyring(
    *,
    active_key: JWTSigningKey,
    previous_keys: dict[str, str],
    label: str,
) -> dict[str, str]:
    keyring: dict[str, str] = {active_key.kid: active_key.secret}
    for raw_kid, raw_secret in previous_keys.items():
        normalized_kid = _normalize_key_id(raw_kid, label=f"{label} previous key id")
        if normalized_kid == active_key.kid:
            raise RuntimeError(
                f"{label} previous key ring reuses active kid {active_key.kid!r}; "
                "move that secret to the active slot or rename the previous kid"
            )
        keyring[normalized_kid] = _normalize_secret(
            raw_secret,
            label=f"{label} previous key {normalized_kid!r} secret",
        )
    return keyring


def get_auth_jwt_signing_key() -> JWTSigningKey:
    return JWTSigningKey(
        kid=_normalize_key_id(settings.AUTH_JWT_ACTIVE_KID, label="AUTH_JWT_ACTIVE_KID"),
        secret=_normalize_secret(settings.JWT_SECRET, label="JWT_SECRET"),
    )


def get_auth_jwt_secret() -> str:
    return get_auth_jwt_signing_key().secret


def get_tracking_jwt_signing_key() -> JWTSigningKey:
    return JWTSigningKey(
        kid=_normalize_key_id(
            settings.TRACKING_JWT_ACTIVE_KID,
            label="TRACKING_JWT_ACTIVE_KID",
        ),
        secret=_normalize_secret(
            settings.TRACKING_JWT_SECRET or settings.JWT_SECRET,
            label="TRACKING_JWT_SECRET",
        ),
    )


def get_tracking_jwt_secret() -> str:
    return get_tracking_jwt_signing_key().secret


def get_auth_jwt_verification_keys() -> dict[str, str]:
    return _build_verification_keyring(
        active_key=get_auth_jwt_signing_key(),
        previous_keys=settings.AUTH_JWT_PREVIOUS_KEYS,
        label="AUTH_JWT",
    )


def get_tracking_jwt_verification_keys() -> dict[str, str]:
    return _build_verification_keyring(
        active_key=get_tracking_jwt_signing_key(),
        previous_keys=settings.TRACKING_JWT_PREVIOUS_KEYS,
        label="TRACKING_JWT",
    )


def _encode_jwt(payload: dict, *, signing_key: JWTSigningKey) -> str:
    return jwt.encode(
        payload,
        signing_key.secret,
        algorithm=settings.JWT_ALGORITHM,
        headers={"kid": signing_key.kid},
    )


def _build_registered_claims(
    *,
    token_type: str,
    expires_at: datetime,
    issuer: str,
    audience: str,
) -> dict[str, object]:
    now = _utcnow_naive()
    return {
        "exp": expires_at,
        "iat": now,
        "nbf": now,
        "iss": issuer,
        "aud": audience,
        "type": token_type,
        "jti": str(uuid.uuid4()),
    }


def _decode_verified_jwt(
    token: str,
    *,
    verification_keys: dict[str, str],
    issuer: str,
    audience: str,
) -> dict:
    header = jwt.get_unverified_header(token)
    token_kid = header.get("kid")

    candidate_keys: list[tuple[str, str]]
    if token_kid is None:
        candidate_keys = list(verification_keys.items())
    else:
        matching_secret = verification_keys.get(str(token_kid))
        if matching_secret is None:
            raise InvalidTokenError("Unknown token key id")
        candidate_keys = [(str(token_kid), matching_secret)]

    last_error: InvalidTokenError | None = None
    for _kid, secret in candidate_keys:
        try:
            return jwt.decode(
                token,
                secret,
                algorithms=[settings.JWT_ALGORITHM],
                issuer=issuer,
                audience=audience,
            )
        except InvalidTokenError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise InvalidTokenError("No verification keys configured")


def _decode_unverified_claims(token: str) -> dict:
    return jwt.decode(
        token,
        options={
            "verify_signature": False,
            "verify_exp": False,
            "verify_nbf": False,
            "verify_iat": False,
            "verify_iss": False,
            "verify_aud": False,
        },
    )

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, role: str, tenant_id: str) -> str:
    expire = _utcnow_naive() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": tenant_id,
        **_build_registered_claims(
            token_type="access",
            expires_at=expire,
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_ACCESS_JWT_AUDIENCE,
        ),
    }
    return _encode_jwt(payload, signing_key=get_auth_jwt_signing_key())


def create_refresh_token(user_id: str, role: str, tenant_id: str) -> str:
    expire = _utcnow_naive() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": tenant_id,
        **_build_registered_claims(
            token_type="refresh",
            expires_at=expire,
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_REFRESH_JWT_AUDIENCE,
        ),
    }
    return _encode_jwt(payload, signing_key=get_auth_jwt_signing_key())


def create_tracking_token(order_id: str) -> str:
    """Create a 48-hour tracking token scoped to a single order."""
    expire = _utcnow_naive() + timedelta(hours=48)
    payload = {
        "order_id": order_id,
        **_build_registered_claims(
            token_type="tracking",
            expires_at=expire,
            issuer=settings.TRACKING_JWT_ISSUER,
            audience=settings.TRACKING_JWT_AUDIENCE,
        ),
    }
    return _encode_jwt(payload, signing_key=get_tracking_jwt_signing_key())


def decode_tracking_token(token: str) -> dict:
    """Decode and verify a tracking token. Raises HTTPException on failure."""
    try:
        payload = _decode_verified_jwt(
            token,
            verification_keys=get_tracking_jwt_verification_keys(),
            issuer=settings.TRACKING_JWT_ISSUER,
            audience=settings.TRACKING_JWT_AUDIENCE,
        )
        if payload.get("type") != "tracking":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid tracking token")
        return payload
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired tracking token")


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def _get_revoked_token_expiry_db(jti: str) -> datetime | None:
    async with get_async_session_factory()() as db:
        result = await db.execute(select(RevokedToken.expires_at).where(RevokedToken.jti == jti))
        return result.scalar_one_or_none()


async def _store_revoked_token_db(jti: str, expires_at: datetime) -> None:
    async with get_async_session_factory()() as db:
        existing = await db.get(RevokedToken, jti)
        if existing is None:
            db.add(RevokedToken(jti=jti, expires_at=expires_at))
        else:
            existing.expires_at = expires_at
        await db.execute(delete(RevokedToken).where(RevokedToken.expires_at <= datetime.now(UTC).replace(tzinfo=None)))
        await db.commit()


async def _delete_revoked_token_db(jti: str) -> None:
    async with get_async_session_factory()() as db:
        await db.execute(delete(RevokedToken).where(RevokedToken.jti == jti))
        await db.commit()


def decode_token(token: str, expected_type: str | None = None) -> dict:
    try:
        unverified = _decode_unverified_claims(token)
        token_type = expected_type or unverified.get("type")
        audience_factory = _AUTH_TOKEN_AUDIENCES.get(token_type)
        if audience_factory is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
        payload = _decode_verified_jwt(
            token,
            verification_keys=get_auth_jwt_verification_keys(),
            issuer=settings.AUTH_JWT_ISSUER,
            audience=audience_factory(),
        )
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    if expected_type and payload.get("type") != expected_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    return payload


async def is_token_revoked(jti: str | None) -> bool:
    if not jti:
        return True
    redis_client: aioredis.Redis | None = None
    try:
        redis_client = await _get_redis()
        if await redis_client.exists(f"revoked_token:{jti}") == 1:
            return True
    except Exception:
        logger.warning("Token revocation Redis unavailable; checking durable database store.")

    try:
        expires_at = await _get_revoked_token_expiry_db(jti)
    except Exception as exc:
        logger.exception("Token revocation lookup failed in durable store.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication store unavailable",
        ) from exc

    if expires_at is None:
        return False

    now = _utcnow_naive()
    if expires_at <= now:
        await _delete_revoked_token_db(jti)
        return False

    if redis_client is not None:
        ttl = max(1, int((expires_at - now).total_seconds()))
        try:
            await redis_client.set(f"revoked_token:{jti}", "1", ex=ttl)
        except Exception:
            logger.warning("Token revocation Redis refresh failed; durable store remains authoritative.")
    return True


async def revoke_token(token: str) -> None:
    payload = decode_token(token)
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp:
        return
    now_ts = int(_utcnow_naive().timestamp())
    ttl = max(1, int(exp) - now_ts)
    expires_at = datetime.utcfromtimestamp(int(exp))

    try:
        await _store_revoked_token_db(jti, expires_at)
    except Exception as exc:
        logger.exception("Failed to persist revoked token in durable store.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication store unavailable",
        ) from exc

    try:
        r = await _get_redis()
        await r.set(f"revoked_token:{jti}", "1", ex=ttl)
    except Exception:
        logger.warning("Token revocation Redis unavailable; durable database store remains authoritative.")


async def get_user_from_refresh_token(
    token: str,
    db: AsyncSession,
) -> User:
    payload = decode_token(token, expected_type="refresh")
    if await is_token_revoked(payload.get("jti")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked")
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    set_current_tenant_scope(tenant_id)
    result = await db.execute(
        select(User).where(
            User.id == uuid.UUID(user_id),
            User.tenant_id == get_current_tenant_id(tenant_id),
        )
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """FastAPI dependency: extract and verify JWT, return the User object."""
    token = credentials.credentials
    payload = decode_token(token, expected_type="access")
    if await is_token_revoked(payload.get("jti")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access token revoked")
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    set_current_tenant_scope(tenant_id)

    result = await db.execute(
        select(User).where(
            User.id == uuid.UUID(user_id),
            User.tenant_id == get_current_tenant_id(tenant_id),
        )
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def require_role(*roles: UserRole):
    """Dependency factory: require the current user to have one of the given roles.

    Convenience expansions:
      * UserRole.ADMIN expands to ADMIN_LIKE_ROLES (OWNER, ADMIN) so
        callers don't have to enumerate them.
      * UserRole.DISPATCHER expands to also include COMPANY_ADMIN so the
        per-tenant read-only role inherits dispatcher-level visibility
        (orders, routes, fleet, analytics) without unlocking write paths
        — those gate further on operations_policy.team_permissions.
    """
    expanded_roles = set(roles)
    if UserRole.ADMIN in expanded_roles:
        expanded_roles.update(ADMIN_LIKE_ROLES)
    if UserRole.DISPATCHER in expanded_roles:
        expanded_roles.add(UserRole.COMPANY_ADMIN)

    async def role_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role not in expanded_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {[r.value for r in roles]}",
            )
        return current_user

    return role_checker
