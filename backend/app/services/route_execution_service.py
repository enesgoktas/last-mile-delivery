import logging
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.courier import Courier
from app.models.enums import (
    FailedOrderCustomerResponseStatus,
    FailureReason,
    InventoryEventType,
    OrderStatus,
    OrderType,
    PackageStatus,
    RouteStatus,
    StopStatus,
    StopType,
)
from app.models.notification import ProofOfDelivery
from app.models.order import Order
from app.models.package import Package
from app.models.route import Route, RouteStop
from app.models.user import User
from app.services import operations_policy_service
from app.services.lifecycle_history_service import (
    apply_route_status_transition,
    apply_stop_status_transition,
)
from app.services.order_service import apply_order_status_transition

# imports from route_service
from app.services.route_service import (
    _record_traffic_observation_for_arrival,
    _route_stop_load_options,
    get_pod_policy,
    get_route,
    validate_stop_failure_details,
)
from app.utils.compat import UTC
from app.utils.s3 import generate_presigned_upload_url
from app.utils.tenant_context import tenant_filter

logger = logging.getLogger(__name__)

async def _ensure_order_in_transit_for_delivery(
    db: AsyncSession,
    *,
    order: Order,
    current_user: User,
    reason: str,
) -> None:
    """Ensure an order is in IN_TRANSIT when operating on a delivery stop.

    Some demo/dispatch flows can collapse initial preload PICKUP stops, leaving an
    order in ASSIGNED even though a delivery stop is actionable. In that case we
    advance the order ASSIGNED → PICKED_UP → IN_TRANSIT to keep the state machine
    consistent for delivery completion/failure.
    """
    if order.status == OrderStatus.ASSIGNED:
        await apply_order_status_transition(
            db,
            order,
            OrderStatus.PICKED_UP,
            actor_user_id=current_user.id,
            reason=f"{reason} (implicit pickup)",
        )
    if order.status == OrderStatus.PICKED_UP:
        await apply_order_status_transition(
            db,
            order,
            OrderStatus.IN_TRANSIT,
            actor_user_id=current_user.id,
            reason=reason,
        )

async def _record_delivery_inventory_event(
    db: AsyncSession,
    order: Order,
    route_id: uuid.UUID,
) -> None:
    """Record the appropriate inventory event when an order reaches DELIVERED status.

    RETURN orders emit RETURN_RECEIVED (package is back at the depot); all other
    order types emit DELIVERED_TO_CUSTOMER.
    """
    from app.services.inventory_service import record_event

    result = await db.execute(
        select(Package).where(
            Package.order_id == order.id,
            tenant_filter(Package, order.tenant_id),
        )
    )
    packages = list(result.scalars().all())
    route_result = await db.execute(
        select(Route).where(
            Route.id == route_id,
            tenant_filter(Route, order.tenant_id),
        )
    )
    route = route_result.scalar_one_or_none()
    courier_id = route.courier_id if route else None

    event_type = (
        InventoryEventType.RETURN_RECEIVED
        if order.type == OrderType.RETURN
        else InventoryEventType.DELIVERED_TO_CUSTOMER
    )
    for pkg in packages:
        await record_event(
            db,
            package_id=pkg.id,
            event_type=event_type,
            courier_id=courier_id,
        )


async def _record_pickup_inventory_event(
    db: AsyncSession,
    order: Order,
    route_id: uuid.UUID,
) -> None:
    """Record inventory movement when a visible pickup stop is completed."""
    from app.services.inventory_service import record_event

    route_result = await db.execute(
        select(Route).where(
            Route.id == route_id,
            tenant_filter(Route, order.tenant_id),
        )
    )
    route = route_result.scalar_one_or_none()
    courier_id = route.courier_id if route else None

    if order.type != OrderType.REVERSE:
        if courier_id is None:
            return
        depot_id = await _get_courier_depot_id(
            db,
            tenant_id=order.tenant_id,
            courier_id=courier_id,
        )
        await _record_load_events_for_order_ids(
            db,
            tenant_id=order.tenant_id,
            courier_id=courier_id,
            depot_id=depot_id,
            order_ids=[order.id],
        )
        return

    result = await db.execute(
        select(Package).where(
            Package.order_id == order.id,
            tenant_filter(Package, order.tenant_id),
        )
    )
    packages = list(result.scalars().all())

    for pkg in packages:
        await record_event(
            db,
            package_id=pkg.id,
            event_type=InventoryEventType.PICKED_UP_FROM_CUSTOMER,
            courier_id=courier_id,
        )


