import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import OrderStatus
from app.models.order import Order
from app.models.tenant import Tenant
from app.schemas.tracking import TenantBranding, TrackingResponse
from app.services import eta_service, operations_policy_service, order_service
from app.services.courier_service import get_courier_location_from_cache
from app.utils.auth import decode_tracking_token


async def get_tracking_data(db: AsyncSession, token: str) -> TrackingResponse:
    """Decode tracking token, fetch order and courier location."""
    payload = decode_tracking_token(token)
    order_id = payload.get("order_id")

    result = await db.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    operations_policy = await operations_policy_service.get_operations_policy(db, order.tenant_id)
    eta_visibility = operations_policy.notifications.eta_visibility

    # Fetch tenant branding for white-label tracking page
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == order.tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    branding = TenantBranding(
        logo_url=tenant.logo_url if tenant else None,
        primary_color=tenant.primary_color if tenant else None,
        secondary_color=tenant.secondary_color if tenant else None,
    )

    courier_lat = None
    courier_lon = None
    last_updated = None
    estimated_arrival = None
    eta_source = None
    total_stops = None
    completed_stops = None
    remaining_stops = None
    next_stop_sequence = None
    target_stop_sequence = None

    if order.assigned_courier_id:
        location = await get_courier_location_from_cache(order.assigned_courier_id, db=db)
        if location:
            if eta_visibility == "LIVE_MAP":
                courier_lat = location.get("lat")
                courier_lon = location.get("lon")
            last_updated = location.get("updated_at")
            if (
                order.status
                not in (OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.EXPIRED)
                and location.get("lat") is not None
                and location.get("lon") is not None
            ):
                eta = await eta_service.compute_tracking_eta(
                    db,
                    order=order,
                    courier_lat=location.get("lat"),
                    courier_lon=location.get("lon"),
                )
                estimated_arrival = eta.estimated_arrival if eta_visibility != "NONE" else None
                eta_source = eta.eta_source
                if eta_visibility != "NONE":
                    total_stops = eta.total_stops
                    completed_stops = eta.completed_stops
                    remaining_stops = eta.remaining_stops
                    next_stop_sequence = eta.next_stop_sequence
                    target_stop_sequence = eta.target_stop_sequence

    return TrackingResponse(
        order_id=str(order.id),
        order_type=order.type,
        status=order.status,
        status_label=operations_policy.delivery_workflow.customer_status_labels.get(
            order.status.value,
            order.status.value.replace("_", " ").title(),
        ),
        delivery_address=order.delivery_address,
        delivery_lat=order.delivery_lat,
        delivery_lon=order.delivery_lon,
        pickup_address=order.pickup_address,
        route_id=str(order.route_id) if order.route_id else None,
        courier_lat=courier_lat,
        courier_lon=courier_lon,
        estimated_arrival=estimated_arrival,
        last_updated=last_updated,
        eta_source=eta_source,
        total_stops=total_stops,
        completed_stops=completed_stops,
        remaining_stops=remaining_stops,
        next_stop_sequence=next_stop_sequence,
        target_stop_sequence=target_stop_sequence,
        can_reschedule=(
            order.status == OrderStatus.FAILED
            and operations_policy_service.can_customer_reschedule(operations_policy)
        ),
        can_update_instructions=operations_policy_service.can_customer_update_instructions(operations_policy),
        show_live_map=eta_visibility == "LIVE_MAP",
        eta_visibility=eta_visibility,
        customer_instructions=order.notes,
        branding=branding,
        # Phase 1: surface passenger-facing fields for RIDE orders.
        passenger_name=order.passenger_name,
        flight_info=order.flight_info,
    )


async def reschedule_by_tracking_token(
    db: AsyncSession,
    token: str,
    *,
    earliest_delivery: datetime,
    latest_delivery: datetime,
    customer_instructions: str | None = None,
) -> Order:
    payload = decode_tracking_token(token)
    order_id = payload.get("order_id")
    if not order_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid tracking token")
    order = await order_service.get_order(db, uuid.UUID(order_id))
    operations_policy = await operations_policy_service.get_operations_policy(db, order.tenant_id)
    if not operations_policy_service.can_customer_reschedule(operations_policy):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-service rescheduling is disabled")
    updated = await order_service.reschedule_order(
        db,
        order.id,
        earliest_delivery=earliest_delivery,
        latest_delivery=latest_delivery,
    )
    if customer_instructions and operations_policy_service.can_customer_update_instructions(operations_policy):
        updated.notes = customer_instructions
        await db.flush()
    return updated
