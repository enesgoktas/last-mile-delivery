import logging
import uuid
from datetime import date, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.courier import Courier
from app.models.depot import Depot
from app.models.vehicle_tag import VehicleTag
from app.models.enums import (
    ORDER_STATE_TRANSITIONS,
    AvailabilityStatus,
    FailedOrderCustomerResponseStatus,
    OrderPriority,
    OrderStatus,
    OrderType,
    RouteStatus,
    StopStatus,
    VehicleType,
)
from app.models.notification import ProofOfDelivery
from app.models.order import Order
from app.models.order_history import OrderStatusHistory
from app.models.route import Route
from app.schemas.order import OrderCreate, OrderUpdate
from app.services import operations_policy_service
from app.services.courier_service import get_courier_location_from_cache
from app.utils.auth import create_tracking_token
from app.utils.compat import utcnow_naive
from app.utils.geo import haversine_meters, point_in_geojson_polygon
from app.utils.tenant_context import get_current_tenant_id, tenant_filter
from app.workers.task_dispatch import enqueue_task

logger = logging.getLogger(__name__)


async def _resolve_tags(
    db: AsyncSession,
    tag_ids: list,
    tenant_id: uuid.UUID,
) -> list[VehicleTag]:
    if not tag_ids:
        return []
    result = await db.execute(
        select(VehicleTag).where(
            VehicleTag.id.in_(tag_ids),
            VehicleTag.tenant_id == tenant_id,
        )
    )
    return list(result.scalars().all())


def validate_state_transition(current: OrderStatus, target: OrderStatus) -> None:
    """Enforce the order state machine. Raises HTTPException if transition is invalid."""
    allowed = ORDER_STATE_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid state transition: {current.value} → {target.value}. "
            f"Allowed: {[s.value for s in allowed]}",
        )