def _collect_trip_load_order_ids(
    stops: list[RouteStop],
    *,
    after_sequence: int | None,
) -> list[uuid.UUID]:
    order_ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for stop in sorted(stops, key=lambda s: s.sequence_number):
        if after_sequence is not None and stop.sequence_number <= after_sequence:
            continue
        if stop.stop_type in (StopType.DEPOT_RETURN, StopType.DEPOT_RELOAD):
            break
        if stop.order_id is None or stop.order_id in seen:
            continue
        if stop.stop_type in (StopType.PICKUP, StopType.DELIVERY):
            seen.add(stop.order_id)
            order_ids.append(stop.order_id)
    return order_ids


async def _get_courier_depot_id(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    courier_id: uuid.UUID,
) -> uuid.UUID | None:
    courier_result = await db.execute(
        select(Courier).where(
            Courier.id == courier_id,
            tenant_filter(Courier, tenant_id),
        )
    )
    courier = courier_result.scalar_one_or_none()
    return courier.depot_id if courier else None


async def _record_load_events_for_order_ids(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    courier_id: uuid.UUID,
    depot_id: uuid.UUID | None,
    order_ids: list[uuid.UUID],
) -> None:
    """Load AT_DEPOT packages for outbound orders, without double-loading."""
    if not order_ids:
        return

    from app.services.inventory_service import record_event

    conditions = [
        Package.order_id.in_(order_ids),
        Package.status == PackageStatus.AT_DEPOT,
        Order.type != OrderType.REVERSE,
        tenant_filter(Package, tenant_id),
        tenant_filter(Order, tenant_id),
    ]
    if depot_id is not None:
        conditions.append(or_(Order.depot_id.is_(None), Order.depot_id == depot_id))

    result = await db.execute(
        select(Package)
        .join(Order, Package.order_id == Order.id)
        .where(*conditions)
    )
    packages = list(result.scalars().all())
    for pkg in packages:
        await record_event(
            db,
            package_id=pkg.id,
            event_type=InventoryEventType.LOADED_ONTO_VEHICLE,
            courier_id=courier_id,
            depot_id=depot_id,
        )


async def _record_charge_completion(
    db: AsyncSession,
    route_id: uuid.UUID,
    charging_stop: RouteStop,
) -> None:
    """When a CHARGING stop completes, bump the courier's SoC to the
    planning target so subsequent dispatch decisions see the fresh tank.

    Uses ``operations_policy.ev_settings.charge_target_pct_for_planning``
    as the assumed post-charge SoC — the same value the dispatch
    charging-post-processor used when it inserted the stop. We refuse to
    *lower* the recorded SoC: if the driver app reported a higher reading
    just before plugging in (unlikely but possible), keep that value.
    """
    route_result = await db.execute(
        select(Route).where(
            Route.id == route_id,
            tenant_filter(Route, charging_stop.tenant_id),
        )
    )
    route = route_result.scalar_one_or_none()
    if route is None or route.courier_id is None:
        return

    courier_result = await db.execute(
        select(Courier).where(
            Courier.id == route.courier_id,
            tenant_filter(Courier, charging_stop.tenant_id),
        ).with_for_update()
    )
    courier = courier_result.scalar_one_or_none()
    if courier is None:
        return

    try:
        policy = await operations_policy_service.get_operations_policy(
            db, charging_stop.tenant_id,
        )
        target_pct = int(policy.ev_settings.charge_target_pct_for_planning)
    except Exception:
        target_pct = 80

    current = int(courier.current_battery_pct or 0)
    if target_pct > current:
        courier.current_battery_pct = max(0, min(100, target_pct))
        await db.flush()


