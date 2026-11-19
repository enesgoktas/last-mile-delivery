import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.depot import Depot
from app.schemas.depot import DepotCreate, DepotUpdate
from app.utils.tenant_context import get_current_tenant_id, tenant_filter

logger = logging.getLogger(__name__)


async def create_depot(db: AsyncSession, data: DepotCreate, principal) -> Depot:
    depot = Depot(
        tenant_id=get_current_tenant_id(principal),
        name=data.name,
        address=data.address,
        province_code=data.province_code,
        province_name=data.province_name,
        lat=data.lat,
        lon=data.lon,
        operating_hours_start=data.operating_hours_start,
        operating_hours_end=data.operating_hours_end,
        is_active=data.is_active,
    )
    db.add(depot)
    await db.flush()
    await db.refresh(depot)
    return depot


async def get_depot(db: AsyncSession, depot_id: uuid.UUID, principal) -> Depot:
    result = await db.execute(
        select(Depot).where(
            Depot.id == depot_id,
            tenant_filter(Depot, principal),
        )
    )
    depot = result.scalar_one_or_none()
    if not depot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Depot not found")
    return depot


async def list_depots(
    db: AsyncSession,
    principal,
    *,
    active_only: bool = False,
    skip: int = 0,
    limit: int = 50,
) -> list[Depot]:
    query = select(Depot).where(tenant_filter(Depot, principal))
    if active_only:
        query = query.where(Depot.is_active.is_(True))
    query = query.order_by(Depot.name).offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def count_depots(db: AsyncSession, principal, *, active_only: bool = False) -> int:
    query = select(sa_func.count(Depot.id)).where(tenant_filter(Depot, principal))
    if active_only:
        query = query.where(Depot.is_active.is_(True))
    result = await db.execute(query)
    return result.scalar_one()


async def update_depot(db: AsyncSession, depot_id: uuid.UUID, data: DepotUpdate, principal) -> Depot:
    depot = await get_depot(db, depot_id, principal)
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(depot, key, value)
    await db.flush()
    await db.refresh(depot)
    return depot


async def delete_depot(db: AsyncSession, depot_id: uuid.UUID, principal) -> None:
    depot = await get_depot(db, depot_id, principal)
    await db.delete(depot)
    await db.flush()
