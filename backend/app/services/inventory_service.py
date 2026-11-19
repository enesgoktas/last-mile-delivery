"""
Inventory service — manages packages and inventory events.

Phase 1: Package creation (auto-created with orders).
Phase 4: Full inventory event tracking (loading, delivery, returns).
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import InventoryEventType, PackageStatus
from app.models.inventory_event import InventoryEvent
from app.models.package import Package
from app.utils.compat import utcnow_naive
from app.utils.tenant_context import tenant_filter

logger = logging.getLogger(__name__)


def _generate_package_label() -> str:
    date_part = utcnow_naive().strftime("%Y%m%d")
    unique_part = uuid.uuid4().hex[:6].upper()
    return f"PKG-{date_part}-{unique_part}"


async def create_package_for_order(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    order_id: uuid.UUID,
    weight_kg: float,
    length_cm: float | None = None,
    width_cm: float | None = None,
    height_cm: float | None = None,
    volume_cm3: float | None = None,
    depot_id: uuid.UUID | None = None,
) -> Package:
    computed_volume = volume_cm3
    if computed_volume is None and length_cm and width_cm and height_cm:
        computed_volume = length_cm * width_cm * height_cm

    package = Package(
        tenant_id=tenant_id,
        order_id=order_id,
        label=_generate_package_label(),
        weight_kg=weight_kg,
        length_cm=length_cm,
        width_cm=width_cm,
        height_cm=height_cm,
        volume_cm3=computed_volume,
        status=PackageStatus.AT_DEPOT,
    )
    db.add(package)
    await db.flush()

    if depot_id:
        event = InventoryEvent(
            tenant_id=tenant_id,
            package_id=package.id,
            event_type=InventoryEventType.RECEIVED_AT_DEPOT,
            depot_id=depot_id,
        )
        db.add(event)
        await db.flush()

    await db.refresh(package)
    return package


async def get_packages_for_order(
    db: AsyncSession,
    order_id: uuid.UUID,
    principal=None,
) -> list[Package]:
    query = select(Package).where(Package.order_id == order_id)
    if principal is not None:
        query = query.where(tenant_filter(Package, principal))
    result = await db.execute(
        query.order_by(Package.created_at)
    )
    return list(result.scalars().all())


async def get_package(db: AsyncSession, package_id: uuid.UUID, principal=None) -> Package | None:
    query = select(Package).where(Package.id == package_id)
    if principal is not None:
        query = query.where(tenant_filter(Package, principal))
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_package_events(
    db: AsyncSession,
    package_id: uuid.UUID,
    principal=None,
) -> list[InventoryEvent]:
    query = select(InventoryEvent).where(InventoryEvent.package_id == package_id)
    if principal is not None:
        query = query.where(tenant_filter(InventoryEvent, principal))
    result = await db.execute(
        query.order_by(InventoryEvent.created_at)
    )
    return list(result.scalars().all())


async def record_event(
    db: AsyncSession,
    *,
    package_id: uuid.UUID,
    event_type: InventoryEventType,
    courier_id: uuid.UUID | None = None,
    depot_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> InventoryEvent:
    pkg = await get_package(db, package_id)
    if pkg is None:
        raise ValueError(f"Package {package_id} not found")

    event = InventoryEvent(
        tenant_id=pkg.tenant_id,
        package_id=package_id,
        event_type=event_type,
        courier_id=courier_id,
        depot_id=depot_id,
        notes=notes,
    )
    db.add(event)

    status_map: dict[InventoryEventType, PackageStatus] = {
        InventoryEventType.RECEIVED_AT_DEPOT: PackageStatus.AT_DEPOT,
        InventoryEventType.LOADED_ONTO_VEHICLE: PackageStatus.LOADED,
        InventoryEventType.DEPARTED_DEPOT: PackageStatus.IN_TRANSIT,
        InventoryEventType.DELIVERED_TO_CUSTOMER: PackageStatus.DELIVERED,
        InventoryEventType.RETURN_RECEIVED: PackageStatus.RETURNED_TO_DEPOT,
        InventoryEventType.PICKED_UP_FROM_CUSTOMER: PackageStatus.IN_TRANSIT,
        InventoryEventType.RETURNED_TO_DEPOT: PackageStatus.RETURNED_TO_DEPOT,
        InventoryEventType.MARKED_LOST: PackageStatus.LOST,
    }
    new_status = status_map.get(event_type)
    if new_status:
        pkg.status = new_status

    await db.flush()
    await db.refresh(event)
    return event


async def get_courier_current_load(
    db: AsyncSession,
    courier_id: uuid.UUID,
    principal=None,
) -> list[Package]:
    """Return packages currently loaded on a courier's vehicle."""
    query = (
        select(Package).where(
            Package.status.in_([PackageStatus.LOADED, PackageStatus.IN_TRANSIT]),
        )
        .join(
            InventoryEvent,
            InventoryEvent.package_id == Package.id,
        )
        .where(
            InventoryEvent.courier_id == courier_id,
            InventoryEvent.event_type == InventoryEventType.LOADED_ONTO_VEHICLE,
        )
        .distinct()
    )
    if principal is not None:
        query = query.where(
            tenant_filter(Package, principal),
            tenant_filter(InventoryEvent, principal),
        )
    result = await db.execute(
        query
    )
    return list(result.scalars().all())
