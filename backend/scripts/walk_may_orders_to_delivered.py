"""One-shot data generator: walk May 1–12 default-tenant orders through
the full lifecycle (PENDING → ASSIGNED → PICKED_UP → IN_TRANSIT →
DELIVERED) so the Layer 0/1/2 instrumentation captures real-execution
history for the dashboards.

Idempotent — orders already in terminal state are skipped.

Produces:
  * OrderStatusHistory       — one row per status transition
  * RouteStatusHistory       — PLANNED → ACTIVE → COMPLETED
  * StopStatusHistory        — PENDING → ARRIVED → COMPLETED w/ lat/lon
  * DispatchDecisionLog      — one row per (run, order) assignment
  * RoutePerformanceSnapshot — fuel + CO₂ + distance per completed route
                               (auto-materialized by _maybe_complete_route)
"""
from __future__ import annotations

import asyncio
import random
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

import app.models.vehicle_tag  # noqa: F401  — register association table

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.courier import Courier
from app.models.dispatch_decision_log import DispatchDecisionLog
from app.models.enums import (
    AvailabilityStatus,
    DispatchDecisionKind,
    OptimizationRunStatus,
    OptimizationRunType,
    OrderStatus,
    OrderType,
    RouteStatus,
    StopStatus,
    StopType,
)
from app.models.optimization_run import OptimizationRun
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.models.tenant import Tenant
from app.services.lifecycle_history_service import (
    apply_route_status_transition,
    apply_stop_status_transition,
)
from app.services.order_service import apply_order_status_transition
from app.services.route_performance_snapshot_service import (
    materialize_route_snapshot,
)


WINDOW_START = datetime(2026, 5, 1)
WINDOW_END = datetime(2026, 5, 13)  # exclusive — covers up to & incl. May 12

# Synthetic-but-plausible service times so per-stop arrival deltas
# don't all read 0.
PICKUP_SERVICE_S = (3 * 60, 8 * 60)
DELIVERY_SERVICE_S = (4 * 60, 12 * 60)
TRAVEL_BETWEEN_STOPS_S = (6 * 60, 22 * 60)


async def _get_default_tenant(db) -> Tenant:
    return (
        await db.execute(select(Tenant).where(Tenant.slug == "default"))
    ).scalar_one()


async def _get_eligible_couriers(db, tenant_id: uuid.UUID) -> list[Courier]:
    """Any courier on the tenant — including ON_BREAK and OFFLINE — so the
    backfill has enough vehicles for every day. The historical data we're
    seeding doesn't care about current availability."""
    return list(
        (
            await db.execute(
                select(Courier)
                .where(Courier.tenant_id == tenant_id)
                .options(selectinload(Courier.vehicle_spec))
            )
        )
        .scalars()
        .all()
    )


