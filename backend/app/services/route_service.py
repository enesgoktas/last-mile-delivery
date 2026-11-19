import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.algorithms import osrm_client
from app.config import settings
from app.models.courier import Courier
from app.models.enums import (
    DISPATCH_ACCESS_ROLES,
    FailureReason,
    OrderType,
    RouteStatus,
    StopStatus,
    StopType,
)
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.models.user import User
from app.schemas.operations_policy import default_operations_policy
from app.services import operations_policy_service, traffic_profile_service
from app.services.vehicle_routing_profile import resolve_osrm_profile_from_values
from app.utils.compat import UTC
from app.utils.tenant_context import tenant_filter

logger = logging.getLogger(__name__)
_redis: aioredis.Redis | None = None
TERMINAL_STOP_STATUSES = {StopStatus.COMPLETED, StopStatus.FAILED}


def _courier_osrm_profile(courier: Courier) -> str:
    """Return the OSRM profile for a courier, using weight as a proxy when no spec is loaded."""
    spec = getattr(courier, "vehicle_spec", None)
    return resolve_osrm_profile_from_values(
        courier.vehicle_type,
        spec=spec,
        gross_weight_kg=round(courier.max_weight_kg),
    )


def _route_load_options():
    return (selectinload(Route.stops).selectinload(RouteStop.order),)


def _route_stop_load_options():
    return (selectinload(RouteStop.order),)


def get_pod_policy(
    order_type: OrderType,
    operations_policy=None,
) -> dict[str, object]:
    if operations_policy is None:
        operations_policy = default_operations_policy()
    requirements = operations_policy_service.resolve_pod_requirements(
        operations_policy,
        order_type,
    )
    return {
        "photo_required": requirements.require_photo,
        "photo_count": requirements.photo_count,
        "signature_required": requirements.require_signature,
        "otp_required": requirements.require_otp,
        "receiver_name_mode": requirements.receiver_name_mode,
        "notes_mode": requirements.notes_mode,
    }


def resolve_stop_wait_time_minutes(
    *,
    base_wait_time_minutes: int,
    pod_policy: dict[str, object],
) -> int:
    """Derive stop wait time from workflow default + POD risk flags.

    High-assurance proof requirements (signature/OTP/required receiver identity)
    indicate higher handoff risk, so enforce a minimum wait floor.
    """
    high_risk_pod = (
        bool(pod_policy.get("signature_required"))
        or bool(pod_policy.get("otp_required"))
        or pod_policy.get("receiver_name_mode") == "REQUIRED"
    )
    if high_risk_pod:
        return max(base_wait_time_minutes, 8)
    return base_wait_time_minutes


def build_route_stops_for_order(
    *,
    route_id: uuid.UUID,
    order: Order,
    pickup_sequence: int,
    delivery_sequence: int,
    planned_arrival: datetime | None = None,
    operations_policy=None,
) -> tuple[RouteStop, RouteStop]:
    """Create pickup and delivery route stops for an order.

    `planned_arrival` is a placeholder for callers that will immediately
    re-time the route after inserting the stops.

    When ``operations_policy`` is supplied and ``ride_no_show_handling_enabled``
    is on, RIDE pickups are stamped with ``wait_required=True`` and the
    configured ``passenger_no_show_wait_minutes`` so the driver app shows a
    wait timer and a 'no-show' action.
    """
    stop_eta = planned_arrival or datetime.now(UTC).replace(tzinfo=None)

    ride_wait_on = bool(
        operations_policy is not None
        and order.type == OrderType.RIDE
        and operations_policy.feature_flags.ride_no_show_handling_enabled
    )
    pickup_wait_required = ride_wait_on
    pickup_max_wait_minutes = (
        int(operations_policy.ride_settings.passenger_no_show_wait_minutes)
        if ride_wait_on
        else 0
    )

    pickup_stop = RouteStop(
        tenant_id=order.tenant_id,
        route_id=route_id,
        order_id=order.id,
        sequence_number=pickup_sequence,
        stop_type=StopType.PICKUP,
        lat=order.pickup_lat,
        lon=order.pickup_lon,
        address=order.pickup_address,
        planned_arrival=stop_eta,
        status=StopStatus.PENDING,
        wait_required=pickup_wait_required,
        max_wait_minutes=pickup_max_wait_minutes,
    )
    delivery_stop = RouteStop(
        tenant_id=order.tenant_id,
        route_id=route_id,
        order_id=order.id,
        sequence_number=delivery_sequence,
        stop_type=StopType.DELIVERY,
        lat=order.delivery_lat,
        lon=order.delivery_lon,
        address=order.delivery_address,
        planned_arrival=stop_eta,
        status=StopStatus.PENDING,
    )
    return pickup_stop, delivery_stop


