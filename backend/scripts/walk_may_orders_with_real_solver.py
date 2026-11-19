"""Use the REAL Rich ALNS solver to dispatch May 1-12 orders, then walk
each algorithm-built route through the full lifecycle to DELIVERED.

Differs from the earlier round-robin script in one critical way: the
assignments come from `dispatch_range_service.dispatch_range` running
the actual Rich ALNS adapter. Decision-log rows, counterfactual baseline,
replay journal, and solver-metric columns all fill with REAL values
because dispatch_range already wires them in. We just call it.

The post-dispatch lifecycle walk (start_route → arrive → complete each
stop) still uses the Layer 0 helpers so OrderStatusHistory /
RouteStatusHistory / StopStatusHistory / RoutePerformanceSnapshot all
fill the same way they would in production.
"""
from __future__ import annotations

import asyncio
import random
import uuid
from datetime import date, datetime, timedelta

import app.models.vehicle_tag  # noqa: F401

from sqlalchemy import select

from app.database import async_session
from app.models.enums import (
    OptimizationRunStatus,
    OptimizationRunType,
    OrderStatus,
    RouteStatus,
    StopStatus,
    StopType,
)
from app.models.optimization_run import OptimizationRun
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.models.tenant import Tenant
from app.services.dispatch_range_service import dispatch_range
from app.services.lifecycle_history_service import (
    apply_route_status_transition,
    apply_stop_status_transition,
)
from app.services.order_service import apply_order_status_transition
from app.services.route_performance_snapshot_service import (
    materialize_route_snapshot,
)


WINDOW_START = date(2026, 5, 1)
WINDOW_END_INCLUSIVE = date(2026, 5, 12)

PICKUP_SERVICE_S = (3 * 60, 8 * 60)
DELIVERY_SERVICE_S = (4 * 60, 12 * 60)
TRAVEL_S = (6 * 60, 22 * 60)


async def main() -> None:
    rng = random.Random(20260513)

    async with async_session() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == "default"))
        ).scalar_one()
        tenant_id = tenant.id

    # Iterate day by day so each day's solve has its own anchor.
    current = WINDOW_START
    total_routes = 0
    total_assigned = 0
    total_unassigned = 0
    while current <= WINDOW_END_INCLUSIVE:
        date_from = datetime(current.year, current.month, current.day)
        date_to = date_from + timedelta(days=1)

        # Survey what's eligible for this day
        async with async_session() as db:
            n = await db.scalar(
                select(__import__("sqlalchemy").func.count())
                .select_from(Order)
                .where(
                    Order.tenant_id == tenant_id,
                    Order.status == OrderStatus.PENDING,
                    Order.earliest_delivery < date_to,
                    Order.latest_delivery >= date_from,
                )
            )
        print(f"\n{current.isoformat()}: {n} eligible PENDING orders")

        if not n:
            current += timedelta(days=1)
            continue

        # Pre-create the OptimizationRun row so dispatch_decision_log
        # FK constraints pass. Production normally does this via
        # `_start_run` in the Celery worker before invoking dispatch_range.
        run_id = uuid.uuid4()
        async with async_session() as db:
            db.add(
                OptimizationRun(
                    id=run_id,
                    tenant_id=tenant_id,
                    task_id=f"backfill_real_{current.isoformat()}",
                    run_type=OptimizationRunType.WEEKLY_BATCH,
                    status=OptimizationRunStatus.STARTED,
                    started_at=datetime.utcnow(),
                )
            )
            await db.commit()

        # Real Rich ALNS solve via dispatch_range, backfill mode bypasses
        # the latest_delivery > now filter.
        result = await dispatch_range(
            tenant_id=tenant_id,
            date_from=date_from,
            date_to=date_to,
            optimization_run_id=run_id,
            _backfill_mode=True,
        )
        print(
            f"  solver_status={result.get('solver_status')} · "
            f"routes={result.get('routes_created')} · "
            f"assigned={result.get('assigned_count')} · "
            f"unassigned={result.get('unassigned_count')} · "
            f"elapsed={result.get('solver_elapsed_s', 0):.2f}s"
        )
        total_routes += result.get("routes_created", 0)
        total_assigned += result.get("assigned_count", 0)
        total_unassigned += result.get("unassigned_count", 0)

        # ── Walk every route this run created through completion
        run_id_str = result.get("optimization_run_id")
        if run_id_str:
            await _complete_routes_for_run(uuid.UUID(run_id_str), rng)

        current += timedelta(days=1)

    print(
        f"\nTOTAL: {total_routes} routes · {total_assigned} assigned · "
        f"{total_unassigned} unassigned"
    )