async def _record_reload_events(
    db: AsyncSession,
    route_id: uuid.UUID,
    depot_reload_stop: RouteStop,
) -> None:
    """When a DEPOT_RELOAD stop is completed, load packages for the next trip."""
    route_result = await db.execute(
        select(Route)
        .where(
            Route.id == route_id,
            tenant_filter(Route, depot_reload_stop.tenant_id),
        )
        .options(selectinload(Route.stops))
    )
    route = route_result.scalar_one_or_none()
    if not route:
        return

    courier_id = route.courier_id
    depot_id = await _get_courier_depot_id(
        db,
        tenant_id=route.tenant_id,
        courier_id=courier_id,
    )

    sorted_stops = sorted(route.stops, key=lambda s: s.sequence_number)
    next_trip_order_ids = _collect_trip_load_order_ids(
        sorted_stops,
        after_sequence=depot_reload_stop.sequence_number,
    )
    await _record_load_events_for_order_ids(
        db,
        tenant_id=route.tenant_id,
        courier_id=courier_id,
        depot_id=depot_id,
        order_ids=next_trip_order_ids,
    )


async def _ensure_depot_return_stop(
    db: AsyncSession,
    *,
    route_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> None:
    route_result = await db.execute(
        select(Route)
        .where(
            Route.id == route_id,
            tenant_filter(Route, tenant_id),
        )
        .options(selectinload(Route.stops), selectinload(Route.courier))
    )
    route = route_result.scalar_one_or_none()
    if route is None or route.courier is None:
        return

    pending_return = next(
        (
            stop for stop in route.stops
            if stop.stop_type == StopType.DEPOT_RETURN and stop.status in (StopStatus.PENDING, StopStatus.ARRIVED)
        ),
        None,
    )
    if pending_return is not None:
        return

    next_sequence = max((stop.sequence_number for stop in route.stops), default=-1) + 1
    depot_stop = RouteStop(
        tenant_id=tenant_id,
        route_id=route_id,
        order_id=None,
        sequence_number=next_sequence,
        stop_type=StopType.DEPOT_RETURN,
        lat=route.courier.depot_lat,
        lon=route.courier.depot_lon,
        address="Depot return",
        planned_arrival=datetime.now(UTC).replace(tzinfo=None),
        status=StopStatus.PENDING,
    )
    db.add(depot_stop)
    await db.flush()


async def _record_load_events_for_route_start(
    db: AsyncSession,
    route: Route,
    courier_id: uuid.UUID,
) -> None:
    """Record LOADED_ONTO_VEHICLE events for packages in the first trip of a route."""
    first_trip_order_ids = _collect_trip_load_order_ids(
        list(route.stops),
        after_sequence=None,
    )
    depot_id = await _get_courier_depot_id(
        db,
        tenant_id=route.tenant_id,
        courier_id=courier_id,
    )
    await _record_load_events_for_order_ids(
        db,
        tenant_id=route.tenant_id,
        courier_id=courier_id,
        depot_id=depot_id,
        order_ids=first_trip_order_ids,
    )


async def _maybe_complete_route(db: AsyncSession, route_id: uuid.UUID) -> None:
    """If every stop on the route is COMPLETED or FAILED, mark the route COMPLETED."""
    result = await db.execute(
        select(Route).where(Route.id == route_id).options(selectinload(Route.stops))
    )
    route = result.scalar_one_or_none()
    if not route or route.status != RouteStatus.ACTIVE:
        return
    terminal = {StopStatus.COMPLETED, StopStatus.FAILED}
    if all(s.status in terminal for s in route.stops):
        await apply_route_status_transition(
            db,
            route,
            RouteStatus.COMPLETED,
            reason="All stops terminal — route auto-completed",
        )
        route.completed_at = datetime.now(UTC).replace(tzinfo=None)
        await db.flush()
        logger.info("Route %s auto-completed: all %d stops are terminal", route_id, len(route.stops))

        # Materialize the per-route performance snapshot so the cost-
        # savings dashboard has fresh data the moment the route closes.
        # Non-fatal: a snapshot failure must never block route completion.
        try:
            from app.services.route_performance_snapshot_service import (
                materialize_route_snapshot,
            )

            await materialize_route_snapshot(db, route_id)
        except Exception:
            logger.exception(
                "Failed to materialize route_performance_snapshot for %s",
                route_id,
            )


async def start_route(db: AsyncSession, route_id: uuid.UUID, current_user: User) -> Route:
    route = await get_route(db, route_id, current_user)
    if route.status == RouteStatus.ACTIVE:
        return route
    if route.status != RouteStatus.PLANNED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Route must be PLANNED to start. Current: {route.status.value}",
        )
    await apply_route_status_transition(
        db,
        route,
        RouteStatus.ACTIVE,
        actor_user_id=current_user.id,
        reason="Courier started route",
    )
    route.started_at = datetime.now(UTC).replace(tzinfo=None)
    await db.flush()

    try:
        await _record_load_events_for_route_start(db, route, route.courier_id)
    except Exception as exc:
        logger.warning("Failed to record load events for route start: %s", exc)

    await db.refresh(route)
    return route