def validate_stop_failure_details(
    failure_reason: FailureReason,
    notes: str | None,
    *,
    require_notes_for_other: bool = True,
    custom_reason_label: str | None = None,
    allowed_custom_reason_labels: list[str] | None = None,
) -> None:
    if custom_reason_label and failure_reason != FailureReason.OTHER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="custom_reason_label can only be used with failure_reason OTHER",
        )
    if (
        custom_reason_label
        and allowed_custom_reason_labels is not None
        and custom_reason_label not in allowed_custom_reason_labels
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected custom failure reason is not enabled for this workspace",
        )
    if (
        failure_reason == FailureReason.OTHER
        and require_notes_for_other
        and not custom_reason_label
        and not (notes and notes.strip())
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A note is required when failure_reason is OTHER",
        )


def _build_stop_action_policy_from_operations_policy(
    operations_policy,
    *,
    order: Order | None,
) -> dict | None:
    if order is None:
        return None

    pod_policy = get_pod_policy(order.type, operations_policy)
    return {
        "require_arrival_scan": operations_policy.delivery_workflow.require_arrival_scan,
        "pre_arrival_call": operations_policy.delivery_workflow.pre_arrival_call,
        "wait_time_minutes": resolve_stop_wait_time_minutes(
            base_wait_time_minutes=operations_policy.delivery_workflow.wait_time_minutes,
            pod_policy=pod_policy,
        ),
        "safe_drop_policy": operations_policy.delivery_workflow.safe_drop_policy,
        "proof_of_delivery": {
            "require_photo": bool(pod_policy["photo_required"]),
            "photo_count": int(pod_policy["photo_count"]),
            "require_signature": bool(pod_policy["signature_required"]),
            "require_otp": bool(pod_policy["otp_required"]),
            "receiver_name_mode": pod_policy["receiver_name_mode"],
            "notes_mode": pod_policy["notes_mode"],
        },
        "failure_handling": {
            "allowed_reasons": operations_policy_service.get_failure_reason_options(operations_policy),
            "require_notes_for_other": operations_policy.failure_handling.require_notes_for_other,
            "post_failure_action": operations_policy.failure_handling.post_failure_action,
            "self_service_mode": operations_policy.failure_handling.self_service_mode,
        },
    }


async def _attach_route_action_policies(db: AsyncSession, route: Route) -> Route:
    if not route.stops:
        return route

    operations_policy = await operations_policy_service.get_operations_policy(db, route.tenant_id)
    order_ids = [stop.order_id for stop in route.stops if stop.order_id is not None]
    orders_by_id: dict[uuid.UUID, Order] = {}
    if order_ids:
        order_result = await db.execute(
            select(Order).where(
                Order.id.in_(order_ids),
                tenant_filter(Order, route.tenant_id),
            )
        )
        orders_by_id = {order.id: order for order in order_result.scalars().all()}

    for stop in route.stops:
        order = orders_by_id.get(stop.order_id) if stop.order_id is not None else None
        stop.order = order
        stop.action_policy = _build_stop_action_policy_from_operations_policy(
            operations_policy,
            order=order,
        )
    return route


async def _attach_stop_action_policy(
    db: AsyncSession,
    stop: RouteStop,
    *,
    tenant_id,
) -> RouteStop:
    if stop.order_id is None:
        stop.action_policy = None
        return stop

    order_result = await db.execute(
        select(Order).where(
            Order.id == stop.order_id,
            tenant_filter(Order, tenant_id),
        )
    )
    order = order_result.scalar_one_or_none()
    stop.order = order
    operations_policy = await operations_policy_service.get_operations_policy(db, tenant_id)
    stop.action_policy = _build_stop_action_policy_from_operations_policy(
        operations_policy,
        order=order,
    )
    return stop


