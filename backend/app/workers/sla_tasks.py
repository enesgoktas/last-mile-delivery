"""Celery tasks for SLA monitoring."""
import asyncio
import logging

from app import database
from app.utils.compat import utcnow_naive
from app.workers.celery_app import celery_app
from app.workers.task_request_context import bind_task_request_id

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from a sync Celery task with NullPool sessions."""
    database._session_factory_override.set(database.celery_async_session)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.workers.sla_tasks.scan_sla_violations", bind=True, max_retries=1)
def scan_sla_violations(self):
    """Periodic task: scan active routes for SLA/time-window violations."""
    with bind_task_request_id(self.request):
        try:
            result = _run_async(_run_scan())
            logger.info("SLA scan completed: %s", result)
            return result
        except Exception as exc:
            logger.exception("SLA scan failed: %s", exc)
            raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.sla_tasks.check_schedule_drift", bind=True, max_retries=1)
def check_schedule_drift(self):
    """Periodic task: detect and correct schedule drift on active routes."""
    with bind_task_request_id(self.request):
        try:
            result = _run_async(_run_drift_check())
            logger.info("Schedule drift check completed: %s", result)
            return result
        except Exception as exc:
            logger.exception("Schedule drift check failed: %s", exc)
            raise self.retry(exc=exc, countdown=30)


async def _run_scan() -> dict:
    from app.services.sla_monitor_service import scan_active_routes
    async with database.celery_async_session() as db:
        result = await scan_active_routes(db)
        await db.commit()
        return result


async def _run_drift_check() -> dict:
    from app.services.reoptimization_triggers import check_route_schedule_drift
    async with database.celery_async_session() as db:
        result = await check_route_schedule_drift(db)
        await db.commit()
        return result


@celery_app.task(name="app.workers.sla_tasks.expire_overdue_orders", bind=True, max_retries=1)
def expire_overdue_orders(self):
    """Periodic task: expire PENDING orders whose latest_delivery has passed,
    and unassign / abort routes for ASSIGNED orders past their delivery window."""
    with bind_task_request_id(self.request):
        try:
            result = _run_async(_run_expire_overdue())
            logger.info("Expired order sweep completed: %s", result)
            return result
        except Exception as exc:
            logger.exception("Expired order sweep failed: %s", exc)
            raise self.retry(exc=exc, countdown=60)


async def _run_expire_overdue() -> dict:
    from sqlalchemy import select as sa_select
    from sqlalchemy.orm import selectinload

    from app.models.enums import (
        OrderStatus,
        RouteStatus,
        StopStatus,
    )
    from app.models.order import Order
    from app.models.route import Route
    from app.services.order_service import apply_order_status_transition

    now = utcnow_naive()
    expired_pending = 0
    failed_assigned = 0
    routes_aborted = 0

    async with database.celery_async_session() as db:
        pending_result = await db.execute(
            sa_select(Order).where(
                Order.status == OrderStatus.PENDING,
                Order.latest_delivery < now,
            ).limit(500)
        )
        for order in pending_result.scalars().all():
            await apply_order_status_transition(
                db, order, OrderStatus.EXPIRED,
                reason="Delivery window expired",
            )
            expired_pending += 1

        assigned_result = await db.execute(
            sa_select(Order).where(
                Order.status == OrderStatus.ASSIGNED,
                Order.latest_delivery < now,
            ).limit(500)
        )
        overdue_assigned = list(assigned_result.scalars().all())

        route_ids_to_check: set = set()
        for order in overdue_assigned:
            await apply_order_status_transition(
                db, order, OrderStatus.PENDING,
                reason="Delivery window expired — unassigning for review",
            )
            order.assigned_courier_id = None
            if order.route_id:
                route_ids_to_check.add(order.route_id)
                order.route_id = None
            failed_assigned += 1

        if route_ids_to_check:
            route_result = await db.execute(
                sa_select(Route)
                .where(Route.id.in_(route_ids_to_check))
                .options(selectinload(Route.stops))
            )
            for route in route_result.scalars().unique().all():
                from app.services.lifecycle_history_service import (
                    apply_route_status_transition,
                    apply_stop_status_transition,
                )

                pending_stops = [
                    s for s in route.stops
                    if s.status in (StopStatus.PENDING, StopStatus.ARRIVED)
                    and s.stop_type not in ("DEPOT_RETURN", "DEPOT_RELOAD")
                ]
                if not pending_stops:
                    await apply_route_status_transition(
                        db,
                        route,
                        RouteStatus.ABORTED,
                        reason="SLA scan — route abandoned with no pending work",
                    )
                    routes_aborted += 1
                else:
                    for stop in route.stops:
                        if (
                            stop.order_id is not None
                            and stop.status in (StopStatus.PENDING, StopStatus.ARRIVED)
                        ):
                            order_ids = {o.id for o in overdue_assigned}
                            if stop.order_id in order_ids:
                                await apply_stop_status_transition(
                                    db,
                                    stop,
                                    StopStatus.FAILED,
                                    reason="Delivery window expired",
                                )
                                stop.notes = "Delivery window expired"

        await db.commit()

    return {
        "expired_pending": expired_pending,
        "failed_assigned": failed_assigned,
        "routes_aborted": routes_aborted,
    }