async def _get_stop(
    db: AsyncSession,
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    current_user: User,
    *,
    for_update: bool = False,
    require_active_route: bool = False,
) -> RouteStop:
    route = await get_route(db, route_id, current_user)
    if require_active_route and route.status != RouteStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Route must be ACTIVE before updating stops. Current: {route.status.value}",
        )
    query = select(RouteStop).where(
        RouteStop.id == stop_id,
        RouteStop.route_id == route_id,
        tenant_filter(RouteStop, route.tenant_id),
    ).options(*_route_stop_load_options())
    if for_update:
        query = query.with_for_update()

    result = await db.execute(query)
    stop = result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route stop not found")
    return stop


async def _assert_prior_stops_done(
    db: AsyncSession,
    route_id: uuid.UUID,
    stop: RouteStop,
) -> None:
    """Ensure all stops earlier in the sequence are in a terminal state."""
    result = await db.execute(
        select(RouteStop).where(
            RouteStop.route_id == route_id,
            RouteStop.sequence_number < stop.sequence_number,
            RouteStop.status.notin_([StopStatus.COMPLETED, StopStatus.FAILED]),
            tenant_filter(RouteStop, stop.tenant_id),
        ).limit(1)
    )
    blocking = result.scalar_one_or_none()
    if blocking:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Previous stop (sequence {blocking.sequence_number}) "
                f"must be completed or failed first"
            ),
        )


async def create_pod_upload_url(
    db: AsyncSession,
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    current_user: User,
    *,
    file_type: str,
    content_type: str,
) -> dict:
    stop = await _get_stop(db, route_id, stop_id, current_user, require_active_route=True)
    if stop.stop_type != StopType.DELIVERY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="POD upload URL is only available for delivery stops",
        )

    object_key = f"pod/{stop.order_id}/{stop.id}/{file_type}-{uuid.uuid4().hex}"
    upload_url, file_url, expires_at = await generate_presigned_upload_url(
        object_key=object_key,
        content_type=content_type,
    )
    return {
        "upload_url": upload_url,
        "file_url": file_url,
        "object_key": object_key,
        "expires_at": expires_at,
    }


async def _upsert_proof_of_delivery(
    db: AsyncSession,
    *,
    order: Order,
    stop: RouteStop,
    photo_urls: list[str] | None,
    photo_url: str | None,
    signature_url: str | None,
    receiver_name: str | None,
    otp_code: str | None,
    notes: str | None,
) -> ProofOfDelivery:
    result = await db.execute(
        select(ProofOfDelivery).where(
            ProofOfDelivery.route_stop_id == stop.id,
            tenant_filter(ProofOfDelivery, order.tenant_id),
        )
    )
    pod = result.scalar_one_or_none()
    if pod is None:
        pod = ProofOfDelivery(
            tenant_id=order.tenant_id,
            order_id=order.id,
            route_stop_id=stop.id,
            photo_url=photo_url,
            photo_urls=photo_urls,
            signature_url=signature_url,
            receiver_name=receiver_name,
            otp_code=otp_code,
            notes=notes,
        )
        db.add(pod)
    else:
        if photo_urls is not None:
            pod.photo_urls = photo_urls
        if photo_url is not None:
            pod.photo_url = photo_url
        if signature_url is not None:
            pod.signature_url = signature_url
        if receiver_name is not None:
            pod.receiver_name = receiver_name
        if otp_code is not None:
            pod.otp_code = otp_code
        if notes is not None:
            pod.notes = notes
        pod.captured_at = datetime.now(UTC).replace(tzinfo=None)
    await db.flush()
    return pod