async def _assert_route_access(
    db: AsyncSession,
    route: Route,
    current_user: User,
) -> None:
    if current_user.role in DISPATCH_ACCESS_ROLES:
        return
    if current_user.role.value != "COURIER":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    result = await db.execute(
        select(Courier).where(
            Courier.user_id == current_user.id,
            tenant_filter(Courier, current_user),
        )
    )
    courier = result.scalar_one_or_none()
    if not courier or courier.id != route.courier_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Couriers can only access their own routes",
        )


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


def _build_route_geometry_cache_key(route: Route) -> str:
    payload = {
        "route_id": str(route.id),
        "status": route.status.value,
        "stops": [
            {
                "id": str(stop.id),
                "seq": stop.sequence_number,
                "status": stop.status.value,
                "actual_arrival": stop.actual_arrival.isoformat() if stop.actual_arrival else None,
                "lat": round(stop.lat, 6),
                "lon": round(stop.lon, 6),
            }
            for stop in sorted(route.stops, key=lambda s: s.sequence_number)
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"route_geometry:{route.id}:{digest}"


async def _cache_route_geometry(cache_key: str, payload: dict) -> None:
    try:
        redis_client = await _get_redis()
        await redis_client.set(
            cache_key,
            json.dumps(payload, default=str),
            ex=settings.ROUTE_GEOMETRY_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning("Route geometry cache write failed: %s", exc)


async def _record_traffic_observation_for_arrival(
    db: AsyncSession,
    *,
    route_id: uuid.UUID,
    stop: RouteStop,
) -> None:
    if stop.actual_arrival is None:
        return

    prev_result = await db.execute(
        select(RouteStop)
        .where(
            RouteStop.route_id == route_id,
            RouteStop.sequence_number < stop.sequence_number,
            tenant_filter(RouteStop, stop.tenant_id),
        )
        .order_by(RouteStop.sequence_number.desc())
        .limit(1)
    )
    prev_stop = prev_result.scalar_one_or_none()
    if prev_stop is None or prev_stop.actual_arrival is None:
        return

    observed_s = int((stop.actual_arrival - prev_stop.actual_arrival).total_seconds())
    planned_s = int((stop.planned_arrival - prev_stop.planned_arrival).total_seconds())
    if observed_s <= 0 or planned_s <= 0:
        return

    await traffic_profile_service.record_observation(
        db,
        lat=stop.lat,
        lon=stop.lon,
        observed_travel_s=observed_s,
        planned_travel_s=planned_s,
        observed_at=stop.actual_arrival,
    )


async def _resolve_retime_anchor(
    db: AsyncSession,
    *,
    route: Route,
    actionable_stops: list[RouteStop],
) -> tuple[tuple[float, float], datetime]:
    now = datetime.now(UTC).replace(tzinfo=None)
    if route.courier is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Route courier not found",
        )

    if route.status == RouteStatus.ACTIVE:
        from app.services.courier_service import get_courier_location_from_cache

        location = await get_courier_location_from_cache(route.courier_id, db=db)
        if location and location.get("lat") is not None and location.get("lon") is not None:
            return (float(location["lat"]), float(location["lon"])), now

    terminal_stops = sorted(
        [stop for stop in route.stops if stop.status in TERMINAL_STOP_STATUSES],
        key=lambda stop: stop.sequence_number,
    )
    if terminal_stops:
        last_terminal = terminal_stops[-1]
        anchor_time = last_terminal.actual_arrival or last_terminal.planned_arrival or now
        if route.status == RouteStatus.ACTIVE:
            anchor_time = max(anchor_time, now)
        return (last_terminal.lat, last_terminal.lon), anchor_time

    depot_origin = (route.courier.depot_lat, route.courier.depot_lon)
    if route.status == RouteStatus.PLANNED and actionable_stops:
        first_stop = actionable_stops[0]
        try:
            leg = await osrm_client.get_route(
                depot_origin,
                (first_stop.lat, first_stop.lon),
                profile=_courier_osrm_profile(route.courier),
            )
            anchor_time = first_stop.planned_arrival - timedelta(seconds=leg.duration_s)
        except Exception:
            anchor_time = route.created_at
        if anchor_time < now and first_stop.planned_arrival <= now:
            anchor_time = now
        return depot_origin, anchor_time

    anchor_time = route.started_at or route.created_at or now
    if route.status == RouteStatus.ACTIVE:
        anchor_time = max(anchor_time, now)
    return depot_origin, anchor_time


async def retime_route_stops(
    db: AsyncSession,
    route_id: uuid.UUID,
) -> list[RouteStop]:
    """Recompute planned arrivals for all non-terminal stops on a route."""
    result = await db.execute(
        select(Route)
        .where(Route.id == route_id)
        .options(selectinload(Route.stops), selectinload(Route.courier))
    )
    route = result.scalar_one_or_none()
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found")

    actionable_stops = sorted(
        [stop for stop in route.stops if stop.status not in TERMINAL_STOP_STATUSES],
        key=lambda stop: stop.sequence_number,
    )
    if not actionable_stops:
        return sorted(route.stops, key=lambda stop: stop.sequence_number)

    origin, anchor_time = await _resolve_retime_anchor(
        db,
        route=route,
        actionable_stops=actionable_stops,
    )
    coords = [origin, *[(stop.lat, stop.lon) for stop in actionable_stops]]
    matrix = await osrm_client.get_distance_matrix(
        coords, profile=_courier_osrm_profile(route.courier)
    )

    durations = matrix.durations
    try:
        adjusted = await traffic_profile_service.adjust_duration_matrix(
            db,
            coords=coords,
            durations=matrix.durations,
            at_time=anchor_time,
        )
        durations = adjusted.durations
    except Exception as exc:
        logger.warning("Route re-timing traffic adjustment failed: %s", exc)

    from app.services.constraint_service import get_global_constraints

    global_constraints = await get_global_constraints(db, route.tenant_id)
    service_time_s = global_constraints["service_time_minutes"] * 60

    current_time = anchor_time
    total_distance_m = 0
    for index, stop in enumerate(actionable_stops, start=1):
        travel_s = int(durations[index - 1][index])
        distance_m = int(matrix.distances[index - 1][index])
        current_time = current_time + timedelta(seconds=travel_s)
        stop.planned_arrival = current_time
        current_time = current_time + timedelta(seconds=service_time_s)
        total_distance_m += distance_m

    route.planned_distance_km = round(total_distance_m / 1000.0, 2)
    await db.flush()
    return sorted(route.stops, key=lambda stop: stop.sequence_number)


async def get_route(db: AsyncSession, route_id: uuid.UUID, current_user: User | None = None) -> Route:
    query = select(Route).where(Route.id == route_id).options(*_route_load_options())
    if current_user is not None:
        query = query.where(tenant_filter(Route, current_user))
    result = await db.execute(query)
    route = result.scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found")
    if current_user is not None:
        await _assert_route_access(db, route, current_user)
    return await _attach_route_action_policies(db, route)


def _route_has_stop_on_date(route_date: date):
    route_start = datetime(route_date.year, route_date.month, route_date.day)
    route_end = route_start + timedelta(days=1)
    return (
        select(RouteStop.id)
        .where(
            RouteStop.route_id == Route.id,
            RouteStop.planned_arrival >= route_start,
            RouteStop.planned_arrival < route_end,
        )
        .exists()
    )


async def count_routes(
    db: AsyncSession,
    principal,
    courier_id: uuid.UUID | None = None,
    statuses: list | None = None,
    created_after: datetime | None = None,
    route_date: date | None = None,
) -> int:
    query = select(func.count()).select_from(Route).where(tenant_filter(Route, principal))
    if courier_id:
        query = query.where(Route.courier_id == courier_id)
    if statuses:
        query = query.where(Route.status.in_(statuses))
    if created_after:
        naive = created_after.replace(tzinfo=None) if created_after.tzinfo else created_after
        query = query.where(Route.created_at >= naive)
    if route_date:
        query = query.where(_route_has_stop_on_date(route_date))
    result = await db.execute(query)
    return int(result.scalar_one())


async def list_routes(
    db: AsyncSession,
    principal,
    courier_id: uuid.UUID | None = None,
    statuses: list | None = None,
    created_after: datetime | None = None,
    route_date: date | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[Route]:
    query = select(Route).where(tenant_filter(Route, principal)).options(*_route_load_options())
    if courier_id:
        query = query.where(Route.courier_id == courier_id)
    if statuses:
        query = query.where(Route.status.in_(statuses))
    if created_after:
        naive = created_after.replace(tzinfo=None) if created_after.tzinfo else created_after
        query = query.where(Route.created_at >= naive)
    if route_date:
        query = query.where(_route_has_stop_on_date(route_date))
    query = query.order_by(Route.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    routes = list(result.scalars().unique().all())
    for route in routes:
        await _attach_route_action_policies(db, route)
    return routes


async def get_active_route_for_courier(
    db: AsyncSession,
    courier_id: uuid.UUID,
    principal=None,
) -> Route | None:
    query = (
        select(Route)
        .where(
            Route.courier_id == courier_id,
            Route.status.in_([RouteStatus.PLANNED, RouteStatus.ACTIVE]),
        )
        .options(*_route_load_options())
        .order_by(Route.created_at.desc())
        .limit(1)
    )
    if principal is not None:
        query = query.where(tenant_filter(Route, principal))
    result = await db.execute(query)
    route = result.scalar_one_or_none()
    if route is None:
        return None
    return await _attach_route_action_policies(db, route)


async def get_next_stop_for_courier(
    db: AsyncSession,
    courier_id: uuid.UUID,
    principal=None,
) -> RouteStop | None:
    route = await get_active_route_for_courier(db, courier_id, principal)
    if not route:
        return None
    candidates = [
        s for s in sorted(route.stops, key=lambda st: st.sequence_number)
        if s.status in (StopStatus.PENDING, StopStatus.ARRIVED)
    ]
    if not candidates:
        return None
    return await _attach_stop_action_policy(db, candidates[0], tenant_id=route.tenant_id)


async def reorder_stops(
    db: AsyncSession,
    route_id: uuid.UUID,
    stop_ids: list[uuid.UUID],
    principal=None,
) -> list[RouteStop]:
    """Reorder stops on a route. Validates pickup-before-delivery constraints."""
    query = select(Route).where(Route.id == route_id).options(*_route_load_options())
    if principal is not None:
        query = query.where(tenant_filter(Route, principal))
    result = await db.execute(query)
    route = result.scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found")
    if principal is not None:
        await operations_policy_service.ensure_permission(db, principal, "reassign_order")

    if route.status not in (RouteStatus.PLANNED, RouteStatus.ACTIVE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reorder stops on a {route.status.value} route",
        )

    stop_by_id = {s.id: s for s in route.stops}
    if set(stop_ids) != set(stop_by_id.keys()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stop_ids must contain exactly all stops of the route",
        )

    new_order = [stop_by_id[sid] for sid in stop_ids]

    # All completed/failed stops must come before all non-completed stops
    seen_pending = False
    for stop in new_order:
        if stop.status not in (StopStatus.COMPLETED, StopStatus.FAILED):
            seen_pending = True
        elif seen_pending:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Completed or failed stops cannot be placed after pending stops",
            )

    # Validate pickup comes before delivery for each order (skip depot stops)
    order_pickup_pos: dict[uuid.UUID, int] = {}
    order_delivery_pos: dict[uuid.UUID, int] = {}
    for i, stop in enumerate(new_order):
        if stop.order_id is None:
            continue
        if stop.stop_type == StopType.PICKUP:
            order_pickup_pos[stop.order_id] = i
        elif stop.stop_type == StopType.DELIVERY:
            order_delivery_pos[stop.order_id] = i

    for order_id, pickup_pos in order_pickup_pos.items():
        delivery_pos = order_delivery_pos.get(order_id)
        if delivery_pos is not None and pickup_pos >= delivery_pos:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Pickup must come before delivery for order {str(order_id)[:8]}",
            )

    for i, stop in enumerate(new_order):
        stop.sequence_number = i

    await db.flush()
    retimed = await retime_route_stops(db, route_id)
    return [await _attach_stop_action_policy(db, stop, tenant_id=route.tenant_id) for stop in retimed]


async def set_dispatcher_note(
    db: AsyncSession,
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    dispatcher_note: str | None,
    principal=None,
) -> RouteStop:
    """Set or clear a dispatcher note on a route stop."""
    query = select(RouteStop).where(
        RouteStop.id == stop_id,
        RouteStop.route_id == route_id,
    ).options(*_route_stop_load_options())
    if principal is not None:
        query = query.where(tenant_filter(RouteStop, principal))
    result = await db.execute(query)
    stop = result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stop not found")
    if principal is not None:
        await operations_policy_service.ensure_permission(db, principal, "edit_order")
    stop.dispatcher_note = dispatcher_note
    await db.flush()
    return await _attach_stop_action_policy(db, stop, tenant_id=stop.tenant_id)


async def get_route_geometry(
    db: AsyncSession,
    route_id: uuid.UUID,
    principal=None,
) -> dict:
    query = (
        select(Route)
        .where(Route.id == route_id)
        .options(selectinload(Route.stops), selectinload(Route.courier))
    )
    if principal is not None:
        query = query.where(tenant_filter(Route, principal))
    result = await db.execute(query)
    route = result.scalar_one_or_none()
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found")
    if route.courier is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Route courier not found")

    cache_key = _build_route_geometry_cache_key(route)
    try:
        redis_client = await _get_redis()
        cached = await redis_client.get(cache_key)
        if cached:
            payload = json.loads(cached)
            if isinstance(payload, dict):
                payload["source"] = "cache"
                return payload
    except Exception as exc:
        logger.warning("Route geometry cache read failed: %s", exc)

    ordered_stops = sorted(route.stops, key=lambda s: s.sequence_number)
    if not ordered_stops:
        payload = {
            "route_id": route.id,
            "status": route.status,
            "source": "generated",
            "generated_at": datetime.now(UTC).replace(tzinfo=None),
            "geometry": {"type": "FeatureCollection", "features": []},
        }
        await _cache_route_geometry(cache_key, payload)
        return payload

    waypoints: list[dict] = [
        {
            "kind": "depot",
            "lat": route.courier.depot_lat,
            "lon": route.courier.depot_lon,
            "stop_id": None,
            "sequence": -1,
        }
    ]
    for stop in ordered_stops:
        waypoints.append(
            {
                "kind": stop.stop_type.value.lower(),
                "lat": stop.lat,
                "lon": stop.lon,
                "stop_id": str(stop.id),
                "sequence": stop.sequence_number,
            }
        )

    full_coords: list[list[float]] = []
    leg_features: list[dict] = []
    total_distance_m = 0
    total_duration_s = 0
    route_profile = _courier_osrm_profile(route.courier)
    for index in range(len(waypoints) - 1):
        origin = waypoints[index]
        destination = waypoints[index + 1]
        leg = await osrm_client.get_route_geojson(
            (origin["lat"], origin["lon"]),
            (destination["lat"], destination["lon"]),
            profile=route_profile,
        )
        coordinates = leg.coordinates or [
            [origin["lon"], origin["lat"]],
            [destination["lon"], destination["lat"]],
        ]
        if full_coords:
            if full_coords[-1] == coordinates[0]:
                full_coords.extend(coordinates[1:])
            else:
                full_coords.extend(coordinates)
        else:
            full_coords.extend(coordinates)

        total_distance_m += int(leg.distance_m)
        total_duration_s += int(leg.duration_s)
        leg_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "properties": {
                    "kind": "leg",
                    "leg_index": index,
                    "from_kind": origin["kind"],
                    "to_kind": destination["kind"],
                    "from_stop_id": origin["stop_id"],
                    "to_stop_id": destination["stop_id"],
                    "to_sequence": destination["sequence"],
                    "distance_m": int(leg.distance_m),
                    "duration_s": int(leg.duration_s),
                },
            }
        )

    full_feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": full_coords},
        "properties": {
            "kind": "full_route",
            "distance_m": total_distance_m,
            "duration_s": total_duration_s,
            "stop_count": len(ordered_stops),
        },
    }

    payload = {
        "route_id": route.id,
        "status": route.status,
        "source": "generated",
        "generated_at": datetime.now(UTC).replace(tzinfo=None),
        "geometry": {
            "type": "FeatureCollection",
            "features": [full_feature, *leg_features],
        },
    }
    await _cache_route_geometry(cache_key, payload)
    return payload
