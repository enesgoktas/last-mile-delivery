"""
Re-optimization trigger service — fires re-optimization on real-time events.

Supports multiple trigger types beyond just courier-offline:
  - New urgent/high-priority order arrives
  - Stop completion reveals significant delay
  - Route falls behind schedule (SLA alert)
  - Manual dispatcher trigger

Each trigger respects the existing cooldown/lock mechanism to prevent
excessive re-optimization.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import (
    OrderPriority,
    OrderStatus,
    RouteStatus,
    StopStatus,
)
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.utils.compat import utcnow_naive
from app.workers.task_dispatch import enqueue_task

logger = logging.getLogger(__name__)

# Delay threshold: if a stop arrives more than this many seconds late,
# trigger re-optimization for remaining stops
LATE_ARRIVAL_THRESHOLD_SECONDS = 600  # 10 minutes


async def on_new_urgent_order(db: AsyncSession, order: Order) -> dict:
    """Trigger when a new urgent/high-priority order is created.

    Immediately queues insertion dispatch instead of waiting for next
    batch cycle.
    """
    if order.priority not in (OrderPriority.URGENT, OrderPriority.HIGH):
        return {"triggered": False, "reason": "not_high_priority"}

    if order.status != OrderStatus.PENDING:
        return {"triggered": False, "reason": "not_pending"}

    from app.workers.optimization_tasks import run_insertion_dispatch
    enqueue_task(run_insertion_dispatch, args=[str(order.id), str(order.tenant_id)])

    logger.info(
        "Re-optimization triggered: new %s order %s queued for immediate dispatch",
        order.priority.value, str(order.id)[:8],
    )
    return {"triggered": True, "trigger_type": "urgent_order", "order_id": str(order.id)}


async def on_late_stop_completion(
    db: AsyncSession,
    route_id,
    stop: RouteStop,
) -> dict:
    """Trigger when a stop is completed significantly late.

    If the actual arrival is far behind the planned arrival, the remaining
    stops on the route need re-timing and potentially re-assignment.
    """
    if stop.actual_arrival is None or stop.planned_arrival is None:
        return {"triggered": False, "reason": "no_arrival_data"}

    delay_seconds = int((stop.actual_arrival - stop.planned_arrival).total_seconds())

    if delay_seconds < LATE_ARRIVAL_THRESHOLD_SECONDS:
        return {"triggered": False, "reason": "within_threshold", "delay_s": delay_seconds}

    # Re-time the remaining stops on this route
    from app.services.route_service import retime_route_stops
    try:
        await retime_route_stops(db, route_id)
    except Exception as exc:
        logger.warning("Re-timing after late stop failed: %s", exc)

    logger.info(
        "Re-optimization triggered: stop %s on route %s was %ds late, "
        "route re-timed",
        str(stop.id)[:8], str(route_id)[:8], delay_seconds,
    )
    return {
        "triggered": True,
        "trigger_type": "late_stop",
        "delay_s": delay_seconds,
        "route_id": str(route_id),
    }


async def on_sla_alert_critical(db: AsyncSession, route_id, courier_id) -> dict:
    """Trigger when a critical SLA alert is raised for a route.

    Queues re-optimization for the affected courier to try to salvage
    remaining deliveries.
    """
    from app.workers.optimization_tasks import run_reoptimization

    # Only trigger if route is still active
    result = await db.execute(
        select(Route).where(Route.id == route_id)
    )
    route = result.scalar_one_or_none()
    if not route or route.status != RouteStatus.ACTIVE:
        return {"triggered": False, "reason": "route_not_active"}

    enqueue_task(run_reoptimization, args=[str(courier_id), str(route.tenant_id)])

    logger.info(
        "Re-optimization triggered: critical SLA alert on route %s, "
        "courier %s queued for re-optimization",
        str(route_id)[:8], str(courier_id)[:8],
    )
    return {
        "triggered": True,
        "trigger_type": "sla_critical",
        "route_id": str(route_id),
        "courier_id": str(courier_id),
    }


async def check_route_schedule_drift(db: AsyncSession) -> dict:
    """Scan active routes for significant schedule drift.

    Called periodically to detect routes that have fallen behind
    schedule and may need intervention.

    Eagerly loads stops to avoid N+1 queries. Each re-timing runs in its
    own savepoint to prevent a single failure from poisoning the session.
    """
    result = await db.execute(
        select(Route)
        .where(Route.status == RouteStatus.ACTIVE)
        .options(selectinload(Route.stops))
    )
    active_routes = list(result.scalars().unique().all())

    drifted = 0
    retimed = 0
    now = utcnow_naive()
    drift_cutoff = now - timedelta(seconds=LATE_ARRIVAL_THRESHOLD_SECONDS)

    drifted_route_ids: list = []
    for route in active_routes:
        pending_stops = sorted(
            [s for s in route.stops if s.status == StopStatus.PENDING],
            key=lambda s: s.sequence_number,
        )
        if not pending_stops:
            continue
        next_stop = pending_stops[0]
        if next_stop.planned_arrival and next_stop.planned_arrival < drift_cutoff:
            drifted += 1
            drifted_route_ids.append(route.id)

    from app.services.route_service import retime_route_stops
    for route_id in drifted_route_ids:
        try:
            async with db.begin_nested():
                await retime_route_stops(db, route_id)
            retimed += 1
        except Exception as exc:
            logger.warning("Schedule drift re-timing failed for route %s: %s",
                           route_id, exc)

    if drifted > 0:
        logger.info(
            "Schedule drift check: %d routes drifted, %d re-timed",
            drifted, retimed,
        )

    return {
        "routes_checked": len(active_routes),
        "routes_drifted": drifted,
        "routes_retimed": retimed,
    }
