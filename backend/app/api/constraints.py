import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_write_db
from app.models.enums import UserRole
from app.models.user import User
from app.services import constraint_service, operations_policy_service
from app.utils.auth import require_role

router = APIRouter()

AdminDep = Annotated[User, Depends(require_role(UserRole.OWNER, UserRole.ADMIN))]
DispatcherDep = Annotated[User, Depends(require_role(UserRole.OWNER, UserRole.ADMIN, UserRole.DISPATCHER))]


class ConstraintSet(BaseModel):
    max_route_distance_km: float | None = Field(default=None, ge=0)
    max_stops_per_route: int | None = Field(default=None, ge=0)
    max_weight_kg: float | None = Field(default=None, ge=0)
    max_shift_hours: float | None = Field(default=None, ge=0)
    break_duration_minutes: int | None = Field(default=None, ge=0)
    break_earliest_offset_hours: float | None = Field(default=None, ge=0)
    break_latest_offset_hours: float | None = Field(default=None, ge=0)
    break_required: bool | None = None
    service_time_minutes: int | None = Field(default=None, ge=0)
    allow_fragile: bool | None = None
    allow_temp_sensitive: bool | None = None


@router.get("/global")
async def get_global(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
) -> dict[str, Any]:
    """Get fully resolved global constraints (defaults + overrides)."""
    return await constraint_service.get_global_constraints(db, current_user)


@router.put("/global")
async def set_global(
    data: ConstraintSet,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
) -> dict[str, Any]:
    """Set global constraint overrides (admin only). Send null to clear a constraint."""
    await operations_policy_service.ensure_permission(db, current_user, "manage_settings")
    payload = data.model_dump(exclude_unset=True)
    return await constraint_service.set_global_constraints(db, payload, current_user)


@router.get("/couriers/{courier_id}")
async def get_courier_constraints(
    courier_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
) -> dict[str, Any]:
    """Get resolved constraints for a courier (global + per-courier overrides)."""
    from app.services.courier_service import get_courier
    courier = await get_courier(db, courier_id, current_user)
    return await constraint_service.get_resolved_constraints_for_courier(db, courier)


@router.get("/couriers/{courier_id}/overrides")
async def get_courier_overrides(
    courier_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
) -> dict[str, Any]:
    """Get only the per-courier overrides (not merged with global defaults)."""
    return await constraint_service.get_courier_overrides(db, courier_id, current_user)


@router.put("/couriers/{courier_id}")
async def set_courier_constraints(
    courier_id: uuid.UUID,
    data: ConstraintSet,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
) -> dict[str, Any]:
    """Set per-courier constraint overrides (admin only). Send null to remove an override."""
    await operations_policy_service.ensure_permission(db, current_user, "manage_settings")
    payload = data.model_dump(exclude_unset=True)
    return await constraint_service.set_courier_constraints(db, courier_id, payload, current_user)


@router.delete("/couriers/{courier_id}")
async def clear_courier_constraints(
    courier_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Clear all per-courier constraint overrides (admin only)."""
    await operations_policy_service.ensure_permission(db, current_user, "manage_settings")
    await constraint_service.clear_courier_constraints(db, courier_id, current_user)
    return {"status": "ok"}