async def arrive_at_stop(
    db: AsyncSession,
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    current_user: User,
) -> RouteStop:
    stop = await _get_stop(
        db,
        route_id,
        stop_id,
        current_user,
        for_update=True,
        require_active_route=True,
    )
    if stop.status == StopStatus.ARRIVED:
        return stop
    if stop.status != StopStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stop must be PENDING to arrive. Current: {stop.status.value}",
        )
    await _assert_prior_stops_done(db, route_id, stop)
    await apply_stop_status_transition(
        db,
        stop,
        StopStatus.ARRIVED,
        actor_user_id=current_user.id,
        reason="Courier arrived at stop",
        lat=stop.lat,
        lon=stop.lon,
    )
    stop.actual_arrival = datetime.now(UTC).replace(tzinfo=None)
    await db.flush()

    if stop.stop_type in (StopType.DEPOT_RETURN, StopType.DEPOT_RELOAD) or stop.order_id is None:
        await db.refresh(stop)
        return stop

    result = await db.execute(
        select(Order).where(
            Order.id == stop.order_id,
            tenant_filter(Order, stop.tenant_id),
        )
    )
    order = result.scalar_one_or_none()
    if order and stop.stop_type == StopType.DELIVERY:
        await _ensure_order_in_transit_for_delivery(
            db,
            order=order,
            current_user=current_user,
            reason="Courier arrived at delivery stop",
        )
    try:
        await _record_traffic_observation_for_arrival(
            db,
            route_id=route_id,
            stop=stop,
        )
    except Exception as exc:
        logger.warning(
            "Traffic profile observation skipped [route_id=%s stop_id=%s]: %s",
            route_id,
            stop_id,
            exc,
        )

    # Check if arrival is significantly late → trigger re-optimization
    try:
        from app.services.reoptimization_triggers import on_late_stop_completion
        await on_late_stop_completion(db, route_id, stop)
    except Exception as exc:
        logger.warning(
            "Late-stop re-optimization trigger failed [route_id=%s stop_id=%s]: %s",
            route_id, stop_id, exc,
        )

    await db.refresh(stop)
    return stop


