from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant

DEFAULT_TENANT_NAME = "Default"
DEFAULT_TENANT_SLUG = "default"


async def ensure_default_tenant(db: AsyncSession) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(
            name=DEFAULT_TENANT_NAME,
            slug=DEFAULT_TENANT_SLUG,
            is_active=True,
        )
        db.add(tenant)
        await db.flush()
    return tenant
