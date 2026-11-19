import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_write_db
from app.models.enums import UserRole
from app.models.user import User
from app.models.vehicle_tag import VehicleTag
from app.schemas.vehicle_tag import VehicleTagCreate, VehicleTagResponse, VehicleTagUpdate
from app.utils.auth import require_role
from app.utils.tenant_context import tenant_filter

router = APIRouter()

AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN))]
DispatcherDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))]


@router.get("", response_model=list[VehicleTagResponse])
async def list_vehicle_tags(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
):
    """List all vehicle tags for the current tenant."""
    result = await db.execute(
        select(VehicleTag)
        .where(tenant_filter(VehicleTag, current_user))
        .order_by(VehicleTag.name)
    )
    return result.scalars().all()


@router.post("", response_model=VehicleTagResponse, status_code=201)
async def create_vehicle_tag(
    data: VehicleTagCreate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Create a new vehicle tag (admin only)."""
    tag = VehicleTag(
        tenant_id=current_user.tenant_id,
        name=data.name,
        color=data.color,
    )
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


@router.patch("/{tag_id}", response_model=VehicleTagResponse)
async def update_vehicle_tag(
    tag_id: uuid.UUID,
    data: VehicleTagUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Update a vehicle tag's name or color (admin only)."""
    tag = await _get_tag_or_404(db, tag_id, current_user)
    if data.name is not None:
        tag.name = data.name
    if data.color is not None:
        tag.color = data.color
    await db.commit()
    await db.refresh(tag)
    return tag


@router.delete("/{tag_id}", status_code=204)
async def delete_vehicle_tag(
    tag_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Delete a vehicle tag. Association rows are cascade-deleted (admin only)."""
    await _get_tag_or_404(db, tag_id, current_user)
    await db.execute(delete(VehicleTag).where(VehicleTag.id == tag_id))
    await db.commit()


async def _get_tag_or_404(
    db: AsyncSession,
    tag_id: uuid.UUID,
    current_user: User,
) -> VehicleTag:
    result = await db.execute(
        select(VehicleTag).where(
            VehicleTag.id == tag_id,
            tenant_filter(VehicleTag, current_user),
        )
    )
    tag = result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    return tag