async def _ensure_run(
    db, tenant_id: uuid.UUID, op_date: date
) -> OptimizationRun:
    """Get-or-create a synthetic optimization_run for the day's backfill so
    dispatch_decision_log rows have an FK to point at."""
    task_id = f"backfill_may_{op_date.isoformat()}"
    existing = (
        await db.execute(
            select(OptimizationRun).where(OptimizationRun.task_id == task_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    run = OptimizationRun(
        tenant_id=tenant_id,
        task_id=task_id,
        run_type=OptimizationRunType.BATCH,
        status=OptimizationRunStatus.SUCCEEDED,
        routes_created=0,
        unassigned_count=0,
        solver_status="FEASIBLE",
        solver_kind="rich_alns",
        iterations=1500,
        feasibility_ratio=1.0,
        fallback_invoked=False,
        decomposition_used=False,
        warm_start_hit=False,
        time_budget_used_s=12.4,
        time_budget_allowed_s=60.0,
        started_at=datetime(op_date.year, op_date.month, op_date.day, 7, 45),
        finished_at=datetime(op_date.year, op_date.month, op_date.day, 7, 46),
    )
    db.add(run)
    await db.flush()
    return run


def _service_day(order: Order) -> date:
    """Pick the operational date for an order. scheduled_for_date if set,
    otherwise earliest_delivery's date, otherwise created_at's date."""
    if order.scheduled_for_date is not None:
        return order.scheduled_for_date
    if order.earliest_delivery is not None:
        return order.earliest_delivery.date()
    return order.created_at.date()


async def main() -> None:
    rng = random.Random(20260513)
    async with async_session() as db:
        tenant = await _get_default_tenant(db)
        couriers = await _get_eligible_couriers(db, tenant.id)
        if not couriers:
            print("no couriers — abort")
            return

        # ── 1. Load every non-terminal order in the window
        orders = list(
            (
                await db.execute(
                    select(Order).where(
                        Order.tenant_id == tenant.id,
                        Order.created_at >= WINDOW_START,
                        Order.created_at < WINDOW_END,
                        Order.status.in_(
                            [
                                OrderStatus.DRAFT,
                                OrderStatus.PENDING,
                                OrderStatus.ASSIGNED,
                                OrderStatus.PICKED_UP,
                                OrderStatus.IN_TRANSIT,
                                OrderStatus.RESCHEDULED,
                            ]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        print(f"loaded {len(orders)} non-terminal orders to advance")

        # ── 2. Bucket by service-day so we create one Route per (courier,
        # day). Order assignment is round-robin within each day.
        by_day: dict[date, list[Order]] = defaultdict(list)
        for o in orders:
            by_day[_service_day(o)].append(o)

        total_routes = 0
        total_assignments = 0
        total_history_rows = 0

        for op_date in sorted(by_day):
            day_orders = by_day[op_date]
            run = await _ensure_run(db, tenant.id, op_date)

            # ── 3. Round-robin assign orders to couriers; ensure routes
            # exist. Orders that already have route_id keep theirs.
            routes_for_day: dict[uuid.UUID, Route] = {}
            courier_load: dict[uuid.UUID, int] = {c.id: 0 for c in couriers}
            for o in day_orders:
                if o.route_id is not None:
                    rt = (
                        await db.execute(
                            select(Route)
                            .where(Route.id == o.route_id)
                            .options(selectinload(Route.stops))
                        )
                    ).scalar_one_or_none()
                    if rt is not None:
                        routes_for_day.setdefault(rt.courier_id, rt)
                        courier_load[rt.courier_id] = courier_load.get(rt.courier_id, 0) + 1
                        continue
                # No existing route — pick the courier with the fewest
                # orders today so far so the load spreads.
                courier = min(
                    couriers, key=lambda c: courier_load.get(c.id, 0)
                )
                courier_load[courier.id] = courier_load.get(courier.id, 0) + 1
                if courier.id not in routes_for_day:
                    rt = Route(
                        tenant_id=tenant.id,
                        courier_id=courier.id,
                        status=RouteStatus.PLANNED,
                        planned_distance_km=0.0,
                        optimization_run_id=run.id,
                        created_at=datetime(op_date.year, op_date.month, op_date.day, 7, 30),
                    )
                    db.add(rt)
                    await db.flush()
                    routes_for_day[courier.id] = rt
                    total_routes += 1
                rt = routes_for_day[courier.id]
                # Order → route assignment
                o.assigned_courier_id = courier.id
                o.route_id = rt.id
                o.scheduled_for_date = op_date

                # Decision log: one row per (run, order) — matches what a
                # real dispatch_batch would have written.
                # Score is the great-circle distance from depot to delivery
                # in meters; alternatives left empty.
                db.add(
                    DispatchDecisionLog(
                        tenant_id=tenant.id,
                        optimization_run_id=run.id,
                        order_id=o.id,
                        chosen_courier_id=courier.id,
                        chosen_score=float(
                            _haversine_m(
                                (o.pickup_lat, o.pickup_lon),
                                (o.delivery_lat, o.delivery_lon),
                            )
                        ),
                        kind=DispatchDecisionKind.BATCH_DISPATCH,
                        alternatives=None,
                        decision_reason=None,
                    )
                )
                total_assignments += 1

            # ── 4. Walk each route through start → arrive each stop →
            # complete each stop.
            for rt in routes_for_day.values():
                # Make sure stops exist for every order on this route.
                await _ensure_pickup_and_delivery_stops(db, rt, rng)
                # Reload stops via explicit query (async-safe).
                stops = list(
                    (
                        await db.execute(
                            select(RouteStop)
                            .where(RouteStop.route_id == rt.id)
                            .order_by(RouteStop.sequence_number)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not stops:
                    continue

                rt.started_at = datetime(op_date.year, op_date.month, op_date.day, 8, 0)
                if await apply_route_status_transition(
                    db,
                    rt,
                    RouteStatus.ACTIVE,
                    reason="Backfill: courier started route",
                ):
                    total_history_rows += 1

                # Walk stops in sequence_number order. Maintain a moving
                # "now" cursor so timestamps spread realistically.
                stop_cursor = datetime(
                    op_date.year, op_date.month, op_date.day, 8, 15
                )
                # Track each order's pickup-complete time so we can flip
                # IN_TRANSIT at the right moment.
                order_state: dict[uuid.UUID, OrderStatus] = {}
                for stop in sorted(stops, key=lambda s: s.sequence_number):
                    if stop.status == StopStatus.COMPLETED:
                        continue
                    arrival = stop_cursor + timedelta(
                        seconds=rng.randint(*TRAVEL_BETWEEN_STOPS_S)
                    )
                    stop.planned_arrival = stop.planned_arrival or arrival
                    stop.actual_arrival = arrival

                    if await apply_stop_status_transition(
                        db,
                        stop,
                        StopStatus.ARRIVED,
                        reason="Backfill: courier arrived",
                        lat=stop.lat,
                        lon=stop.lon,
                    ):
                        total_history_rows += 1

                    # Service time
                    service_s = rng.randint(*(
                        PICKUP_SERVICE_S
                        if stop.stop_type == StopType.PICKUP
                        else DELIVERY_SERVICE_S
                    ))
                    departure = arrival + timedelta(seconds=service_s)
                    if hasattr(stop, "planned_departure") and stop.planned_departure is None:
                        stop.planned_departure = departure

                    if await apply_stop_status_transition(
                        db,
                        stop,
                        StopStatus.COMPLETED,
                        reason=f"Backfill: {stop.stop_type.value.lower()} complete",
                        lat=stop.lat,
                        lon=stop.lon,
                    ):
                        total_history_rows += 1

                    # Order status transitions tied to stop type
                    if stop.order_id is not None:
                        o = next((x for x in day_orders if x.id == stop.order_id), None)
                        if o is None:
                            continue
                        if stop.stop_type == StopType.PICKUP:
                            # PENDING/DRAFT/RESCHEDULED → PENDING → ASSIGNED → PICKED_UP
                            if o.status in (OrderStatus.DRAFT, OrderStatus.RESCHEDULED):
                                # DRAFT and RESCHEDULED can only go to PENDING; bounce there first.
                                await apply_order_status_transition(
                                    db, o, OrderStatus.PENDING,
                                    reason="Backfill: re-activating for dispatch",
                                )
                            if o.status == OrderStatus.PENDING:
                                await apply_order_status_transition(
                                    db, o, OrderStatus.ASSIGNED,
                                    reason="Backfill: route start",
                                )
                            if o.status == OrderStatus.ASSIGNED:
                                await apply_order_status_transition(
                                    db, o, OrderStatus.PICKED_UP,
                                    reason="Backfill: pickup complete",
                                )
                                order_state[o.id] = OrderStatus.PICKED_UP
                        elif stop.stop_type == StopType.DELIVERY:
                            if o.status == OrderStatus.PICKED_UP:
                                await apply_order_status_transition(
                                    db, o, OrderStatus.IN_TRANSIT,
                                    reason="Backfill: en route to delivery",
                                )
                            if o.status == OrderStatus.IN_TRANSIT:
                                await apply_order_status_transition(
                                    db, o, OrderStatus.DELIVERED,
                                    reason="Backfill: delivered",
                                )

                    stop_cursor = departure

                # Route auto-completes when all stops are terminal
                if all(s.status in (StopStatus.COMPLETED, StopStatus.FAILED) for s in stops):
                    if await apply_route_status_transition(
                        db,
                        rt,
                        RouteStatus.COMPLETED,
                        reason="Backfill: all stops terminal",
                    ):
                        total_history_rows += 1
                    rt.completed_at = stop_cursor
                    # Estimate actual distance — for the backfill we use
                    # planned_distance_km × small variance, or a
                    # haversine sum if planned_distance_km is 0.
                    if not rt.actual_distance_km:
                        rt.actual_distance_km = (
                            rt.planned_distance_km
                            if rt.planned_distance_km and rt.planned_distance_km > 0
                            else round(
                                _estimate_route_distance_km(rt, day_orders), 2
                            )
                        )
                    if not rt.planned_distance_km:
                        rt.planned_distance_km = rt.actual_distance_km

            await db.flush()

            # ── 5. Materialize the per-route snapshot for every completed
            # route on this day (fuel + CO₂ + cost).
            for rt in routes_for_day.values():
                if rt.status == RouteStatus.COMPLETED:
                    try:
                        await materialize_route_snapshot(db, rt.id)
                    except Exception as exc:
                        print(f"  snapshot failed for {rt.id}: {exc}")

            await db.commit()
            print(
                f"  {op_date}: {len(day_orders)} orders · "
                f"{len(routes_for_day)} routes · committed"
            )

        print()
        print(
            f"done: {total_assignments} dispatches · "
            f"{total_routes} new routes · "
            f"{total_history_rows} lifecycle history rows written"
        )


async def _ensure_pickup_and_delivery_stops(
    db, rt: Route, rng: random.Random
) -> None:
    """For each order on this route, make sure the pickup + delivery
    RouteStops exist. We do this so non-route orders can still be walked
    through the lifecycle."""
    # Find orders that point at this route and don't have stops yet
    orders_here = list(
        (
            await db.execute(
                select(Order).where(Order.route_id == rt.id)
            )
        )
        .scalars()
        .all()
    )
    # Query existing stops explicitly to avoid async lazy-load.
    existing_stops = list(
        (
            await db.execute(
                select(RouteStop).where(RouteStop.route_id == rt.id)
            )
        )
        .scalars()
        .all()
    )
    existing_stop_orders = {
        s.order_id for s in existing_stops if s.order_id is not None
    }
    next_seq = max((s.sequence_number for s in existing_stops), default=-1) + 1
    plan_anchor = rt.created_at or datetime.utcnow()
    for o in orders_here:
        if o.id in existing_stop_orders:
            continue
        # Pickup
        pickup = RouteStop(
            tenant_id=rt.tenant_id,
            route_id=rt.id,
            order_id=o.id,
            sequence_number=next_seq,
            stop_type=StopType.PICKUP,
            lat=o.pickup_lat,
            lon=o.pickup_lon,
            address=o.pickup_address,
            planned_arrival=plan_anchor + timedelta(minutes=30 + 15 * (next_seq // 2)),
            status=StopStatus.PENDING,
        )
        db.add(pickup)
        next_seq += 1
        delivery = RouteStop(
            tenant_id=rt.tenant_id,
            route_id=rt.id,
            order_id=o.id,
            sequence_number=next_seq,
            stop_type=StopType.DELIVERY,
            lat=o.delivery_lat,
            lon=o.delivery_lon,
            address=o.delivery_address,
            planned_arrival=plan_anchor + timedelta(minutes=45 + 15 * (next_seq // 2)),
            status=StopStatus.PENDING,
        )
        db.add(delivery)
        next_seq += 1
    await db.flush()
    # Distance estimate so the snapshot has something to compute fuel on.
    if not rt.planned_distance_km or rt.planned_distance_km == 0:
        rt.planned_distance_km = round(
            _estimate_route_distance_km(rt, orders_here), 2
        )


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in meters."""
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1 = a
    lat2, lon2 = b
    if any(v is None for v in (lat1, lon1, lat2, lon2)):
        return 0.0
    R = 6_371_000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(h))


def _estimate_route_distance_km(
    rt: Route, orders_on_route: list[Order]
) -> float:
    """Synthetic but stable: sum of haversine pickup→delivery legs
    for each order. Good enough to seed snapshots."""
    total_m = 0.0
    for o in orders_on_route:
        total_m += _haversine_m(
            (o.pickup_lat, o.pickup_lon), (o.delivery_lat, o.delivery_lon)
        )
    return max(total_m / 1000.0, 1.0)


if __name__ == "__main__":
    asyncio.run(main())
