from __future__ import annotations

import logging
import uuid
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.enums import OrderStatus, OrderType, RouteStatus, StopStatus, StopType
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.services.audit_service import log_audit_event
from app.services.lifecycle_history_service import apply_stop_status_transition
from app.services.order_service import apply_order_status_transition
from app.utils.compat import utcnow_naive
from app.utils.geo import haversine_meters
from app.utils.tenant_context import tenant_filter
from app.workers.task_dispatch import enqueue_task

_redis: aioredis.Redis | None = None
_redis_unavailable_logged = False
logger = logging.getLogger(__name__)


class _NoopRedis:
    async def get(self, *_args, **_kwargs):
        return None

    async def set(self, *_args, **_kwargs):
        return None

    async def delete(self, *_args, **_kwargs):
        return None


_noop_redis = _NoopRedis()


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def _get_redis_or_noop() -> aioredis.Redis | _NoopRedis:
    global _redis_unavailable_logged
    try:
        redis_client = await get_redis()
        if _redis_unavailable_logged:
            logger.info("Geofencing Redis connection restored.")
            _redis_unavailable_logged = False
        return redis_client
    except Exception:
        if not _redis_unavailable_logged:
            logger.warning("Geofencing Redis unavailable; using in-memory no-op state.")
            _redis_unavailable_logged = True
        return _noop_redis


def arrival_radius_for_order_type(order_type: OrderType) -> float:
    mapping = {
        OrderType.ON_DEMAND: settings.ARRIVAL_RADIUS_ON_DEMAND_METERS,
        OrderType.SCHEDULED: settings.ARRIVAL_RADIUS_SCHEDULED_METERS,
        OrderType.BULK: settings.ARRIVAL_RADIUS_BULK_METERS,
        OrderType.REVERSE: settings.ARRIVAL_RADIUS_REVERSE_METERS,
    }
    return float(mapping.get(order_type, settings.ARRIVAL_RADIUS_METERS))


def should_trigger_dwell_alert(
    *,
    first_exit_at: datetime | None,
    now: datetime,
    dwell_timeout_seconds: int,
    already_alerted: bool,
) -> bool:
    if already_alerted or first_exit_at is None:
        return False
    return (now - first_exit_at).total_seconds() >= dwell_timeout_seconds


def _first_exit_key(stop_id: uuid.UUID) -> str:
    return f"geofence:first_exit:{stop_id}"


def _alerted_key(stop_id: uuid.UUID) -> str:
    return f"geofence:dwell_alerted:{stop_id}"


def _inside_key(stop_id: uuid.UUID) -> str:
    return f"geofence:last_inside:{stop_id}"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


async def _emit_geofence_webhook(event_type: str, payload: dict) -> None:
    try:
        from app.workers.webhook_tasks import dispatch_webhook_event

        enqueue_task(dispatch_webhook_event, args=[event_type, payload])
    except Exception:
        logger.exception("Failed to enqueue geofence webhook event %s", event_type)


async def _emit_notification_event(event_type: str, payload: dict) -> None:
    try:
        from app.workers.notification_tasks import dispatch_order_notifications

        enqueue_task(dispatch_order_notifications, args=[event_type, payload])
    except Exception:
        logger.exception("Failed to enqueue geofence notification event %s", event_type)