async def _complete_routes_for_run(
    run_id: uuid.UUID, rng: random.Random
) -> None:
    async with async_session() as db:
        routes = list(
            (
                await db.execute(
                    select(Route).where(Route.optimization_run_id == run_id)
                )
            )
            .scalars()
            .all()
        )

        completed = 0
        for route in routes:
            # All routes from dispatch_range commit as PLANNED. Move
            # through ACTIVE → COMPLETED via the lifecycle helpers.
            stops = list(
                (
                    await db.execute(
                        select(RouteStop)
                        .where(RouteStop.route_id == route.id)
                        .order_by(RouteStop.sequence_number)
                    )
                )
                .scalars()
                .all()
            )
            if not stops:
                continue

            # Start the route (PLANNED → ACTIVE).
            await apply_route_status_transition(
                db,
                route,
                RouteStatus.ACTIVE,
                reason="Backfill: courier started route",
            )
            route.started_at = stops[0].planned_arrival - timedelta(minutes=15)

            # Walk each stop. Order transitions flow alongside.
            for stop in stops:
                arrival = (
                    stop.planned_arrival
                    + timedelta(seconds=rng.randint(*TRAVEL_S) - 5 * 60)
                )
                stop.actual_arrival = arrival
                await apply_stop_status_transition(
                    db,
                    stop,
                    StopStatus.ARRIVED,
                    reason="Backfill: arrived",
                    lat=stop.lat,
                    lon=stop.lon,
                )
                service_s = rng.randint(
                    *(
                        PICKUP_SERVICE_S
                        if stop.stop_type == StopType.PICKUP
                        else DELIVERY_SERVICE_S
                    )
                )
                await apply_stop_status_transition(
                    db,
                    stop,
                    StopStatus.COMPLETED,
                    reason=f"Backfill: {stop.stop_type.value.lower()} done",
                    lat=stop.lat,
                    lon=stop.lon,
                )
                # Order transitions tied to stop type
                if stop.order_id is not None:
                    o = (
                        await db.execute(
                            select(Order).where(Order.id == stop.order_id)
                        )
                    ).scalar_one_or_none()
                    if o is None:
                        continue
                    if stop.stop_type == StopType.PICKUP:
                        if o.status == OrderStatus.ASSIGNED:
                            await apply_order_status_transition(
                                db,
                                o,
                                OrderStatus.PICKED_UP,
                                reason="Backfill: pickup complete",
                            )
                    elif stop.stop_type == StopType.DELIVERY:
                        if o.status == OrderStatus.PICKED_UP:
                            await apply_order_status_transition(
                                db,
                                o,
                                OrderStatus.IN_TRANSIT,
                                reason="Backfill: en route to delivery",
                            )
                        if o.status == OrderStatus.IN_TRANSIT:
                            await apply_order_status_transition(
                                db,
                                o,
                                OrderStatus.DELIVERED,
                                reason="Backfill: delivered",
                            )
                # Bump the time cursor by service duration so subsequent
                # arrivals look monotonic.
                stop.actual_arrival = arrival + timedelta(seconds=service_s)

            # Mark route complete + materialize the snapshot.
            await apply_route_status_transition(
                db,
                route,
                RouteStatus.COMPLETED,
                reason="Backfill: all stops terminal",
            )
            route.completed_at = stops[-1].actual_arrival or datetime.utcnow()
            completed += 1

        await db.commit()

        # Materialize per-route snapshots after commit so they read the
        # latest actual_arrival / completed_at values.
        async with async_session() as fresh:
            for route in routes:
                try:
                    await materialize_route_snapshot(fresh, route.id)
                except Exception as exc:
                    print(f"    snapshot for {route.id} failed: {exc}")
            await fresh.commit()

        print(f"  completed {completed}/{len(routes)} routes")


if __name__ == "__main__":
    asyncio.run(main())