async def _record_status_transition(
    db: AsyncSession,
    order_id: uuid.UUID,
    from_status: OrderStatus | None,
    to_status: OrderStatus,
    *,
    actor_user_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> None:
    event = OrderStatusHistory(
        order_id=order_id,
        from_status=from_status,
        to_status=to_status,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    db.add(event)
    await db.flush()


async def apply_order_status_transition(
    db: AsyncSession,
    order: Order,
    target: OrderStatus,
    *,
    actor_user_id: uuid.UUID | None = None,
    reason: str | None = None,
    event_payload_overrides: dict | None = None,
) -> None:
    if order.status == target:
        return
    validate_state_transition(order.status, target)
    previous = order.status
    order.status = target
    await db.flush()
    await _record_status_transition(
        db,
        order.id,
        previous,
        target,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await _enqueue_webhook_for_transition(
        db,
        order,
        target,
        reason,
        event_payload_overrides=event_payload_overrides,
    )


async def apply_order_status_transitions_bulk(
    db: AsyncSession,
    transitions: list[tuple[Order, OrderStatus]],
    *,
    actor_user_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> None:
    """Apply many order status transitions in one DB round-trip.

    Same semantics as calling `apply_order_status_transition` once per
    `(order, target)` pair, but collapses the per-call `db.flush()` into a
    single flush at the end. For batch flows assigning hundreds of orders,
    this drops persistence latency from O(N × flush_round_trip) to O(1).

    Webhook/notification enqueue still fires per transition — those calls
    are non-DB (Redis enqueue) and one of them does an extra ProofOfDelivery
    SELECT only on the DELIVERED transition (irrelevant for batch ASSIGNED).
    """
    if not transitions:
        return
    history_events: list[OrderStatusHistory] = []
    transitioned: list[tuple[Order, OrderStatus]] = []
    for order, target in transitions:
        if order.status == target:
            continue
        validate_state_transition(order.status, target)
        previous = order.status
        order.status = target
        history_events.append(
            OrderStatusHistory(
                order_id=order.id,
                from_status=previous,
                to_status=target,
                actor_user_id=actor_user_id,
                reason=reason,
            )
        )
        transitioned.append((order, target))
    if not transitioned:
        return
    db.add_all(history_events)
    await db.flush()
    for order, target in transitioned:
        await _enqueue_webhook_for_transition(db, order, target, reason)


async def _enqueue_webhook_for_transition(
    db: AsyncSession,
    order: Order,
    target: OrderStatus,
    reason: str | None,
    *,
    event_payload_overrides: dict | None = None,
) -> None:
    # RIDE orders emit a parallel `ride.*` event surface so external systems
    # can subscribe to passenger lifecycle separately from package delivery.
    # The status -> verb mapping is identical, only the prefix changes.
    is_ride = order.type == OrderType.RIDE
    status_verbs = {
        OrderStatus.ASSIGNED: "assigned",
        OrderStatus.PICKED_UP: "picked_up" if not is_ride else "passenger_boarded",
        OrderStatus.IN_TRANSIT: "in_transit",
        OrderStatus.DELIVERED: "delivered" if not is_ride else "completed",
        OrderStatus.FAILED: "failed",
        OrderStatus.CANCELLED: "cancelled",
        OrderStatus.EXPIRED: "expired",
    }
    verb = status_verbs.get(target)
    event_type = f"{'ride' if is_ride else 'order'}.{verb}" if verb else None
    if event_payload_overrides and event_payload_overrides.get("_event_type"):
        event_type = str(event_payload_overrides["_event_type"])
    if not event_type:
        return

    tracking_url = None
    if order.tracking_token:
        base = settings.TRACKING_PUBLIC_BASE_URL.rstrip("/")
        tracking_url = f"{base}/{order.tracking_token}"

    payload: dict = {
        "order_id": str(order.id),
        "tenant_id": str(order.tenant_id),
        "external_ref": order.external_ref,
        "status": target.value,
        "timestamp": utcnow_naive().isoformat() + "Z",
        "assigned_courier_id": str(order.assigned_courier_id) if order.assigned_courier_id else None,
        "courier_id": str(order.assigned_courier_id) if order.assigned_courier_id else None,
        "customer_email": order.customer_email,
        "customer_phone": order.customer_phone,
        "delivery_otp_code": order.delivery_otp_code,
        "custom_fields": order.custom_fields,
        "reason": reason,
        "tracking_url": tracking_url,
        "reschedule_url": tracking_url if target == OrderStatus.FAILED else None,
        "response_due_at": order.customer_response_due_at.isoformat() if order.customer_response_due_at else None,
        "customer_response_status": (
            order.customer_response_status.value
            if order.customer_response_status is not None
            else None
        ),
    }
    # RIDE-specific fields surface in every ride.* event so subscribers
    # don't have to fetch the order separately for each notification.
    if is_ride:
        payload.update(
            {
                "passenger_count": order.passenger_count,
                "passenger_name": order.passenger_name,
                "passenger_phone": order.passenger_phone,
                "flight_info": order.flight_info,
                "pickup_address": order.pickup_address,
                "dropoff_address": order.delivery_address,
            }
        )

    if target == OrderStatus.DELIVERED:
        pod_result = await db.execute(
            select(ProofOfDelivery).where(
                ProofOfDelivery.order_id == order.id,
                tenant_filter(ProofOfDelivery, order.tenant_id),
            )
        )
        pod = pod_result.scalar_one_or_none()
        payload["delivered_at"] = utcnow_naive().isoformat() + "Z"
        payload["pod_photo_url"] = pod.photo_url if pod else None
        payload["pod_signature_url"] = pod.signature_url if pod else None

    if event_payload_overrides:
        payload.update({k: v for k, v in event_payload_overrides.items() if k != "_event_type"})

    try:
        from app.workers.webhook_tasks import dispatch_webhook_event

        enqueue_task(dispatch_webhook_event, args=[event_type, payload])
    except Exception:
        # Webhook failure should never block primary transaction flow.
        logger.exception(
            "Failed to enqueue webhook for order transition [order_id=%s, event=%s]",
            order.id,
            event_type,
        )

    try:
        from app.workers.notification_tasks import dispatch_order_notifications

        enqueue_task(dispatch_order_notifications, args=[event_type, payload])
    except Exception:
        # Notification dispatch should not block order workflow.
        logger.exception(
            "Failed to enqueue notification for order transition [order_id=%s, event=%s]",
            order.id,
            event_type,
        )


def _split_ride_distribution(total: int, per_vehicle: int) -> list[int]:
    """Split `total` passengers into the smallest N slices ≤ `per_vehicle`,
    balanced as evenly as possible (largest slices first).

    e.g. _split_ride_distribution(10, 4) → [4, 3, 3]
         _split_ride_distribution(12, 4) → [4, 4, 4]
         _split_ride_distribution(7, 3)  → [3, 2, 2]
    """
    import math
    if total <= per_vehicle:
        return [total]
    n = math.ceil(total / per_vehicle)
    base, extra = divmod(total, n)
    return [base + 1] * extra + [base] * (n - extra)


async def _day_plan_in_flight(
    db: AsyncSession,
    *,
    tenant_id,
    on_date: date,
) -> bool:
    """Return True if batch dispatch has already produced assignments for ``on_date``.

    Used to gate auto-dispatch of newly-created orders: before batch has run
    for the day the new order would join, orders accumulate for the dispatcher's
    own planning algorithm; after batch has run, they slot in via the insertion
    heuristic.
    """
    result = await db.execute(
        select(Order.id)
        .where(
            tenant_filter(Order, tenant_id),
            Order.scheduled_for_date == on_date,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _split_ride_into_siblings(
    db: AsyncSession,
    *,
    parent: "Order",
    per_vehicle: int,
    tag_ids: list,
    operations_policy,
    actor_user_id: uuid.UUID | None,
) -> list["Order"]:
    """Mutate `parent` to carry the first slice and persist N-1 siblings.

    All siblings share the parent's id as `ride_group_id`. They each
    get their own `tracking_token` and follow the same status flow.
    Returns the new sibling orders (excluding the parent).
    """
    sizes = _split_ride_distribution(parent.passenger_count, per_vehicle)
    if len(sizes) <= 1:
        return []  # No split needed

    # Parent keeps the first slice; gets stamped with its own id as
    # the group id so a single `WHERE ride_group_id = X` query lists
    # the whole group.
    parent.passenger_count = sizes[0]
    parent.ride_group_id = parent.id
    await db.flush()

    siblings: list[Order] = []
    for slice_passengers in sizes[1:]:
        sibling_id = uuid.uuid4()
        tracking_token = create_tracking_token(str(sibling_id))
        sibling = Order(
            id=sibling_id,
            tenant_id=parent.tenant_id,
            type=parent.type,
            priority=parent.priority,
            pickup_address=parent.pickup_address,
            pickup_lat=parent.pickup_lat,
            pickup_lon=parent.pickup_lon,
            delivery_address=parent.delivery_address,
            delivery_lat=parent.delivery_lat,
            delivery_lon=parent.delivery_lon,
            depot_id=parent.depot_id,
            weight_kg=0.0,
            is_fragile=False,
            is_temp_sensitive=False,
            earliest_pickup=parent.earliest_pickup,
            latest_pickup=parent.latest_pickup,
            earliest_delivery=parent.earliest_delivery,
            latest_delivery=parent.latest_delivery,
            customer_email=parent.customer_email,
            customer_phone=parent.customer_phone,
            tracking_token=tracking_token,
            delivery_otp_code=None,
            external_ref=None,
            custom_fields=None,
            max_attempts=parent.max_attempts,
            notes=parent.notes,
            status=parent.status,
            passenger_count=slice_passengers,
            passenger_name=parent.passenger_name,
            passenger_phone=parent.passenger_phone,
            flight_info=parent.flight_info,
            special_requirements=parent.special_requirements,
            pickup_buffer_minutes_override=parent.pickup_buffer_minutes_override,
            service_time_pickup_seconds_override=parent.service_time_pickup_seconds_override,
            ride_group_id=parent.id,
        )
        # Resolve tags pre-flush to avoid the post-flush lazy-load that
        # fails in async context (same fix as create_order).
        if tag_ids:
            sibling.required_vehicle_tags = await _resolve_tags(
                db, tag_ids, parent.tenant_id
            )
        db.add(sibling)
        await db.flush()
        await _record_status_transition(
            db,
            sibling.id,
            None,
            sibling.status,
            actor_user_id=actor_user_id,
            reason=f"Sibling of split ride {parent.id}",
        )
        siblings.append(sibling)
    return siblings


async def create_order(
    db: AsyncSession,
    data: OrderCreate,
    *,
    tenant_id,
    initial_status: OrderStatus = OrderStatus.PENDING,
    creation_reason: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> Order:
    """Create a new order with a tracking token.

    If batch dispatch has already produced assignments for the order's intended
    day (``earliest_delivery.date()``), enqueues an insertion dispatch task so
    the order — and any RIDE split siblings — is immediately routed into an
    existing courier route. Otherwise the order stays PENDING for the next
    batch cycle.
    """
    resolved_tenant_id = get_current_tenant_id(tenant_id)
    operations_policy = await operations_policy_service.get_operations_policy(db, resolved_tenant_id)

    # ── Phase 1: feature-flag gate on order type ──
    # The schema (`OrderCreate`) already enforces shape per type; this is the
    # tenant-level toggle layer. Tenants without `rides_enabled` cannot create
    # RIDE orders; tenants without `deliveries_enabled` cannot create anything
    # else. `flight_info` is meaningful only when airport features are on.
    flags = operations_policy.feature_flags
    if data.type == OrderType.RIDE:
        if not flags.rides_enabled:
            raise HTTPException(
                status_code=403,
                detail="Passenger transport (RIDE orders) is not enabled for this tenant.",
            )
        if data.flight_info is not None and not flags.ride_airport_features_enabled:
            raise HTTPException(
                status_code=422,
                detail="flight_info requires the airport-features module.",
            )
    else:
        if not flags.deliveries_enabled:
            raise HTTPException(
                status_code=403,
                detail="Package delivery is not enabled for this tenant.",
            )

    custom_fields = operations_policy_service.validate_custom_field_values(
        operations_policy_service.get_custom_field_definitions_for_order_type(
            operations_policy,
            data.type,
        ),
        getattr(data, "custom_fields", None),
    )

    field_rules = operations_policy.order_form.standard_fields
    if field_rules.customer_phone == "REQUIRED" and not data.customer_phone:
        raise HTTPException(status_code=422, detail="customer_phone is required for this workspace")
    if field_rules.external_ref == "REQUIRED" and not data.external_ref:
        raise HTTPException(status_code=422, detail="external_ref is required for this workspace")
    if field_rules.depot_id == "REQUIRED" and getattr(data, "depot_id", None) is None:
        raise HTTPException(status_code=422, detail="depot_id is required for this workspace")
    if field_rules.notes == "REQUIRED" and not data.notes:
        raise HTTPException(status_code=422, detail="notes are required for this workspace")
    provided_fields = getattr(data, "model_fields_set", set())
    if field_rules.is_fragile == "REQUIRED" and "is_fragile" not in provided_fields:
        raise HTTPException(status_code=422, detail="is_fragile must be provided for this workspace")
    if field_rules.is_temp_sensitive == "REQUIRED" and "is_temp_sensitive" not in provided_fields:
        raise HTTPException(status_code=422, detail="is_temp_sensitive must be provided for this workspace")

    if data.external_ref:
        existing = await db.execute(
            select(Order).where(
                Order.external_ref == data.external_ref,
                tenant_filter(Order, resolved_tenant_id),
            )
        )
        existing_order = existing.scalar_one_or_none()
        if existing_order:
            return existing_order

    order_id = uuid.uuid4()
    tracking_token = create_tracking_token(str(order_id))

    order = Order(
        id=order_id,
        tenant_id=resolved_tenant_id,
        type=data.type,
        priority=data.priority,
        pickup_address=data.pickup_address,
        pickup_lat=data.pickup_lat,
        pickup_lon=data.pickup_lon,
        delivery_address=data.delivery_address,
        delivery_lat=data.delivery_lat,
        delivery_lon=data.delivery_lon,
        depot_id=getattr(data, "depot_id", None),
        weight_kg=data.weight_kg,
        is_fragile=data.is_fragile,
        is_temp_sensitive=data.is_temp_sensitive,
        earliest_pickup=data.earliest_pickup,
        latest_pickup=data.latest_pickup,
        earliest_delivery=data.earliest_delivery,
        latest_delivery=data.latest_delivery,
        customer_email=data.customer_email,
        customer_phone=data.customer_phone,
        tracking_token=tracking_token,
        delivery_otp_code=(
            operations_policy_service.generate_delivery_otp()
            if operations_policy_service.resolve_pod_requirements(operations_policy, data.type).require_otp
            else None
        ),
        external_ref=data.external_ref,
        custom_fields=custom_fields or None,
        max_attempts=operations_policy.failure_handling.max_attempts,
        notes=data.notes,
        status=initial_status,
        # ── Phase 1: passenger / ride fields (Pydantic enforced shape) ──
        passenger_count=data.passenger_count or 1,
        passenger_name=data.passenger_name,
        passenger_phone=data.passenger_phone,
        flight_info=data.flight_info,
        special_requirements=data.special_requirements,
        pickup_buffer_minutes_override=data.pickup_buffer_minutes_override,
        service_time_pickup_seconds_override=data.service_time_pickup_seconds_override,
    )
    # Resolve and attach required vehicle tags BEFORE flushing. Setting
    # `order.required_vehicle_tags = ...` after a flush triggers SQLAlchemy
    # to lazy-load the existing relationship rows first, which fails in
    # async context with `MissingGreenlet`. Attaching pre-flush avoids
    # that round-trip entirely.
    tag_ids = getattr(data, "required_vehicle_tag_ids", None) or []
    if tag_ids:
        order.required_vehicle_tags = await _resolve_tags(
            db, tag_ids, resolved_tenant_id
        )

    db.add(order)
    await db.flush()

    await _record_status_transition(
        db,
        order.id,
        None,
        initial_status,
        actor_user_id=actor_user_id,
        reason=creation_reason or "Order created",
    )

    # RIDE orders have no physical package — skip the inventory shadow row.
    if data.type != OrderType.RIDE:
        from app.services.inventory_service import create_package_for_order
        await create_package_for_order(
            db,
            tenant_id=resolved_tenant_id,
            order_id=order.id,
            weight_kg=data.weight_kg,
            length_cm=getattr(data, "length_cm", None),
            width_cm=getattr(data, "width_cm", None),
            height_cm=getattr(data, "height_cm", None),
            volume_cm3=getattr(data, "volume_cm3", None),
            depot_id=getattr(data, "depot_id", None),
        )

    await db.refresh(order)

    # ── Ride splitting ────────────────────────────────────────────────
    # When dispatcher ticks `allow_split` on a RIDE whose passenger
    # count exceeds the per-vehicle ceiling, peel off N-1 sibling
    # orders with the same ride_group_id. Each sibling is a normal
    # solver-routable RIDE; the solver picks vehicles independently.
    siblings: list[Order] = []
    if (
        data.type == OrderType.RIDE
        and data.allow_split
        and order.passenger_count > 0
    ):
        per_vehicle = (
            data.passengers_per_vehicle
            or operations_policy.ride_settings.default_passengers_per_vehicle
        )
        if order.passenger_count > per_vehicle:
            siblings = await _split_ride_into_siblings(
                db,
                parent=order,
                per_vehicle=per_vehicle,
                tag_ids=tag_ids,
                operations_policy=operations_policy,
                actor_user_id=actor_user_id,
            )
            await db.refresh(order)

    # Auto-dispatch fires only once the day's plan is in flight — i.e. the
    # dispatcher has already batched assignments for the order's intended day.
    # Before batch has run, new orders accumulate as PENDING for the next batch
    # cycle so the dispatcher's own algorithm gets to plan them globally.
    if (
        initial_status == OrderStatus.PENDING
        and operations_policy_service.should_auto_dispatch_on_demand(operations_policy)
        and order.earliest_delivery is not None
        and await _day_plan_in_flight(
            db,
            tenant_id=resolved_tenant_id,
            on_date=order.earliest_delivery.date(),
        )
    ):
        try:
            from app.workers.optimization_tasks import run_insertion_dispatch

            for target in (order, *siblings):
                enqueue_task(
                    run_insertion_dispatch,
                    args=[str(target.id), str(target.tenant_id)],
                )
        except Exception:
            logger.exception(
                "Failed to enqueue insertion dispatch for order %s",
                order.id,
            )

    return order


async def create_return_order(
    db: AsyncSession,
    order_id: uuid.UUID,
    *,
    principal,
    earliest_pickup: datetime | None = None,
    latest_pickup: datetime | None = None,
    reason_code: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> Order:
    original = await get_order(db, order_id, principal)
    if original.status != OrderStatus.DELIVERED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Returns can only be created for delivered orders",
        )
    if original.depot_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Original order has no depot to receive the return",
        )
    depot_result = await db.execute(
        select(Depot).where(
            Depot.id == original.depot_id,
            tenant_filter(Depot, original.tenant_id),
        )
    )
    depot = depot_result.scalar_one_or_none()
    if depot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Return depot not found")

    return_data = OrderCreate(
        type=OrderType.RETURN,
        priority=original.priority,
        pickup_address=original.delivery_address,
        pickup_lat=original.delivery_lat,
        pickup_lon=original.delivery_lon,
        delivery_address=depot.address,
        delivery_lat=depot.lat,
        delivery_lon=depot.lon,
        depot_id=depot.id,
        weight_kg=original.weight_kg,
        is_fragile=original.is_fragile,
        is_temp_sensitive=original.is_temp_sensitive,
        earliest_pickup=earliest_pickup,
        latest_pickup=latest_pickup,
        earliest_delivery=earliest_pickup or utcnow_naive(),
        latest_delivery=latest_pickup or (utcnow_naive() + timedelta(days=1)),
        customer_email=original.customer_email,
        customer_phone=original.customer_phone,
        external_ref=f"{original.external_ref or original.id}:RETURN",
        custom_fields=None,
        notes=f"Return for order {original.id}" + (f" ({reason_code})" if reason_code else ""),
    )
    return_order = await create_order(
        db,
        return_data,
        tenant_id=principal,
        initial_status=OrderStatus.PENDING,
        creation_reason="Return initiated",
        actor_user_id=actor_user_id,
    )
    return_order.return_of_order_id = original.id
    await db.flush()
    await _enqueue_webhook_for_transition(
        db,
        return_order,
        OrderStatus.PENDING,
        "Return initiated",
        event_payload_overrides={"_event_type": "order.return_initiated"},
    )
    return return_order


def _build_order_filters(
    base_query,
    *,
    status_filter: OrderStatus | None = None,
    type_filter: OrderType | None = None,
    priority_filter: OrderPriority | None = None,
    customer_response_status_filter: FailedOrderCustomerResponseStatus | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    delivery_from: datetime | None = None,
    delivery_to: datetime | None = None,
    service_date: date | None = None,
    search: str | None = None,
):
    if status_filter:
        base_query = base_query.where(Order.status == status_filter)
    if type_filter:
        base_query = base_query.where(Order.type == type_filter)
    if priority_filter:
        base_query = base_query.where(Order.priority == priority_filter)
    if customer_response_status_filter:
        base_query = base_query.where(Order.customer_response_status == customer_response_status_filter)
    if created_from:
        base_query = base_query.where(Order.created_at >= created_from)
    if created_to:
        base_query = base_query.where(Order.created_at <= created_to)
    if service_date:
        service_start = datetime(service_date.year, service_date.month, service_date.day)
        service_end = service_start + timedelta(days=1)
        base_query = base_query.where(
            or_(
                Order.scheduled_for_date == service_date,
                and_(
                    Order.scheduled_for_date.is_(None),
                    Order.latest_delivery >= service_start,
                    Order.earliest_delivery < service_end,
                ),
            )
        )
    if delivery_from:
        base_query = base_query.where(Order.latest_delivery >= delivery_from)
    if delivery_to:
        base_query = base_query.where(Order.earliest_delivery <= delivery_to)
    if search:
        from sqlalchemy import String, cast
        pattern = f"%{search}%"
        base_query = base_query.where(or_(
            Order.delivery_address.ilike(pattern),
            Order.pickup_address.ilike(pattern),
            Order.customer_email.ilike(pattern),
            cast(Order.id, String).ilike(pattern),
        ))
    return base_query


async def count_orders(
    db: AsyncSession,
    principal,
    **filters,
) -> int:
    from sqlalchemy import func as sa_func
    query = _build_order_filters(
        select(sa_func.count(Order.id)).where(tenant_filter(Order, principal)),
        **filters,
    )
    result = await db.execute(query)
    return result.scalar_one()


async def get_orders(
    db: AsyncSession,
    principal,
    status_filter: OrderStatus | None = None,
    type_filter: OrderType | None = None,
    priority_filter: OrderPriority | None = None,
    customer_response_status_filter: FailedOrderCustomerResponseStatus | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    delivery_from: datetime | None = None,
    delivery_to: datetime | None = None,
    service_date: date | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[Order]:
    """List orders with optional filters, search, and pagination."""
    query = _build_order_filters(
        select(Order).where(tenant_filter(Order, principal)),
        status_filter=status_filter,
        type_filter=type_filter,
        priority_filter=priority_filter,
        customer_response_status_filter=customer_response_status_filter,
        created_from=created_from,
        created_to=created_to,
        delivery_from=delivery_from,
        delivery_to=delivery_to,
        service_date=service_date,
        search=search,
    )
    query = query.order_by(Order.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_order(db: AsyncSession, order_id: uuid.UUID, principal=None) -> Order:
    """Get a single order by ID. Raises 404 if not found."""
    query = select(Order).where(Order.id == order_id)
    if principal is not None:
        query = query.where(tenant_filter(Order, principal))
    result = await db.execute(query)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


async def update_order(db: AsyncSession, order_id: uuid.UUID, data: OrderUpdate, principal=None) -> Order:
    """Update an order (only allowed while in PENDING state)."""
    if principal is not None:
        await operations_policy_service.ensure_permission(db, principal, "edit_order")
    order = await get_order(db, order_id, principal)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order can only be updated while in PENDING status",
        )
    update_data = data.model_dump(exclude_unset=True)
    tag_ids = update_data.pop("required_vehicle_tag_ids", None)
    operations_policy = await operations_policy_service.get_operations_policy(db, order.tenant_id)
    if "custom_fields" in update_data:
        update_data["custom_fields"] = operations_policy_service.validate_custom_field_values(
            operations_policy_service.get_custom_field_definitions_for_order_type(
                operations_policy,
                order.type,
            ),
            update_data.get("custom_fields"),
        ) or None
    for key, value in update_data.items():
        setattr(order, key, value)
    if tag_ids is not None:
        order.required_vehicle_tags = await _resolve_tags(db, tag_ids, order.tenant_id)
    await db.flush()
    await db.refresh(order)
    return order


async def cancel_order(
    db: AsyncSession,
    order_id: uuid.UUID,
    *,
    principal=None,
    actor_user_id: uuid.UUID | None = None,
) -> Order:
    """Cancel an order. Only allowed from PENDING, ASSIGNED, or FAILED states."""
    if principal is not None:
        await operations_policy_service.ensure_permission(db, principal, "cancel_order")
    order = await get_order(db, order_id, principal)
    await apply_order_status_transition(
        db,
        order,
        OrderStatus.CANCELLED,
        actor_user_id=actor_user_id,
        reason="Order cancelled",
    )
    await db.refresh(order)
    return order


def _with_preferred_courier(
    custom_fields: dict | None,
    preferred_courier_id: uuid.UUID | None,
) -> dict | None:
    next_fields = dict(custom_fields) if isinstance(custom_fields, dict) else {}
    if preferred_courier_id is None:
        next_fields.pop("preferred_courier_id", None)
    else:
        next_fields["preferred_courier_id"] = str(preferred_courier_id)
    return next_fields or None


def _estimate_travel_seconds(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    speed_ms = settings.SPEED_ESTIMATE_KMH * 1000.0 / 3600.0
    road_m = haversine_meters(lat1, lon1, lat2, lon2) * 1.3
    return round(road_m / speed_ms) if speed_ms > 0 else 0


async def _assert_time_window_feasible(
    db: AsyncSession,
    order: Order,
    courier: Courier,
) -> None:
    now = utcnow_naive()
    source_lat = courier.depot_lat
    source_lon = courier.depot_lon
    base_time = now

    location = await get_courier_location_from_cache(courier.id, db=db)
    if location:
        source_lat = location.get("lat", source_lat)
        source_lon = location.get("lon", source_lon)

    result = await db.execute(
        select(Route)
        .where(
            Route.courier_id == courier.id,
            Route.status.in_([RouteStatus.PLANNED, RouteStatus.ACTIVE]),
            tenant_filter(Route, courier.tenant_id),
        )
        .options(selectinload(Route.stops))
        .order_by(Route.created_at.desc())
        .limit(1)
    )
    active_route = result.scalar_one_or_none()
    pending_stop_count = 0
    if active_route:
        route = active_route
        if route.stops:
            pending_stops = [
                s for s in route.stops
                if s.status in (StopStatus.PENDING, StopStatus.ARRIVED)
            ]
            pending_stop_count = len(pending_stops)
            if pending_stops:
                last_stop = max(pending_stops, key=lambda s: s.sequence_number)
                source_lat = last_stop.lat
                source_lon = last_stop.lon
                base_time = max(base_time, last_stop.planned_arrival)

    if (
        order.type == OrderType.BULK
        and courier.vehicle_type == VehicleType.MOTORCYCLE
        and (pending_stop_count + 2) > settings.BULK_MOTORCYCLE_MAX_STOP_COUNT
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Bulk orders cannot be assigned to motorcycles when the "
                f"route would exceed {settings.BULK_MOTORCYCLE_MAX_STOP_COUNT} stops"
            ),
        )

    to_pickup_s = _estimate_travel_seconds(
        source_lat,
        source_lon,
        order.pickup_lat,
        order.pickup_lon,
    )
    pickup_eta = base_time + timedelta(seconds=to_pickup_s)
    if order.latest_pickup and pickup_eta > order.latest_pickup:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pickup window is not feasible for the selected courier",
        )

    to_delivery_s = _estimate_travel_seconds(
        order.pickup_lat,
        order.pickup_lon,
        order.delivery_lat,
        order.delivery_lon,
    )
    service_s = settings.SERVICE_TIME_MINUTES * 60
    delivery_eta = pickup_eta + timedelta(seconds=to_delivery_s + service_s)
    if delivery_eta > order.latest_delivery:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Delivery window is not feasible for the selected courier",
        )


async def assign_order(
    db: AsyncSession,
    order_id: uuid.UUID,
    courier_id: uuid.UUID,
    *,
    principal=None,
    actor_user_id: uuid.UUID | None = None,
) -> Order:
    """Manually assign a courier to an order with hard constraint checks."""
    if principal is not None:
        await operations_policy_service.ensure_permission(db, principal, "reassign_order")
    query = select(Order).where(Order.id == order_id)
    if principal is not None:
        query = query.where(tenant_filter(Order, principal))
    result = await db.execute(query.with_for_update())
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    validate_state_transition(order.status, OrderStatus.ASSIGNED)

    if order.latest_delivery < utcnow_naive():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot assign order with a delivery window that has already passed",
        )

    # Fetch courier
    result = await db.execute(
        select(Courier).where(
            Courier.id == courier_id,
            tenant_filter(Courier, order.tenant_id),
        )
    )
    courier = result.scalar_one_or_none()
    if not courier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Courier not found")

    # Load resolved constraints for this courier
    from app.services.constraint_service import get_resolved_constraints_for_courier
    rc = await get_resolved_constraints_for_courier(db, courier)

    if courier.availability_status != AvailabilityStatus.ONLINE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Courier must be ONLINE to accept assignments",
        )

    if order.weight_kg > rc["max_weight_kg"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Order weight ({order.weight_kg}kg) exceeds courier capacity ({rc['max_weight_kg']}kg)",
        )

    if order.is_fragile and (
        courier.vehicle_type == VehicleType.MOTORCYCLE
        or not rc.get("allow_fragile", True)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This courier is not configured to carry fragile items",
        )

    if order.is_temp_sensitive and not rc.get("allow_temp_sensitive", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This courier is not configured to carry temperature-sensitive items",
        )

    if courier.zone_polygon and not point_in_geojson_polygon(
        order.pickup_lat,
        order.pickup_lon,
        courier.zone_polygon,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order pickup location is outside courier zone",
        )

    await _assert_time_window_feasible(db, order, courier)

    from app.services import route_service as route_service_module

    active_route = await route_service_module.get_active_route_for_courier(
        db,
        courier_id,
        order.tenant_id,
    )
    if active_route is None:
        route = Route(
            tenant_id=order.tenant_id,
            courier_id=courier_id,
            status=RouteStatus.PLANNED,
            planned_distance_km=0.0,
            optimization_run_id=None,
        )
        db.add(route)
        await db.flush()
        await db.refresh(route)

        pickup_stop, delivery_stop = route_service_module.build_route_stops_for_order(
            route_id=route.id,
            order=order,
            pickup_sequence=0,
            delivery_sequence=1,
        )
        db.add_all([pickup_stop, delivery_stop])
        await db.flush()
        await route_service_module.retime_route_stops(db, route.id)
        order.route_id = route.id
    else:
        next_sequence = max((stop.sequence_number for stop in active_route.stops), default=-1) + 1
        pickup_stop, delivery_stop = route_service_module.build_route_stops_for_order(
            route_id=active_route.id,
            order=order,
            pickup_sequence=next_sequence,
            delivery_sequence=next_sequence + 1,
        )
        db.add_all([pickup_stop, delivery_stop])
        await db.flush()
        await route_service_module.retime_route_stops(db, active_route.id)
        order.route_id = active_route.id

    order.assigned_courier_id = courier_id
    order.scheduled_for_date = delivery_stop.planned_arrival.date()
    await apply_order_status_transition(
        db,
        order,
        OrderStatus.ASSIGNED,
        actor_user_id=actor_user_id,
        reason="Order manually assigned",
    )
    await db.flush()
    await db.refresh(order)
    return order


async def set_preferred_courier(
    db: AsyncSession,
    order_id: uuid.UUID,
    preferred_courier_id: uuid.UUID | None,
    *,
    principal=None,
) -> Order:
    """Pin or clear a preferred courier for a pending order."""
    if principal is not None:
        await operations_policy_service.ensure_permission(db, principal, "reassign_order")

    query = select(Order).where(Order.id == order_id)
    if principal is not None:
        query = query.where(tenant_filter(Order, principal))
    result = await db.execute(query.with_for_update())
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    if order.status != OrderStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Preferred courier can only be changed while the order is pending",
        )
    if order.assigned_courier_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Preferred courier cannot be changed after assignment",
        )

    if preferred_courier_id is not None:
        courier_result = await db.execute(
            select(Courier).where(
                Courier.id == preferred_courier_id,
                tenant_filter(Courier, order.tenant_id),
            )
        )
        courier = courier_result.scalar_one_or_none()
        if not courier:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Courier not found")

    order.custom_fields = _with_preferred_courier(order.custom_fields, preferred_courier_id)
    await db.flush()
    await db.refresh(order)
    return order


async def reschedule_order(
    db: AsyncSession,
    order_id: uuid.UUID,
    earliest_delivery: datetime,
    latest_delivery: datetime,
    *,
    principal=None,
    actor_user_id: uuid.UUID | None = None,
) -> Order:
    """Reschedule a failed order with new time windows."""
    if principal is not None:
        await operations_policy_service.ensure_permission(db, principal, "edit_order")
    order = await get_order(db, order_id, principal)
    validate_state_transition(order.status, OrderStatus.RESCHEDULED)

    if order.attempt_count >= order.max_attempts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum attempts ({order.max_attempts}) reached. Cannot reschedule.",
        )

    previous = order.status
    order.status = OrderStatus.RESCHEDULED
    order.earliest_delivery = earliest_delivery
    order.latest_delivery = latest_delivery
    order.assigned_courier_id = None
    order.route_id = None
    order.scheduled_for_date = None
    order.customer_response_status = FailedOrderCustomerResponseStatus.RESPONDED
    order.customer_response_due_at = None
    order.escalated_at = None
    await db.flush()
    await _record_status_transition(
        db,
        order.id,
        previous,
        OrderStatus.RESCHEDULED,
        actor_user_id=actor_user_id,
        reason="Order rescheduled",
    )

    # Automatically transition to PENDING for re-dispatch
    await apply_order_status_transition(
        db,
        order,
        OrderStatus.PENDING,
        actor_user_id=actor_user_id,
        reason="Order re-queued after reschedule",
    )
    await db.refresh(order)
    return order


async def get_order_status_history(
    db: AsyncSession,
    order_id: uuid.UUID,
    principal=None,
) -> list[OrderStatusHistory]:
    await get_order(db, order_id, principal)
    result = await db.execute(
        select(OrderStatusHistory)
        .where(OrderStatusHistory.order_id == order_id)
        .order_by(OrderStatusHistory.created_at.asc())
    )
    return list(result.scalars().all())
