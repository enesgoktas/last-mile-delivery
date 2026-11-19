import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_write_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.package import (
    CourierCurrentLoadResponse,
    CurrentLoadItem,
    InventoryEventResponse,
    PackageResponse,
    PackageUpdate,
)
from app.services import inventory_service
from app.utils.auth import require_role

router = APIRouter()

DispatcherDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))]
AnyAuthDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER, UserRole.COURIER))]


@router.get("/orders/{order_id}/packages", response_model=list[PackageResponse])
async def get_order_packages(
    order_id: uuid.UUID,
    current_user: AnyAuthDep,
    db: AsyncSession = Depends(get_write_db),
):
    return await inventory_service.get_packages_for_order(db, order_id, current_user)


@router.get("/packages/{package_id}", response_model=PackageResponse)
async def get_package(
    package_id: uuid.UUID,
    current_user: DispatcherDep,
    db: AsyncSession = Depends(get_write_db),
):
    pkg = await inventory_service.get_package(db, package_id, current_user)
    if not pkg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")
    return pkg


@router.put("/packages/{package_id}", response_model=PackageResponse)
async def update_package(
    package_id: uuid.UUID,
    data: PackageUpdate,
    current_user: DispatcherDep,
    db: AsyncSession = Depends(get_write_db),
):
    pkg = await inventory_service.get_package(db, package_id, current_user)
    if not pkg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(pkg, key, value)
    if (
        ("length_cm" in update_data or "width_cm" in update_data or "height_cm" in update_data)
        and pkg.length_cm
        and pkg.width_cm
        and pkg.height_cm
        and "volume_cm3" not in update_data
    ):
        pkg.volume_cm3 = pkg.length_cm * pkg.width_cm * pkg.height_cm
    await db.flush()
    await db.refresh(pkg)
    return pkg


@router.get("/packages/{package_id}/events", response_model=list[InventoryEventResponse])
async def get_package_events(
    package_id: uuid.UUID,
    current_user: DispatcherDep,
    db: AsyncSession = Depends(get_db),
):
    return await inventory_service.get_package_events(db, package_id, current_user)


@router.get("/couriers/{courier_id}/current-load", response_model=CourierCurrentLoadResponse)
async def get_courier_current_load(
    courier_id: uuid.UUID,
    current_user: AnyAuthDep,
    db: AsyncSession = Depends(get_db),
):
    packages = await inventory_service.get_courier_current_load(db, courier_id, current_user)
    items = [
        CurrentLoadItem(
            package_id=pkg.id,
            order_id=pkg.order_id,
            label=pkg.label,
            weight_kg=pkg.weight_kg,
            volume_cm3=pkg.volume_cm3,
            status=pkg.status,
        )
        for pkg in packages
    ]
    return CourierCurrentLoadResponse(
        courier_id=courier_id,
        total_weight_kg=sum(p.weight_kg for p in packages),
        total_volume_cm3=sum(p.volume_cm3 or 0 for p in packages),
        package_count=len(packages),
        packages=items,
    )
