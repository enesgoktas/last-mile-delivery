"""Undo the round-robin backfill in walk_may_orders_to_delivered.py so
the real Rich ALNS solver can re-run cleanly.

Targets exclusively the artefacts the round-robin script created:
  * synthetic OptimizationRun rows (task_id LIKE 'backfill_may_%')
  * the 36 routes those runs created (and their stops + history)
  * the 136 DispatchDecisionLog rows pointing at those runs
  * RoutePerformanceSnapshot rows tied to those routes
  * OrderStatusHistory rows with reason LIKE 'Backfill:%'
  * Orders that were transitioned by the script get reset to PENDING
    with route_id / assigned_courier_id / scheduled_for_date cleared.

Real history rows untouched. Orders already in terminal state before
the round-robin (FAILED, CANCELLED, the pre-existing 424 DELIVERED)
are NOT modified.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import app.models.vehicle_tag  # noqa: F401

from sqlalchemy import delete, select, update

from app.database import async_session
from app.models.dispatch_decision_log import DispatchDecisionLog
from app.models.enums import OrderStatus
from app.models.optimization_counterfactual import OptimizationCounterfactual
from app.models.optimization_run import OptimizationRun
from app.models.optimization_run_replay import OptimizationRunReplay
from app.models.order import Order
from app.models.order_history import OrderStatusHistory
from app.models.route import Route, RouteStop
from app.models.route_history import RouteStatusHistory
from app.models.route_performance_snapshot import RoutePerformanceSnapshot
from app.models.stop_history import StopStatusHistory


BACKFILL_TASK_PREFIXES = ("backfill_may_", "backfill_real_")


async def main() -> None:
    async with async_session() as db:
        # 1. Identify the synthetic runs we created
        from sqlalchemy import or_

        run_ids = list(
            (
                await db.execute(
                    select(OptimizationRun.id).where(
                        or_(
                            *[
                                OptimizationRun.task_id.like(f"{p}%")
                                for p in BACKFILL_TASK_PREFIXES
                            ]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        print(f"synthetic runs to remove: {len(run_ids)}")

        # 2. Identify the routes those runs spawned
        route_ids = list(
            (
                await db.execute(
                    select(Route.id).where(
                        Route.optimization_run_id.in_(run_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        print(f"routes to remove: {len(route_ids)}")

        # 3. Identify the orders that were assigned to those routes
        order_ids = list(
            (
                await db.execute(
                    select(Order.id).where(
                        Order.route_id.in_(route_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        print(f"orders to reset: {len(order_ids)}")

        # 4. Identify the stops on those routes (for history cleanup)
        stop_ids = list(
            (
                await db.execute(
                    select(RouteStop.id).where(
                        RouteStop.route_id.in_(route_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        print(f"stops to remove: {len(stop_ids)}")

        # ── Delete history rows tied to the orders + stops + routes
        # Order status history written by the round-robin (reason marker)
        delete_order_hist = await db.execute(
            delete(OrderStatusHistory).where(
                OrderStatusHistory.reason.like("Backfill:%")
            )
        )
        print(f"  - OrderStatusHistory rows deleted: {delete_order_hist.rowcount}")

        # Stop status history
        if stop_ids:
            r = await db.execute(
                delete(StopStatusHistory).where(
                    StopStatusHistory.stop_id.in_(stop_ids)
                )
            )
            print(f"  - StopStatusHistory rows deleted: {r.rowcount}")

        # Route status history
        if route_ids:
            r = await db.execute(
                delete(RouteStatusHistory).where(
                    RouteStatusHistory.route_id.in_(route_ids)
                )
            )
            print(f"  - RouteStatusHistory rows deleted: {r.rowcount}")

        # Dispatch decision log
        if run_ids:
            r = await db.execute(
                delete(DispatchDecisionLog).where(
                    DispatchDecisionLog.optimization_run_id.in_(run_ids)
                )
            )
            print(f"  - DispatchDecisionLog rows deleted: {r.rowcount}")

        # Optimization counterfactual
        if run_ids:
            r = await db.execute(
                delete(OptimizationCounterfactual).where(
                    OptimizationCounterfactual.optimization_run_id.in_(run_ids)
                )
            )
            print(f"  - OptimizationCounterfactual rows deleted: {r.rowcount}")

        # Optimization run replay
        if run_ids:
            r = await db.execute(
                delete(OptimizationRunReplay).where(
                    OptimizationRunReplay.optimization_run_id.in_(run_ids)
                )
            )
            print(f"  - OptimizationRunReplay rows deleted: {r.rowcount}")

        # Route performance snapshot
        if route_ids:
            r = await db.execute(
                delete(RoutePerformanceSnapshot).where(
                    RoutePerformanceSnapshot.route_id.in_(route_ids)
                )
            )
            print(f"  - RoutePerformanceSnapshot rows deleted: {r.rowcount}")

        # ── Reset orders that the script touched
        if order_ids:
            r = await db.execute(
                update(Order)
                .where(Order.id.in_(order_ids))
                .values(
                    status=OrderStatus.PENDING,
                    route_id=None,
                    assigned_courier_id=None,
                    scheduled_for_date=None,
                    failed_at=None,
                )
            )
            print(f"  - Orders reset to PENDING: {r.rowcount}")

        # ── Drop the stops, routes, and synthetic runs in dependency order
        if stop_ids:
            r = await db.execute(
                delete(RouteStop).where(RouteStop.id.in_(stop_ids))
            )
            print(f"  - RouteStop rows deleted: {r.rowcount}")
        if route_ids:
            r = await db.execute(
                delete(Route).where(Route.id.in_(route_ids))
            )
            print(f"  - Route rows deleted: {r.rowcount}")
        if run_ids:
            r = await db.execute(
                delete(OptimizationRun).where(
                    OptimizationRun.id.in_(run_ids)
                )
            )
            print(f"  - OptimizationRun rows deleted: {r.rowcount}")

        await db.commit()
        print("rollback complete")


if __name__ == "__main__":
    asyncio.run(main())
