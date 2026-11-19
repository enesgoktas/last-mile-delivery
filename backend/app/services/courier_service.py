import json
import logging
import uuid

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.courier import Courier
from app.models.depot import Depot
from app.models.enums import (
    DISPATCH_ACCESS_ROLES,
    AvailabilityStatus,
    OrderStatus,
    PushProvider,
    RouteStatus,
    UserRole,
)
from app.models.location import LocationSnapshot
from app.models.order import Order
from app.models.route import Route
from app.models.user import User
from app.schemas.courier import CourierCreate, CourierUpdate, LocationUpdate
from app.services import operations_policy_service
from app.utils.compat import utcnow_naive
from app.utils.tenant_context import get_current_tenant_id, tenant_filter
from app.workers.task_dispatch import enqueue_task

# Redis client (initialized lazily)
_redis: aioredis.Redis | None = None
_redis_cache_unavailable_logged = False
logger = logging.getLogger(__name__)


def _resolve_push_registration(
    *,
    push_provider: PushProvider | None,
    push_token: str | None,
    fcm_token: str | None,
) -> tuple[PushProvider | None, str | None, str | None]:
    if push_provider is not None or push_token is not None:
        normalized_token = push_token.strip() if isinstance(push_token, str) else push_token
        if not normalized_token:
            return None, None, None
        legacy_fcm_token = normalized_token if push_provider == PushProvider.FCM else None
        return push_provider, normalized_token, legacy_fcm_token

    normalized_fcm_token = fcm_token.strip() if isinstance(fcm_token, str) else fcm_token
    if not normalized_fcm_token:
        return None, None, None
    return PushProvider.FCM, normalized_fcm_token, normalized_fcm_token


# Sentinel for "field was not provided in the update payload" — distinct
# from None ("explicitly clear"). Used by _resolve_multi_depot_fields.
_UNSET: object = object()