async def complete_stop(
    db: AsyncSession,
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    current_user: User,
    *,
    photo_urls: list[str] | None = None,
    photo_url: str | None = None,
    signature_url: str | None = None,
    receiver_name: str | None = None,
    otp_code: str | None = None,
    pod_notes: str | None = None,
) -> RouteStop:
    stop = await _get_stop(
        db,
        route_id,
        stop_id,
        current_user,
        for_update=True,
        require_active_route=True,
    )
    if stop.status == StopStatus.COMPLETED:
        return stop

    if stop.stop_type in (StopType.DEPOT_RETURN, StopType.DEPOT_RELOAD, StopType.CHARGING) or stop.order_id is None:
        if stop.status != StopStatus.ARRIVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Stop must be ARRIVED to complete. Current: {stop.status.value}",
            )
        await _assert_prior_stops_done(db, route_id, stop)
        await apply_stop_status_transition(
            db,
            stop,
            StopStatus.COMPLETED,
            actor_user_id=current_user.id,
            reason=f"{stop.stop_type.value} stop completed",
            lat=stop.lat,
            lon=stop.lon,
        )
        await db.flush()
        if stop.stop_type == StopType.DEPOT_RELOAD:
            try:
                await _record_reload_events(db, route_id, stop)
            except Exception as exc:
                logger.warning("Failed to record reload events: %s", exc)
        elif stop.stop_type == StopType.CHARGING:
            try:
                await _record_charge_completion(db, route_id, stop)
            except Exception as exc:
                logger.warning("Failed to record charge completion: %s", exc)
        await db.refresh(stop)
        return stop

    result = await db.execute(
        select(Order).where(
            Order.id == stop.order_id,
            tenant_filter(Order, stop.tenant_id),
        )
    )
    order = result.scalar_one_or_none()
    if order:
        operations_policy = await operations_policy_service.get_operations_policy(db, order.tenant_id)
        require_arrival_scan = operations_policy.delivery_workflow.require_arrival_scan
        if stop.status != StopStatus.ARRIVED:
            if not require_arrival_scan and stop.status == StopStatus.PENDING:
                await _assert_prior_stops_done(db, route_id, stop)
                stop.actual_arrival = stop.actual_arrival or datetime.now(UTC).replace(tzinfo=None)
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Stop must be ARRIVED to complete. Current: {stop.status.value}",
                )
        else:
            await _assert_prior_stops_done(db, route_id, stop)
        await apply_stop_status_transition(
            db,
            stop,
            StopStatus.COMPLETED,
            actor_user_id=current_user.id,
            reason=f"{stop.stop_type.value} stop completed",
            lat=stop.lat,
            lon=stop.lon,
        )
        await db.flush()

        # Layer 3 — record empirical (courier, address) service time for
        # the solver to consume in future runs. Non-fatal: a failure
        # here must never block stop completion. Only applies to DELIVERY
        # stops on real orders (depot stops skip).
        if (
            stop.stop_type == StopType.DELIVERY
            and stop.actual_arrival is not None
        ):
            try:
                from app.services.empirical_service_time_service import (
                    record_observation,
                )

                # Approximation: the "service duration" is the gap
                # between actual_arrival and now (stop-complete time).
                # When we add `actual_departure` (deferred), switch to
                # that. The clamp inside record_observation keeps
                # outliers (> 24 h) from poisoning the running median.
                now = datetime.now(UTC).replace(tzinfo=None)
                observed_s = (now - stop.actual_arrival).total_seconds()
                # Look up the route's courier — already on the route
                # ORM object from the get_stop loader.
                courier_id = None
                if stop.route is not None:
                    courier_id = stop.route.courier_id
                if courier_id is not None:
                    await record_observation(
                        db,
                        tenant_id=stop.tenant_id,
                        courier_id=courier_id,
                        lat=stop.lat,
                        lon=stop.lon,
                        service_duration_s=float(observed_s),
                    )
            except Exception:
                logger.warning(
                    "Failed to record empirical service time for stop %s",
                    stop.id,
                    exc_info=True,
                )

        if stop.stop_type == StopType.PICKUP:
            await apply_order_status_transition(
                db,
                order,
                OrderStatus.PICKED_UP,
                actor_user_id=current_user.id,
                reason="Pickup stop completed",
            )
            try:
                await _record_pickup_inventory_event(db, order, route_id)
            except Exception as exc:
                logger.warning("Failed to record pickup inventory event: %s", exc)
        elif stop.stop_type == StopType.DELIVERY:
            existing_pod_result = await db.execute(
                select(ProofOfDelivery).where(
                    ProofOfDelivery.route_stop_id == stop.id,
                    tenant_filter(ProofOfDelivery, stop.tenant_id),
                )
            )
            existing_pod = existing_pod_result.scalar_one_or_none()
            final_photo_urls = (
                photo_urls
                if photo_urls is not None
                else (existing_pod.photo_urls if existing_pod and existing_pod.photo_urls else None)
            )
            if final_photo_urls is None:
                final_photo_urls = [photo_url] if photo_url else None
            final_photo = (
                photo_url
                or (final_photo_urls[0] if final_photo_urls else None)
                or (existing_pod.photo_url if existing_pod else None)
            )
            final_signature = signature_url or (existing_pod.signature_url if existing_pod else None)
            final_receiver_name = receiver_name or (existing_pod.receiver_name if existing_pod else None)
            final_otp_code = otp_code or (existing_pod.otp_code if existing_pod else None)
            final_notes = pod_notes if pod_notes is not None else (existing_pod.notes if existing_pod else None)

            pod_policy = get_pod_policy(order.type, operations_policy)
            can_override = operations_policy_service.get_role_permissions(
                operations_policy,
                current_user.role,
            ).override_pod
            if pod_policy["photo_required"] and (not final_photo_urls or len(final_photo_urls) < int(pod_policy["photo_count"])) and not can_override:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{int(pod_policy['photo_count'])} photo proof-of-delivery file(s) are required for this order type",
                )
            if pod_policy["signature_required"] and not final_signature and not can_override:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Signature proof-of-delivery is required for this order type",
                )
            if pod_policy["otp_required"] and final_otp_code != order.delivery_otp_code and not can_override:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A valid delivery OTP is required for this order type",
                )
            if pod_policy["receiver_name_mode"] == "REQUIRED" and not final_receiver_name and not can_override:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Receiver name is required for this order type",
                )
            if pod_policy["notes_mode"] == "REQUIRED" and not final_notes and not can_override:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Proof notes are required for this order type",
                )

            if (
                photo_urls is not None
                or photo_url is not None
                or signature_url is not None
                or receiver_name is not None
                or otp_code is not None
                or pod_notes is not None
                or (existing_pod is None and (final_photo or final_signature or final_receiver_name or final_otp_code))
            ):
                await _upsert_proof_of_delivery(
                    db,
                    order=order,
                    stop=stop,
                    photo_urls=final_photo_urls,
                    photo_url=final_photo,
                    signature_url=final_signature,
                    receiver_name=final_receiver_name,
                    otp_code=final_otp_code,
                    notes=final_notes,
                )

            if order.status == OrderStatus.PICKED_UP:
                await apply_order_status_transition(
                    db,
                    order,
                    OrderStatus.IN_TRANSIT,
                    actor_user_id=current_user.id,
                    reason="Courier in transit to final delivery",
                )
            elif order.status == OrderStatus.ASSIGNED:
                await _ensure_order_in_transit_for_delivery(
                    db,
                    order=order,
                    current_user=current_user,
                    reason="Courier in transit to final delivery",
                )
            await apply_order_status_transition(
                db,
                order,
                OrderStatus.DELIVERED,
                actor_user_id=current_user.id,
                reason="Delivery stop completed",
            )
            try:
                await _record_delivery_inventory_event(db, order, route_id)
            except Exception as exc:
                logger.warning("Failed to record delivery inventory event: %s", exc)

    await _maybe_complete_route(db, route_id)
    await db.refresh(stop)
    return stop


