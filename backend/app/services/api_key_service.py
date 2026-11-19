import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import ApiClient
from app.schemas.api_key import ApiKeyCreateRequest
from app.utils.api_keys import generate_api_key, get_api_key_lookup_prefix, hash_api_key
from app.utils.tenant_context import get_current_tenant_id, tenant_filter


async def create_api_key(db: AsyncSession, data: ApiKeyCreateRequest, principal) -> tuple[ApiClient, str]:
    raw_key = generate_api_key()
    client = ApiClient(
        tenant_id=get_current_tenant_id(principal),
        name=data.name,
        api_key_hash=hash_api_key(raw_key),
        api_key_prefix=get_api_key_lookup_prefix(raw_key),
        permissions=data.permissions,
        rate_limit_per_min=data.rate_limit_per_min,
        is_active=True,
    )
    db.add(client)
    await db.flush()
    await db.refresh(client)
    return client, raw_key


async def list_api_keys(db: AsyncSession, principal) -> list[ApiClient]:
    result = await db.execute(
        select(ApiClient)
        .where(tenant_filter(ApiClient, principal))
        .order_by(ApiClient.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_api_key(db: AsyncSession, api_key_id: uuid.UUID, principal) -> None:
    result = await db.execute(
        select(ApiClient).where(
            ApiClient.id == api_key_id,
            tenant_filter(ApiClient, principal),
        )
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    client.is_active = False
    await db.flush()