async def _resolve_multi_depot_fields(
    db: AsyncSession,
    principal,
    *,
    home_depot_id: uuid.UUID | None,
    additional_depot_ids: list[uuid.UUID] | None,
    end_depot_id: uuid.UUID | None,
    fields_provided: set[str],
) -> tuple[list[Depot] | None, uuid.UUID | None | object]:
    """Validate the multi-depot fields against the tenant's feature flag.

    Returns ``(resolved_additional, end_depot_id)``:

    - ``resolved_additional`` is ``None`` when the caller did not provide
      ``additional_depot_ids`` (leave the M2M relationship untouched), or
      a list[Depot] (possibly empty) when the caller did provide it.
    - ``end_depot_id`` is the literal sentinel ``_UNSET`` when the caller
      didn't provide it, ``None`` when explicitly cleared, or a UUID.

    Raises 400 when the feature flag is off and the caller tried to set
    either field to a non-empty / non-null value.
    """
    provided_additional = "additional_depot_ids" in fields_provided
    provided_end = "end_depot_id" in fields_provided

    if not provided_additional and not provided_end:
        return None, _UNSET

    policy = await operations_policy_service.get_operations_policy(db, principal)
    flag_on = policy.feature_flags.multi_depot_vehicles_enabled

    non_empty_additional = provided_additional and bool(additional_depot_ids)
    non_null_end = provided_end and end_depot_id is not None

    if not flag_on and (non_empty_additional or non_null_end):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Multi-depot vehicle fields require "
                "`features.multi_depot_vehicles_enabled` to be enabled for this tenant."
            ),
        )

    resolved_additional: list[Depot] | None = None
    if provided_additional:
        ids = list(additional_depot_ids or [])
        if len(set(ids)) != len(ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="additional_depot_ids contains duplicates",
            )
        if not ids:
            resolved_additional = []
        else:
            result = await db.execute(
                select(Depot).where(
                    Depot.id.in_(ids),
                    tenant_filter(Depot, principal),
                )
            )
            depots = list(result.scalars().all())
            if len(depots) != len(ids):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="One or more additional_depot_ids not found in tenant",
                )
            resolved_additional = depots

    resolved_end: uuid.UUID | None | object = _UNSET
    if provided_end:
        if end_depot_id is None:
            resolved_end = None
        else:
            # A shift-end depot only makes sense for a courier that has
            # a shift-start (home) depot. Reject the depotless + end
            # combination explicitly so it doesn't decay into "end at
            # home" at dispatch time (where home is also undefined).
            if home_depot_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "end_depot_id requires the courier to have a "
                        "home depot_id set"
                    ),
                )
            existence_result = await db.execute(
                select(Depot.id).where(
                    Depot.id == end_depot_id,
                    tenant_filter(Depot, principal),
                )
            )
            if existence_result.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="end_depot_id not found in tenant",
                )
            allowed: set[uuid.UUID] = set()
            if home_depot_id is not None:
                allowed.add(home_depot_id)
            if resolved_additional is not None:
                allowed.update(d.id for d in resolved_additional)
            if end_depot_id not in allowed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "end_depot_id must be the home depot or one of "
                        "additional_depot_ids"
                    ),
                )
            resolved_end = end_depot_id

    return resolved_additional, resolved_end


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def create_courier(db: AsyncSession, data: CourierCreate, principal) -> Courier:
    user_result = await db.execute(
        select(User).where(
            User.id == data.user_id,
            tenant_filter(User, principal),
        )
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if data.depot_id is not None:
        depot_result = await db.execute(
            select(Depot).where(
                Depot.id == data.depot_id,
                tenant_filter(Depot, principal),
            )
        )
        if depot_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Depot not found")

    push_provider, push_token, fcm_token = _resolve_push_registration(
        push_provider=data.push_provider,
        push_token=data.push_token,
        fcm_token=data.fcm_token,
    )

    # Multi-depot fields are gated by the tenant feature flag. We treat a
    # CourierCreate payload as "explicitly provided" only when the caller
    # sent a non-None value; otherwise the helper short-circuits and
    # leaves the M2M empty / end_depot_id NULL.
    create_provided: set[str] = set()
    if data.additional_depot_ids is not None:
        create_provided.add("additional_depot_ids")
    if data.end_depot_id is not None:
        create_provided.add("end_depot_id")
    resolved_additional, resolved_end = await _resolve_multi_depot_fields(
        db,
        principal,
        home_depot_id=data.depot_id,
        additional_depot_ids=data.additional_depot_ids,
        end_depot_id=data.end_depot_id,
        fields_provided=create_provided,
    )

    courier = Courier(
        tenant_id=get_current_tenant_id(principal),
        user_id=data.user_id,
        vehicle_type=data.vehicle_type,
        max_weight_kg=data.max_weight_kg,
        max_volume_cm3=data.max_volume_cm3,
        max_packages=data.max_packages,
        depot_lat=data.depot_lat,
        depot_lon=data.depot_lon,
        depot_id=data.depot_id,
        end_depot_id=resolved_end if resolved_end is not _UNSET else None,
        working_hours_start=data.working_hours_start,
        working_hours_end=data.working_hours_end,
        zone_polygon=data.zone_polygon,
        fcm_token=fcm_token,
        push_provider=push_provider,
        push_token=push_token,
        vehicle_spec_id=data.vehicle_spec_id,
        max_passengers=data.max_passengers,
        max_battery_range_km=data.max_battery_range_km,
        current_battery_pct=data.current_battery_pct,
        battery_low_threshold_pct=(
            data.battery_low_threshold_pct if data.battery_low_threshold_pct is not None else 20
        ),
    )
    if resolved_additional is not None:
        courier.additional_depots = resolved_additional
    db.add(courier)
    await db.flush()
    await db.refresh(courier)
    return courier


async def get_courier(db: AsyncSession, courier_id: uuid.UUID, principal=None) -> Courier:
    query = select(Courier).where(Courier.id == courier_id)
    if principal is not None:
        query = query.where(tenant_filter(Courier, principal))
    result = await db.execute(query)
    courier = result.scalar_one_or_none()
    if not courier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Courier not found")
    return courier


async def get_courier_by_user_id(db: AsyncSession, user_id: uuid.UUID, principal=None) -> Courier | None:
    query = select(Courier).where(Courier.user_id == user_id)
    if principal is not None:
        query = query.where(tenant_filter(Courier, principal))
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_courier_for_user_or_404(db: AsyncSession, user: User) -> Courier:
    courier = await get_courier_by_user_id(db, user.id, user)
    if courier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Courier profile not found for user",
        )
    return courier