async def fail_stop(
    db: AsyncSession,
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    current_user: User,
    failure_reason: FailureReason,
    custom_reason_label: str | None = None,
    notes: str | None = None,
) -> RouteStop:
    stop = await _get_stop(
        db,
        route_id,
        stop_id,
        current_user,
        for_update=True,
        require_active_route=True,
    )
    if stop.status == StopStatus.FAILED:
        return stop
    if stop.status not in (StopStatus.PENDING, StopStatus.ARRIVED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stop must be PENDING or ARRIVED to fail. Current: {stop.status.value}",
        )
    await _assert_prior_stops_done(db, route_id, stop)
    await apply_stop_status_transition(
        db,
        stop,
        StopStatus.FAILED,
        actor_user_id=current_user.id,
        reason=f"{failure_reason.value}: {custom_reason_label or notes or ''}".strip(": "),
        lat=stop.lat,
        lon=stop.lon,
    )
    stop.failure_reason = failure_reason
    stop.notes = notes
    await db.flush()

    if stop.stop_type in (StopType.DEPOT_RETURN, StopType.DEPOT_RELOAD) or stop.order_id is None:
        await db.refresh(stop)
        return stop

    result = await db.execute(
        select(Order).where(
            Order.id == stop.order_id,
            tenant_filter(Order, stop.tenant_id),
        )
    )
    order = result.scalar_one_or_none()
    if order:
        # Only DELIVERY stop failures should drive the order failure workflow.
        # Pickup stops can be marked FAILED operationally without moving the
        # order into FAILED (which isn't a valid state transition from ASSIGNED).
        if stop.stop_type != StopType.DELIVERY:
            # RIDE passenger no-show is the one pickup-failure case where the
            # whole order is unrecoverable — the passengers won't materialize
            # later, so we fail the orphan delivery stop and cancel the order
            # so the driver isn't sent to an empty drop-off.
            if (
                failure_reason == FailureReason.PASSENGER_NO_SHOW
                and stop.stop_type == StopType.PICKUP
            ):
                companion_result = await db.execute(
                    select(RouteStop).where(
                        RouteStop.route_id == route_id,
                        RouteStop.order_id == order.id,
                        RouteStop.stop_type == StopType.DELIVERY,
                        RouteStop.status.notin_(
                            [StopStatus.COMPLETED, StopStatus.FAILED]
                        ),
                    )
                )
                companion_delivery = companion_result.scalar_one_or_none()
                if companion_delivery is not None:
                    companion_delivery.status = StopStatus.FAILED
                    companion_delivery.failure_reason = FailureReason.PASSENGER_NO_SHOW
                    companion_delivery.notes = "Passenger no-show at pickup"

                if order.status == OrderStatus.ASSIGNED:
                    await apply_order_status_transition(
                        db,
                        order,
                        OrderStatus.CANCELLED,
                        actor_user_id=current_user.id,
                        reason="Passenger no-show at pickup",
                    )
                await db.flush()

            await _maybe_complete_route(db, route_id)
            await db.refresh(stop)
            return stop

        operations_policy = await operations_policy_service.get_operations_policy(db, order.tenant_id)
        validate_stop_failure_details(
            failure_reason,
            notes,
            require_notes_for_other=operations_policy.failure_handling.require_notes_for_other,
            custom_reason_label=custom_reason_label,
            allowed_custom_reason_labels=operations_policy.failure_handling.custom_reasons,
        )
        await _ensure_order_in_transit_for_delivery(
            db,
            order=order,
            current_user=current_user,
            reason="Delivery failed",
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        stop.failure_reason_detail = custom_reason_label
        order.failed_at = now
        order.customer_response_status = FailedOrderCustomerResponseStatus.PENDING
        order.customer_response_due_at = now + timedelta(hours=operations_policy.failure_handling.escalation_hours)
        order.escalated_at = None
        await apply_order_status_transition(
            db,
            order,
            OrderStatus.FAILED,
            actor_user_id=current_user.id,
            reason=f"Delivery failed: {custom_reason_label or failure_reason.value}",
            event_payload_overrides={
                "failure_reason": failure_reason.value,
                "failure_reason_detail": custom_reason_label,
            },
        )
        order.attempt_count += 1
        await db.flush()
        if order.attempt_count >= order.max_attempts:
            await apply_order_status_transition(
                db,
                order,
                OrderStatus.CANCELLED,
                actor_user_id=current_user.id,
                reason=f"Maximum attempts reached ({order.max_attempts})",
            )
        elif operations_policy.failure_handling.post_failure_action == "AUTOMATIC_RETRY":
            from app.services import order_service as order_service_module

            duration = order.latest_delivery - order.earliest_delivery
            if operations_policy.failure_handling.retry_timing == "SAME_DAY":
                retry_start = now + timedelta(hours=2)
            elif operations_policy.failure_handling.retry_timing == "NEXT_DAY":
                retry_start = now + timedelta(days=1)
            else:
                retry_start = None

            if retry_start is not None:
                retry_end = retry_start + max(duration, timedelta(hours=2))
                await order_service_module.reschedule_order(
                    db,
                    order.id,
                    retry_start,
                    retry_end,
                    principal=order.tenant_id,
                    actor_user_id=current_user.id,
                )
        elif operations_policy.failure_handling.post_failure_action == "RETURN_TO_DEPOT":
            await _ensure_depot_return_stop(
                db,
                route_id=route_id,
                tenant_id=order.tenant_id,
            )

    await _maybe_complete_route(db, route_id)
    await db.refresh(stop)
    return stop
