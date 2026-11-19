"""
Platform-level superadmin API.

All endpoints require the X-Superadmin-Key header to match the SUPERADMIN_API_KEY
environment variable. This key must be kept secret and is never exposed to tenants.
"""
from __future__ import annotations

import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_write_db
from app.models.order import Order
from app.models.route import Route
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.operations_policy import OptimizationSettings
from app.services import operations_policy_service

router = APIRouter(prefix="/admin", tags=["superadmin"])


def _require_superadmin(x_superadmin_key: Annotated[str | None, Header()] = None) -> None:
    configured = settings.SUPERADMIN_API_KEY
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Superadmin API is not enabled on this instance",
        )
    if not x_superadmin_key or not secrets.compare_digest(x_superadmin_key, configured):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid superadmin key",
        )


SuperadminDep = Annotated[None, Depends(_require_superadmin)]


class TenantSummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    user_count: int
    order_count: int
    route_count: int


class TenantUpdate(BaseModel):
    is_active: bool


@router.get("/tenants", response_model=list[TenantSummary])
async def list_tenants(
    _: SuperadminDep,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    skip: int = 0,
    limit: int = 100,
):
    """List all tenants (superadmin only)."""
    result = await db.execute(
        select(Tenant).order_by(Tenant.name).offset(skip).limit(limit)
    )
    tenants = list(result.scalars().all())

    summaries = []
    for tenant in tenants:
        user_count_result = await db.execute(
            select(func.count()).select_from(User).where(User.tenant_id == tenant.id)
        )
        order_count_result = await db.execute(
            select(func.count()).select_from(Order).where(Order.tenant_id == tenant.id)
        )
        route_count_result = await db.execute(
            select(func.count()).select_from(Route).where(Route.tenant_id == tenant.id)
        )
        summaries.append(
            TenantSummary(
                id=tenant.id,
                name=tenant.name,
                slug=tenant.slug,
                is_active=tenant.is_active,
                user_count=int(user_count_result.scalar_one()),
                order_count=int(order_count_result.scalar_one()),
                route_count=int(route_count_result.scalar_one()),
            )
        )
    return summaries


@router.get("/tenants/{tenant_id}", response_model=TenantSummary)
async def get_tenant(
    tenant_id: uuid.UUID,
    _: SuperadminDep,
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Get a single tenant by ID (superadmin only)."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    user_count = int((await db.execute(
        select(func.count()).select_from(User).where(User.tenant_id == tenant.id)
    )).scalar_one())
    order_count = int((await db.execute(
        select(func.count()).select_from(Order).where(Order.tenant_id == tenant.id)
    )).scalar_one())
    route_count = int((await db.execute(
        select(func.count()).select_from(Route).where(Route.tenant_id == tenant.id)
    )).scalar_one())

    return TenantSummary(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        is_active=tenant.is_active,
        user_count=user_count,
        order_count=order_count,
        route_count=route_count,
    )


@router.get("/tenants/{tenant_id}/optimization-settings", response_model=OptimizationSettings)
async def get_tenant_optimization_settings(
    tenant_id: uuid.UUID,
    _: SuperadminDep,
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    policy = await operations_policy_service.get_operations_policy(db, tenant_id)
    return policy.optimization


@router.put("/tenants/{tenant_id}/optimization-settings", response_model=OptimizationSettings)
async def update_tenant_optimization_settings(
    tenant_id: uuid.UUID,
    payload: OptimizationSettings,
    _: SuperadminDep,
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    policy = await operations_policy_service.set_internal_optimization_settings(
        db,
        tenant_id,
        payload.model_dump(mode="json"),
    )
    return policy.optimization


@router.patch("/tenants/{tenant_id}", response_model=TenantSummary)
async def update_tenant(
    tenant_id: uuid.UUID,
    data: TenantUpdate,
    _: SuperadminDep,
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Activate or deactivate a tenant (superadmin only)."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    tenant.is_active = data.is_active
    await db.flush()
    await db.refresh(tenant)

    user_count = int((await db.execute(
        select(func.count()).select_from(User).where(User.tenant_id == tenant.id)
    )).scalar_one())
    order_count = int((await db.execute(
        select(func.count()).select_from(Order).where(Order.tenant_id == tenant.id)
    )).scalar_one())
    route_count = int((await db.execute(
        select(func.count()).select_from(Route).where(Route.tenant_id == tenant.id)
    )).scalar_one())

    return TenantSummary(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        is_active=tenant.is_active,
        user_count=user_count,
        order_count=order_count,
        route_count=route_count,
    )