async def process_location_update(
    db: AsyncSession,
    *,
    courier_id: uuid.UUID,
    lat: float,
    lon: float,
) -> dict:
    result = await db.execute(
        select(Route)
        .where(Route.courier_id == courier_id, Route.status == RouteStatus.ACTIVE)
        .order_by(Route.created_at.desc())
        .limit(1)
    )
    route = result.scalar_one_or_none()
    if route is None:
        return {"route_id": None, "stop_id": None, "distance_m": None, "auto_arrived": False, "dwell_alerted": False}

    stop_result = await db.execute(
        select(RouteStop)
        .where(
            RouteStop.route_id == route.id,
            RouteStop.status.in_([StopStatus.PENDING, StopStatus.ARRIVED]),
            tenant_filter(RouteStop, route.tenant_id),
        )
        .order_by(RouteStop.sequence_number.asc())
        .limit(1)
    )
    stop = stop_result.scalar_one_or_none()
    if stop is None:
        return {
            "route_id": str(route.id),
            "stop_id": None,
            "distance_m": None,
            "auto_arrived": False,
            "dwell_alerted": False,
        }

    order_result = await db.execute(
        select(Order).where(
            Order.id == stop.order_id,
            tenant_filter(Order, route.tenant_id),
        )
    )
    order = order_result.scalar_one_or_none()
    if order is None:
        return {
            "route_id": str(route.id),
            "stop_id": str(stop.id),
            "distance_m": None,
            "auto_arrived": False,
            "dwell_alerted": False,
        }

    radius = arrival_radius_for_order_type(order.type)
    distance_m = haversine_meters(lat, lon, stop.lat, stop.lon)
    now = utcnow_naive()

    r = await _get_redis_or_noop()
    first_exit_key = _first_exit_key(stop.id)
    alerted_key = _alerted_key(stop.id)
    inside_key = _inside_key(stop.id)

    auto_arrived = False
    dwell_alerted = False

    if distance_m <= radius:
        await r.set(inside_key, now.isoformat(), ex=24 * 3600)
        await r.delete(first_exit_key)
        await r.delete(alerted_key)

        if stop.status == StopStatus.PENDING:
            await apply_stop_status_transition(
                db,
                stop,
                StopStatus.ARRIVED,
                reason="Auto-arrived via geofence",
                lat=stop.lat,
                lon=stop.lon,
            )
            stop.actual_arrival = now
            await db.flush()
            auto_arrived = True

            if stop.stop_type == StopType.DELIVERY and order.status == OrderStatus.PICKED_UP:
                await apply_order_status_transition(
                    db,
                    order,
                    OrderStatus.IN_TRANSIT,
                    reason="Auto-arrived via geofence",
                )

            event_payload = {
                "courier_id": str(courier_id),
                "route_id": str(route.id),
                "stop_id": str(stop.id),
                "order_id": str(order.id),
                "tenant_id": str(order.tenant_id),
                "external_ref": order.external_ref,
                "status": stop.status.value,
                "timestamp": now.isoformat() + "Z",
                "distance_m": round(distance_m, 2),
                "stop_type": stop.stop_type.value,
                "customer_email": order.customer_email,
                "customer_phone": order.customer_phone,
                "assigned_courier_id": str(order.assigned_courier_id) if order.assigned_courier_id else None,
            }
            await _emit_geofence_webhook(
                "courier.arrived_delivery" if stop.stop_type == StopType.DELIVERY else "courier.arrived_pickup",
                event_payload,
            )
            if stop.stop_type == StopType.DELIVERY:
                await _emit_notification_event("order.arrived_delivery", event_payload)

            await log_audit_event(
                db,
                action="geofence.auto_arrive",
                tenant_id=order.tenant_id,
                resource_type="route_stop",
                resource_id=str(stop.id),
                details={
                    "courier_id": str(courier_id),
                    "route_id": str(route.id),
                    "distance_m": round(distance_m, 2),
                    "radius_m": radius,
                    "stop_type": stop.stop_type.value,
                },
            )
    else:
        if stop.status == StopStatus.ARRIVED:
            first_exit_raw = await r.get(first_exit_key)
            first_exit_at = _parse_dt(first_exit_raw)
            if first_exit_at is None:
                await r.set(first_exit_key, now.isoformat(), ex=24 * 3600)
            already_alerted = bool(await r.get(alerted_key))
            if should_trigger_dwell_alert(
                first_exit_at=first_exit_at,
                now=now,
                dwell_timeout_seconds=settings.GEOFENCE_DWELL_TIMEOUT_SECONDS,
                already_alerted=already_alerted,
            ):
                dwell_alerted = True
                await r.set(alerted_key, "1", ex=24 * 3600)
                payload = {
                    "courier_id": str(courier_id),
                    "route_id": str(route.id),
                    "stop_id": str(stop.id),
                    "order_id": str(order.id),
                    "tenant_id": str(order.tenant_id),
                    "timestamp": now.isoformat() + "Z",
                    "distance_m": round(distance_m, 2),
                    "radius_m": radius,
                    "event": "courier.departed_radius_without_completion",
                }
                await _emit_geofence_webhook("courier.dwell_alert", payload)
                await log_audit_event(
                    db,
                    action="geofence.dwell_alert",
                    tenant_id=order.tenant_id,
                    resource_type="route_stop",
                    resource_id=str(stop.id),
                    details=payload,
                )
        else:
            await r.delete(first_exit_key)
            await r.delete(alerted_key)

    return {
        "route_id": str(route.id),
        "stop_id": str(stop.id),
        "distance_m": round(distance_m, 2),
        "auto_arrived": auto_arrived,
        "dwell_alerted": dwell_alerted,
    }
