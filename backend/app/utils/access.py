import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enums import DISPATCH_ACCESS_ROLES
from app.models.user import User
from app.models.webhook import ApiClient
from app.services.audit_service import log_audit_event
from app.utils.api_keys import get_api_key_lookup_prefix, verify_api_key
from app.utils.auth import decode_token, is_token_revoked
from app.utils.client_ip import get_request_client_ip
from app.utils.rate_limit import enforce_fixed_window_limit
from app.utils.tenant_context import get_current_tenant_id, set_current_tenant_scope

bearer_optional = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(slots=True)
class AccessContext:
    user: User | None
    api_client: ApiClient | None
    source: str  # "jwt" | "api_key"


async def _find_api_client_for_key(db: AsyncSession, api_key: str) -> ApiClient | None:
    lookup_prefix = get_api_key_lookup_prefix(api_key)
    prefixed_result = await db.execute(
        select(ApiClient).where(
            ApiClient.is_active.is_(True),
            ApiClient.api_key_prefix == lookup_prefix,
        )
    )
    prefixed_candidates = list(prefixed_result.scalars().all())
    client = next(
        (candidate for candidate in prefixed_candidates if verify_api_key(api_key, candidate.api_key_hash)),
        None,
    )
    if client:
        return client

    legacy_result = await db.execute(
        select(ApiClient).where(
            ApiClient.is_active.is_(True),
            ApiClient.api_key_prefix.is_(None),
        )
    )
    return next(
        (candidate for candidate in legacy_result.scalars().all() if verify_api_key(api_key, candidate.api_key_hash)),
        None,
    )


def require_dispatcher_or_api_scope(*required_scopes: str):
    async def dependency(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_optional)],
        api_key: Annotated[str | None, Depends(api_key_header)],
        db: Annotated[AsyncSession, Depends(get_db)],
        request: Request,
    ) -> AccessContext:
        if api_key:
            client = await _find_api_client_for_key(db, api_key)
            if not client:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key",
                )

            if required_scopes and not set(required_scopes).issubset(set(client.permissions)):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="API key does not have required scope",
                )

            await enforce_fixed_window_limit(
                key=f"rl:api_key:{client.id}",
                limit=client.rate_limit_per_min,
                window_seconds=60,
            )

            await log_audit_event(
                db,
                action="api_key.request",
                tenant_id=get_current_tenant_id(client),
                resource_type="api_client",
                resource_id=str(client.id),
                details={
                    "method": request.method,
                    "path": request.url.path,
                    "required_scopes": list(required_scopes),
                    "api_key_name": client.name,
                },
                ip_address=get_request_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            set_current_tenant_scope(client)
            return AccessContext(user=None, api_client=client, source="api_key")

        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        payload = decode_token(credentials.credentials, expected_type="access")
        if await is_token_revoked(payload.get("jti")):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Access token revoked",
            )

        user_id = payload.get("sub")
        tenant_id = payload.get("tenant_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )
        set_current_tenant_scope(tenant_id)

        result = await db.execute(
            select(User).where(
                User.id == uuid.UUID(user_id),
                User.tenant_id == get_current_tenant_id(tenant_id),
            )
        )
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
            )

        if user.role not in DISPATCH_ACCESS_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Dispatcher or admin role required",
            )

        return AccessContext(user=user, api_client=None, source="jwt")

    return dependency
