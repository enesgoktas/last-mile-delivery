"""
SLA Monitor Service — detects time-window violations on active routes.

Scans active routes and checks if pending delivery stops will miss their
order's latest_delivery window based on current planned ETAs. Creates
SLAAlert records for the dispatcher to act on.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import (
    RouteStatus,
    SLAAlertSeverity,
    SLAAlertStatus,
    StopStatus,
    StopType,
)
from app.models.order import Order
from app.models.route import Route
from app.models.sla_alert import SLAAlert
from app.utils.compat import UTC
from app.utils.tenant_context import tenant_filter

logger = logging.getLogger(__name__)

# Thresholds
WARNING_THRESHOLD_SECONDS = 600   # Alert as WARNING if ETA within 10 min of deadline
CRITICAL_THRESHOLD_SECONDS = 0    # Alert as CRITICAL if ETA past deadline


async def scan_active_routes(db: AsyncSession, principal=None) -> dict:
    """Scan all active routes for SLA violations.

    Returns summary of alerts created/updated.
    """
    query = (
        select(Route)
        .where(Route.status == RouteStatus.ACTIVE)
        .options(selectinload(Route.stops))
    )
    if principal is not None:
        query = query.where(tenant_filter(Route, principal))
    result = await db.execute(query)
    active_routes = list(result.scalars().unique().all())

    alerts_created = 0
    alerts_resolved = 0
    routes_scanned = 0
    critical_triggers: list[tuple] = []

    for route in active_routes:
        routes_scanned += 1
        pending_delivery_stops = [
            s for s in route.stops
            if s.stop_type == StopType.DELIVERY
            and s.status in (StopStatus.PENDING, StopStatus.ARRIVED)
            and s.order_id is not None
        ]

        if not pending_delivery_stops:
            continue

        order_ids = [s.order_id for s in pending_delivery_stops]
        order_result = await db.execute(
            select(Order).where(
                Order.id.in_(order_ids),
                tenant_filter(Order, route.tenant_id),
            )
        )
        orders_by_id = {o.id: o for o in order_result.scalars().all()}

        for stop in pending_delivery_stops:
            order = orders_by_id.get(stop.order_id)
            if not order or not order.latest_delivery:
                continue

            planned_arrival = stop.planned_arrival
            deadline = order.latest_delivery

            # Skip if delivery is already past (should be handled by stop completion)
            if planned_arrival is None:
                continue

            delay_seconds = int((planned_arrival - deadline).total_seconds())

            dedupe_key = _build_dedupe_key(route.id, stop.id)

            if delay_seconds > -WARNING_THRESHOLD_SECONDS:
                # ETA is within warning threshold or past deadline
                severity = (
                    SLAAlertSeverity.CRITICAL
                    if delay_seconds >= CRITICAL_THRESHOLD_SECONDS
                    else SLAAlertSeverity.WARNING
                )

                alert_type = "TIME_WINDOW_BREACH" if delay_seconds >= 0 else "TIME_WINDOW_AT_RISK"
                time_info = (
                    f"{delay_seconds}s past deadline"
                    if delay_seconds >= 0
                    else f"{abs(delay_seconds)}s until deadline"
                )
                message = (
                    f"Delivery for order {str(order.id)[:8]} "
                    f"(ETA {planned_arrival.strftime('%H:%M')}, "
                    f"deadline {deadline.strftime('%H:%M')}): {time_info}"
                )

                now_ts = datetime.now(UTC).replace(tzinfo=None)
                insert_values = {
                    "tenant_id": route.tenant_id,
                    "route_id": route.id,
                    "stop_id": stop.id,
                    "order_id": order.id,
                    "courier_id": route.courier_id,
                    "severity": severity,
                    "status": SLAAlertStatus.ACTIVE,
                    "alert_type": alert_type,
                    "message": message,
                    "estimated_delay_seconds": max(delay_seconds, 0),
                    "dedupe_key": dedupe_key,
                    "created_at": now_ts,
                    "updated_at": now_ts,
                }
                stmt = pg_insert(SLAAlert).values(**insert_values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["dedupe_key"],
                    set_={
                        "severity": severity,
                        "status": SLAAlertStatus.ACTIVE,
                        "alert_type": alert_type,
                        "message": message,
                        "estimated_delay_seconds": max(delay_seconds, 0),
                        "resolved_at": None,
                        "updated_at": now_ts,
                    },
                )
                await db.execute(stmt)

                # Detect whether this was an insert (new alert)
                existing_check = await db.execute(
                    select(SLAAlert.created_at).where(
                        SLAAlert.dedupe_key == dedupe_key,
                        tenant_filter(SLAAlert, route.tenant_id),
                    )
                )
                existing_row = existing_check.first()
                is_new = existing_row and abs((existing_row[0] - now_ts).total_seconds()) < 1
                if is_new:
                    alerts_created += 1

                if severity == SLAAlertSeverity.CRITICAL:
                    critical_triggers.append((route.id, route.courier_id))
            else:
                # ETA is well within window — resolve any existing alert
                existing = await db.execute(
                    select(SLAAlert).where(
                        SLAAlert.dedupe_key == dedupe_key,
                        SLAAlert.status.in_([SLAAlertStatus.ACTIVE, SLAAlertStatus.ACKNOWLEDGED]),
                        tenant_filter(SLAAlert, route.tenant_id),
                    )
                )
                existing_alert = existing.scalar_one_or_none()
                if existing_alert:
                    existing_alert.status = SLAAlertStatus.RESOLVED
                    existing_alert.resolved_at = datetime.now(UTC).replace(tzinfo=None)
                    alerts_resolved += 1

    await db.flush()

    # Fire re-optimization triggers for new/escalated CRITICAL alerts
    reopt_triggered = 0
    if critical_triggers:
        from app.services.reoptimization_triggers import on_sla_alert_critical
        seen_routes: set = set()
        for route_id, courier_id in critical_triggers:
            if route_id in seen_routes:
                continue
            seen_routes.add(route_id)
            try:
                await on_sla_alert_critical(db, route_id, courier_id)
                reopt_triggered += 1
            except Exception as exc:
                logger.warning("SLA critical re-opt trigger failed for route %s: %s",
                               route_id, exc)

    logger.info(
        "SLA scan: %d routes scanned, %d alerts created, %d alerts resolved, "
        "%d re-optimization triggers fired",
        routes_scanned, alerts_created, alerts_resolved, reopt_triggered,
    )

    return {
        "routes_scanned": routes_scanned,
        "alerts_created": alerts_created,
        "alerts_resolved": alerts_resolved,
        "reopt_triggered": reopt_triggered,
    }


async def acknowledge_alert(db: AsyncSession, alert_id, principal) -> SLAAlert:
    """Mark an alert as acknowledged by a dispatcher."""
    result = await db.execute(
        select(SLAAlert).where(
            SLAAlert.id == alert_id,
            tenant_filter(SLAAlert, principal),
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    if alert.status == SLAAlertStatus.ACTIVE:
        alert.status = SLAAlertStatus.ACKNOWLEDGED
        alert.acknowledged_at = datetime.now(UTC).replace(tzinfo=None)
        await db.flush()
    return alert


async def list_active_alerts(
    db: AsyncSession,
    principal,
    *,
    severity: SLAAlertSeverity | None = None,
    limit: int = 100,
    cursor: datetime | None = None,
) -> list[SLAAlert]:
    """List active and acknowledged alerts with cursor-based pagination.

    Pass *cursor* as the ``created_at`` of the last item returned to
    fetch the next page.
    """
    query = (
        select(SLAAlert)
        .where(
            SLAAlert.status.in_([SLAAlertStatus.ACTIVE, SLAAlertStatus.ACKNOWLEDGED]),
            tenant_filter(SLAAlert, principal),
        )
    )
    if severity:
        query = query.where(SLAAlert.severity == severity)
    if cursor:
        query = query.where(SLAAlert.created_at < cursor)
    query = query.order_by(SLAAlert.created_at.desc()).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


def _build_dedupe_key(route_id, stop_id) -> str:
    raw = f"sla:{route_id}:{stop_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
