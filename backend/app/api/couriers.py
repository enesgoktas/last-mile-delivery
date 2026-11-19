import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.database import get_db, get_write_db
from app.models.enums import UserRole
from app.models.user import User
from app.models.vehicle_tag import VehicleTag
from app.schemas.vehicle_tag import VehicleTagResponse
from app.schemas.courier import (
    BatteryTelemetryUpdate,
    CourierAnalyticsResponse,
    CourierCreate,
    CourierLocationResponse,
    CourierResponse,
    CourierUpdate,
    LocationUpdate,
    PushTokenUpdate,
    StatusUpdate,
)
from app.schemas.route import RouteResponse, RouteStopResponse
from app.services import courier_service, route_service
from app.utils.auth import get_current_user, require_role
from app.utils.tenant_context import tenant_filter

router = APIRouter()

AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN))]
DispatcherDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))]
CourierDep = Annotated[User, Depends(require_role(UserRole.COURIER))]


@router.post("", response_model=CourierResponse, status_code=201)
async def create_courier(
    data: CourierCreate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Create a courier profile (admin only)."""
    return await courier_service.create_courier(db, data, current_user)


@router.get("", response_model=list[CourierResponse])
async def list_couriers(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    response: Response,
    current_user: DispatcherDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """List all couriers."""
    total = await courier_service.count_couriers(db, current_user)
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    return await courier_service.list_couriers(db, current_user, skip=skip, limit=limit)


@router.get("/locations", response_model=list[CourierLocationResponse])
async def list_latest_courier_locations(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Latest courier coordinates for dispatcher map overlays (Redis-first, DB fallback)."""
    return await courier_service.list_latest_locations_for_dispatcher(db, current_user)


@router.get("/me", response_model=CourierResponse)
async def get_my_courier_profile(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Return courier profile for the authenticated user."""
    return await courier_service.get_courier_for_user_or_404(db, current_user)


@router.get("/{courier_id}", response_model=CourierResponse)
async def get_courier(
    courier_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    """Get courier details."""
    return await courier_service.get_courier(db, courier_id, current_user)


@router.put("/{courier_id}", response_model=CourierResponse)
async def update_courier(
    courier_id: uuid.UUID,
    data: CourierUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Update courier profile (admin only)."""
    return await courier_service.update_courier(db, courier_id, data, current_user)


@router.put("/{courier_id}/location")
async def update_location(
    courier_id: uuid.UUID,
    data: LocationUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Update courier GPS location (called by courier app every 30s)."""
    await courier_service.ensure_user_can_access_courier(db, current_user, courier_id)
    return await courier_service.update_location(db, courier_id, data)


@router.put("/{courier_id}/status", response_model=CourierResponse)
async def update_status(
    courier_id: uuid.UUID,
    data: StatusUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Toggle courier availability status."""
    await courier_service.ensure_user_can_access_courier(db, current_user, courier_id)
    return await courier_service.update_status(db, courier_id, data.status)


@router.post("/{courier_id}/battery", response_model=CourierResponse)
async def update_battery(
    courier_id: uuid.UUID,
    data: BatteryTelemetryUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Record current battery state-of-charge for an EV courier (Phase 1).

    Called by the driver app on shift start + periodically, or by a
    dispatcher entering a manual reading. Always accepted regardless of
    the tenant's `ev_battery_awareness_enabled` flag — dispatch consumers
    only act on the value when the flag is on.
    """
    await courier_service.ensure_user_can_access_courier(db, current_user, courier_id)
    return await courier_service.update_battery(db, courier_id, data.pct, current_user)


class CourierTagsUpdate(BaseModel):
    tag_ids: list[uuid.UUID]


@router.put("/{courier_id}/tags", response_model=list[VehicleTagResponse])
async def set_courier_tags(
    courier_id: uuid.UUID,
    data: CourierTagsUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Replace the full set of tags for a courier (admin only)."""
    from sqlalchemy import select
    courier = await courier_service.get_courier(db, courier_id, current_user)
    if data.tag_ids:
        result = await db.execute(
            select(VehicleTag).where(
                VehicleTag.id.in_(data.tag_ids),
                tenant_filter(VehicleTag, current_user),
            )
        )
        tags = list(result.scalars().all())
    else:
        tags = []
    courier.vehicle_tags = tags
    await db.commit()
    await db.refresh(courier)
    return courier.vehicle_tags


@router.put("/{courier_id}/push-token", response_model=CourierResponse)
async def update_push_token(
    courier_id: uuid.UUID,
    data: PushTokenUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Register/update courier device push token for the configured push provider."""
    await courier_service.ensure_user_can_access_courier(db, current_user, courier_id)
    return await courier_service.update_push_token(
        db,
        courier_id,
        push_provider=data.push_provider,
        push_token=data.push_token,
        fcm_token=data.fcm_token,
    )


@router.get("/{courier_id}/analytics", response_model=CourierAnalyticsResponse)
async def get_courier_analytics(
    courier_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
):
    """Get basic courier performance metrics."""
    return await courier_service.get_courier_analytics(db, courier_id, current_user)


@router.get("/{courier_id}/active-route", response_model=RouteResponse | None)
async def get_active_route(
    courier_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get courier's latest active/planned route for mobile consumption."""
    await courier_service.ensure_user_can_access_courier(db, current_user, courier_id)
    return await route_service.get_active_route_for_courier(db, courier_id, current_user)


@router.get("/{courier_id}/next-stop", response_model=RouteStopResponse | None)
async def get_next_stop(
    courier_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get next actionable stop for the courier."""
    await courier_service.ensure_user_can_access_courier(db, current_user, courier_id)
    return await route_service.get_next_stop_for_courier(db, courier_id, current_user)