async def ensure_user_can_access_courier(
    db: AsyncSession,
    current_user: User,
    courier_id: uuid.UUID,
) -> None:
    if current_user.role in DISPATCH_ACCESS_ROLES:
        return
    if current_user.role != UserRole.COURIER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )

    own_profile = await get_courier_by_user_id(db, current_user.id, current_user)
    if own_profile is None or own_profile.id != courier_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Couriers can only access their own profile",
        )


async def count_couriers(db: AsyncSession, principal) -> int:
    from sqlalchemy import func as sa_func
    result = await db.execute(select(sa_func.count(Courier.id)).where(tenant_filter(Courier, principal)))
    return result.scalar_one()


async def list_couriers(db: AsyncSession, principal, skip: int = 0, limit: int = 50) -> list[Courier]:
    result = await db.execute(
        select(Courier)
        .where(tenant_filter(Courier, principal))
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_battery(db: AsyncSession, courier_id: uuid.UUID, pct: int, principal) -> Courier:
    """Record a battery state-of-charge reading for the courier.

    Called by the driver app on shift start + periodically, or by the
    dispatcher UI for manual updates. Does not check whether the tenant
    has EV awareness enabled — recording the reading is harmless even
    when the flag is off; downstream dispatch only consumes it when
    `features.ev_battery_awareness_enabled`.
    """
    courier = await get_courier(db, courier_id, principal)
    courier.current_battery_pct = max(0, min(100, int(pct)))
    await db.flush()
    await db.refresh(courier)
    return courier


async def update_courier(db: AsyncSession, courier_id: uuid.UUID, data: CourierUpdate, principal) -> Courier:
    courier = await get_courier(db, courier_id, principal)
    update_data = data.model_dump(exclude_unset=True)

    # Multi-depot fields are validated/applied separately. `additional_depot_ids`
    # is not a real ORM column (it's a property), so it must be removed from
    # update_data before the setattr loop. `end_depot_id` IS a real column,
    # but we still route it through the helper for feature-flag + allowed-set
    # validation; the helper returns the resolved value to apply.
    multi_depot_fields_provided: set[str] = set()
    if "additional_depot_ids" in update_data:
        multi_depot_fields_provided.add("additional_depot_ids")
    if "end_depot_id" in update_data:
        multi_depot_fields_provided.add("end_depot_id")
    new_additional_ids_raw = update_data.pop("additional_depot_ids", None)
    new_end_depot_raw = update_data.pop("end_depot_id", None)
    new_home_depot_id = update_data.get("depot_id", courier.depot_id)

    # When the caller shrinks `additional_depot_ids` without explicitly
    # touching `end_depot_id`, the existing `end_depot_id` may no longer
    # be in the allowed set. Treat it as implicitly provided so the
    # validation runs and rejects the now-stale value rather than
    # silently letting it drift (where the solver would silently fall
    # back to the home depot).
    if (
        "additional_depot_ids" in multi_depot_fields_provided
        and "end_depot_id" not in multi_depot_fields_provided
        and courier.end_depot_id is not None
    ):
        multi_depot_fields_provided.add("end_depot_id")
        new_end_depot_raw = courier.end_depot_id

    resolved_additional, resolved_end = await _resolve_multi_depot_fields(
        db,
        principal,
        home_depot_id=new_home_depot_id,
        additional_depot_ids=new_additional_ids_raw,
        end_depot_id=new_end_depot_raw,
        fields_provided=multi_depot_fields_provided,
    )

    has_push_fields = any(key in update_data for key in {"push_provider", "push_token", "fcm_token"})
    for key, value in update_data.items():
        setattr(courier, key, value)
    if has_push_fields:
        push_provider, push_token, fcm_token = _resolve_push_registration(
            push_provider=update_data.get("push_provider"),
            push_token=update_data.get("push_token"),
            fcm_token=update_data.get("fcm_token"),
        )
        courier.push_provider = push_provider
        courier.push_token = push_token
        courier.fcm_token = fcm_token

    if resolved_additional is not None:
        courier.additional_depots = resolved_additional
    if resolved_end is not _UNSET:
        courier.end_depot_id = resolved_end  # type: ignore[assignment]

    await db.flush()
    await db.refresh(courier)
    return courier


async def update_location(
    db: AsyncSession, courier_id: uuid.UUID, data: LocationUpdate
) -> dict:
    """Record location snapshot to DB and update Redis cache."""
    # Verify courier exists
    courier = await get_courier(db, courier_id)

    # Write to DB
    snapshot = LocationSnapshot(
        tenant_id=courier.tenant_id,
        courier_id=courier_id,
        lat=data.lat,
        lon=data.lon,
        heading=data.heading,
        speed_kmh=data.speed_kmh,
        accuracy_m=data.accuracy_m,
    )
    db.add(snapshot)
    await db.flush()

    # Update Redis cache (TTL = 2 minutes). Continue gracefully if Redis is unavailable.
    global _redis_cache_unavailable_logged
    cache_status = "updated"
    try:
        r = await get_redis()
        cache_data = json.dumps({
            "lat": data.lat,
            "lon": data.lon,
            "heading": data.heading,
            "speed_kmh": data.speed_kmh,
            "updated_at": utcnow_naive().isoformat(),
        })
        await r.set(f"courier_location:{courier_id}", cache_data, ex=120)
        if _redis_cache_unavailable_logged:
            logger.info("Courier location cache connection restored.")
            _redis_cache_unavailable_logged = False
    except Exception:
        cache_status = "unavailable"
        if not _redis_cache_unavailable_logged:
            logger.warning("Courier location cache update skipped: Redis unavailable.")
            _redis_cache_unavailable_logged = True

    geofence_status = "enqueued"
    geofence_result = None
    try:
        from app.workers.geofencing_tasks import process_geofence_update

        enqueue_task(
            process_geofence_update,
            args=[str(courier_id), data.lat, data.lon],
        )
    except Exception:
        try:
            from app.services import geofencing_service

            geofence_result = await geofencing_service.process_location_update(
                db,
                courier_id=courier_id,
                lat=data.lat,
                lon=data.lon,
            )
            geofence_status = "processed_inline"
        except Exception:
            geofence_status = "failed"
            logger.exception(
                "Failed to process geofence update [courier_id=%s]",
                courier_id,
            )

    return {
        "status": "ok",
        "cache_status": cache_status,
        "geofence_status": geofence_status,
        "geofence_result": geofence_result,
    }


async def update_push_token(
    db: AsyncSession,
    courier_id: uuid.UUID,
    *,
    push_provider: PushProvider | None,
    push_token: str | None,
    fcm_token: str | None,
) -> Courier:
    courier = await get_courier(db, courier_id)
    resolved_provider, resolved_token, resolved_fcm_token = _resolve_push_registration(
        push_provider=push_provider,
        push_token=push_token,
        fcm_token=fcm_token,
    )
    courier.push_provider = resolved_provider
    courier.push_token = resolved_token
    courier.fcm_token = resolved_fcm_token
    await db.flush()
    await db.refresh(courier)
    return courier


async def update_status(
    db: AsyncSession,
    courier_id: uuid.UUID,
    new_status: AvailabilityStatus,
    *,
    actor_user_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> Courier:
    """Update courier availability status.

    Writes a `CourierAvailabilityHistory` row before mutating the column
    so the audit trail is reconstructible — analytics needs this to
    compute online hours, shift compliance, and attrition warning signals.

    If transitioning to OFFLINE and the courier has active routes,
    enqueues a re-optimization task to abort their routes and
    re-dispatch undelivered orders.
    """
    from app.services.lifecycle_history_service import (
        record_courier_availability_change,
    )

    courier = await get_courier(db, courier_id)
    previous_status = courier.availability_status
    await record_courier_availability_change(
        db,
        courier,
        new_status,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await db.flush()
    await db.refresh(courier)

    # Trigger re-optimization if going offline
    if (
        previous_status == AvailabilityStatus.ONLINE
        and new_status == AvailabilityStatus.OFFLINE
    ):
        try:
            from app.workers.optimization_tasks import run_reoptimization

            enqueue_task(run_reoptimization, args=[str(courier_id), str(courier.tenant_id)])
        except Exception:
            logger.exception(
                "Failed to enqueue re-optimization for offline courier %s",
                courier_id,
            )

    return courier


def _serialize_location_snapshot(snapshot: LocationSnapshot) -> dict:
    return {
        "lat": snapshot.lat,
        "lon": snapshot.lon,
        "heading": snapshot.heading,
        "speed_kmh": snapshot.speed_kmh,
        "updated_at": snapshot.recorded_at.isoformat(),
    }


async def _load_latest_location_from_db(
    db: AsyncSession,
    courier_id: uuid.UUID,
) -> dict | None:
    result = await db.execute(
        select(LocationSnapshot)
        .where(LocationSnapshot.courier_id == courier_id)
        .order_by(LocationSnapshot.recorded_at.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        return None
    return _serialize_location_snapshot(snapshot)


def _deserialize_cached_location(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
    except Exception:
        logger.warning("Courier location cache payload is invalid JSON.")
        return None
    if not isinstance(parsed, dict):
        logger.warning("Courier location cache payload has invalid type.")
        return None
    return parsed


async def get_courier_location_with_source(
    courier_id: uuid.UUID,
    db: AsyncSession | None = None,
) -> tuple[dict | None, str]:
    """Read latest courier location with Redis-first and DB fallback semantics."""
    global _redis_cache_unavailable_logged
    try:
        r = await get_redis()
        raw = await r.get(f"courier_location:{courier_id}")
        if raw:
            parsed = _deserialize_cached_location(raw)
            if parsed is not None:
                if _redis_cache_unavailable_logged:
                    logger.info("Courier location cache connection restored.")
                    _redis_cache_unavailable_logged = False
                return parsed, "redis"
    except Exception:
        if not _redis_cache_unavailable_logged:
            logger.warning("Courier location cache read skipped: Redis unavailable.")
            _redis_cache_unavailable_logged = True

    if db is None:
        return None, "none"

    fallback = await _load_latest_location_from_db(db, courier_id)
    if fallback is None:
        return None, "none"
    return fallback, "db"


async def get_courier_location_from_cache(
    courier_id: uuid.UUID,
    db: AsyncSession | None = None,
) -> dict | None:
    """Read latest courier location from Redis with optional DB fallback."""
    location, _ = await get_courier_location_with_source(courier_id, db=db)
    return location


async def list_latest_locations_for_dispatcher(db: AsyncSession, principal) -> list[dict]:
    """Return latest known courier locations with metadata for dispatcher map."""
    result = await db.execute(select(Courier).where(tenant_filter(Courier, principal)))
    couriers = list(result.scalars().all())

    rows: list[dict] = []
    for courier in couriers:
        location, source = await get_courier_location_with_source(courier.id, db=db)
        if location is None:
            continue
        lat = location.get("lat")
        lon = location.get("lon")
        if lat is None or lon is None:
            continue
        rows.append(
            {
                "courier_id": courier.id,
                "vehicle_type": courier.vehicle_type,
                "availability_status": courier.availability_status,
                "lat": float(lat),
                "lon": float(lon),
                "heading": location.get("heading"),
                "speed_kmh": location.get("speed_kmh"),
                "updated_at": location.get("updated_at"),
                "source": source,
            }
        )

    return rows


async def get_courier_analytics(
    db: AsyncSession,
    courier_id: uuid.UUID,
    principal,
) -> dict:
    await get_courier(db, courier_id, principal)

    total_assigned_q = await db.execute(
        select(func.count(Order.id)).where(
            Order.assigned_courier_id == courier_id,
            tenant_filter(Order, principal),
        )
    )
    delivered_q = await db.execute(
        select(func.count(Order.id)).where(
            Order.assigned_courier_id == courier_id,
            Order.status == OrderStatus.DELIVERED,
            tenant_filter(Order, principal),
        )
    )
    failed_q = await db.execute(
        select(func.count(Order.id)).where(
            Order.assigned_courier_id == courier_id,
            Order.status == OrderStatus.FAILED,
            tenant_filter(Order, principal),
        )
    )
    active_routes_q = await db.execute(
        select(func.count(Route.id)).where(
            Route.courier_id == courier_id,
            Route.status == RouteStatus.ACTIVE,
            tenant_filter(Route, principal),
        )
    )

    return {
        "courier_id": courier_id,
        "total_assigned_orders": int(total_assigned_q.scalar() or 0),
        "delivered_orders": int(delivered_q.scalar() or 0),
        "failed_orders": int(failed_q.scalar() or 0),
        "active_routes": int(active_routes_q.scalar() or 0),
    }
