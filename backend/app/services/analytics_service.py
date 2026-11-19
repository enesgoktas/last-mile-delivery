from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

from sqlalchemy import String, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.algorithms.osrm_client import get_osrm_metrics_snapshot
from app.models.enums import OptimizationRunStatus, OrderStatus, OrderType, SLAAlertSeverity
from app.models.eta_prediction_log import ETAPredictedBy, ETAPredictionLog
from app.models.optimization_run import OptimizationRun
from app.models.order import Order
from app.models.plan_stability_score import PlanStabilityScore
from app.models.route_performance_snapshot import RoutePerformanceSnapshot
from app.models.route_scenario import RouteScenario, RouteScenarioComparison
from app.models.sla_alert import SLAAlert
from app.services.route_scenario_service import HUMAN_DISPATCHER_BASELINE_KEY
from app.utils.perf_metrics import perf_metrics
from app.utils.tenant_context import tenant_filter

EXCEPTION_CENTER_LIMIT = 5


def _alert_to_summary(alert: SLAAlert) -> dict:
    return {
        "id": alert.id,
        "route_id": alert.route_id,
        "stop_id": alert.stop_id,
        "order_id": alert.order_id,
        "courier_id": alert.courier_id,
        "severity": alert.severity.value if hasattr(alert.severity, "value") else alert.severity,
        "status": alert.status.value if hasattr(alert.status, "value") else alert.status,
        "alert_type": alert.alert_type,
        "message": alert.message,
        "estimated_delay_seconds": alert.estimated_delay_seconds,
        "created_at": alert.created_at.isoformat(),
        "updated_at": alert.updated_at.isoformat(),
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
    }


async def _scalar_count(db: AsyncSession, query) -> int:
    result = await db.execute(query)
    return int(result.scalar_one() or 0)


async def get_summary(
    db: AsyncSession,
    principal,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    query = select(Order).where(tenant_filter(Order, principal))
    if date_from:
        query = query.where(Order.created_at >= date_from)
    if date_to:
        query = query.where(Order.created_at <= date_to)

    sub = query.subquery()

    result = await db.execute(
        select(
            func.count(sub.c.id),
            func.sum(case((sub.c.status == OrderStatus.DELIVERED, 1), else_=0)),
            func.sum(case((sub.c.status == OrderStatus.FAILED, 1), else_=0)),
            func.sum(case((sub.c.status == OrderStatus.PENDING, 1), else_=0)),
            func.sum(
                case(
                    (
                        (sub.c.status == OrderStatus.DELIVERED)
                        & (sub.c.updated_at <= sub.c.latest_delivery),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
    )
    total, delivered, failed, pending, on_time = result.one()

    total = int(total or 0)
    delivered = int(delivered or 0)
    failed = int(failed or 0)
    pending = int(pending or 0)
    on_time = int(on_time or 0)

    completion_rate = (delivered / total) if total else 0.0
    failed_rate = (failed / total) if total else 0.0
    on_time_rate = (on_time / delivered) if delivered else 0.0

    # Full status breakdown — single GROUP BY query, then fill zeros for
    # missing statuses so the frontend can iterate the enum directly.
    breakdown_result = await db.execute(
        select(sub.c.status, func.count(sub.c.id)).group_by(sub.c.status)
    )
    status_breakdown: dict[str, int] = {s.value: 0 for s in OrderStatus}
    for status, count in breakdown_result.all():
        key = status.value if hasattr(status, "value") else str(status)
        status_breakdown[key] = int(count or 0)

    return {
        "total_orders": total,
        "delivered_orders": delivered,
        "failed_orders": failed,
        "pending_orders": pending,
        "completion_rate": round(completion_rate, 4),
        "failed_rate": round(failed_rate, 4),
        "on_time_rate": round(on_time_rate, 4),
        "status_breakdown": status_breakdown,
    }


async def get_courier_table(
    db: AsyncSession,
    principal,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict]:
    query = select(
        Order.assigned_courier_id,
        func.count(Order.id).label("assigned_orders"),
        func.sum(case((Order.status == OrderStatus.DELIVERED, 1), else_=0)).label("delivered_orders"),
        func.sum(case((Order.status == OrderStatus.FAILED, 1), else_=0)).label("failed_orders"),
    ).where(
        Order.assigned_courier_id.is_not(None),
        tenant_filter(Order, principal),
    )

    if date_from:
        query = query.where(Order.created_at >= date_from)
    if date_to:
        query = query.where(Order.created_at <= date_to)

    query = query.group_by(Order.assigned_courier_id)

    result = await db.execute(query)
    rows = []
    for courier_id, assigned, delivered, failed in result.all():
        rows.append(
            {
                "courier_id": courier_id,
                "assigned_orders": int(assigned or 0),
                "delivered_orders": int(delivered or 0),
                "failed_orders": int(failed or 0),
            }
        )
    return rows


async def get_exception_center_summary(
    db: AsyncSession,
    principal,
    *,
    limit: int = EXCEPTION_CENTER_LIMIT,
) -> dict:
    pending_run_result = await db.execute(
        select(OptimizationRun)
        .where(
            OptimizationRun.status == OptimizationRunStatus.PENDING_APPROVAL,
            tenant_filter(OptimizationRun, principal),
        )
        .order_by(OptimizationRun.started_at.desc())
        .limit(1)
    )
    pending_run = pending_run_result.scalar_one_or_none()

    critical_alerts_result = await db.execute(
        select(SLAAlert)
        .where(
            SLAAlert.status == "ACTIVE",
            SLAAlert.severity == SLAAlertSeverity.CRITICAL,
            tenant_filter(SLAAlert, principal),
        )
        .order_by(SLAAlert.created_at.desc())
        .limit(limit)
    )
    critical_alerts = list(critical_alerts_result.scalars().all())

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    failed_orders_result = await db.execute(
        select(Order)
        .where(
            Order.status == OrderStatus.FAILED,
            Order.updated_at >= today_start,
            tenant_filter(Order, principal),
        )
        .order_by(Order.failed_at.desc().nullslast(), Order.updated_at.desc())
        .limit(limit)
    )
    failed_orders = list(failed_orders_result.scalars().all())

    unassigned_orders_result = await db.execute(
        select(Order)
        .where(
            Order.status == OrderStatus.PENDING,
            Order.assigned_courier_id.is_(None),
            Order.earliest_delivery < today_end,
            Order.latest_delivery >= today_start,
            tenant_filter(Order, principal),
        )
        .order_by(Order.latest_delivery.asc(), Order.created_at.asc())
        .limit(limit)
    )
    unassigned_orders = list(unassigned_orders_result.scalars().all())

    pending_approval_runs = await _scalar_count(
        db,
        select(func.count(OptimizationRun.id)).where(
            OptimizationRun.status == OptimizationRunStatus.PENDING_APPROVAL,
            tenant_filter(OptimizationRun, principal),
        ),
    )
    critical_alert_count = await _scalar_count(
        db,
        select(func.count(SLAAlert.id)).where(
            SLAAlert.status == "ACTIVE",
            SLAAlert.severity == SLAAlertSeverity.CRITICAL,
            tenant_filter(SLAAlert, principal),
        ),
    )
    failed_order_count = await _scalar_count(
        db,
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.FAILED,
            Order.updated_at >= today_start,
            tenant_filter(Order, principal),
        ),
    )
    unassigned_order_count = await _scalar_count(
        db,
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.PENDING,
            Order.assigned_courier_id.is_(None),
            Order.earliest_delivery < today_end,
            Order.latest_delivery >= today_start,
            tenant_filter(Order, principal),
        ),
    )

    return {
        "counts": {
            "pending_approval_runs": pending_approval_runs,
            "critical_alerts": critical_alert_count,
            "failed_orders": failed_order_count,
            "unassigned_orders": unassigned_order_count,
            "total_open_items": (
                pending_approval_runs
                + critical_alert_count
                + failed_order_count
                + unassigned_order_count
            ),
        },
        "pending_run": pending_run,
        "critical_alerts": [_alert_to_summary(alert) for alert in critical_alerts],
        "failed_orders": failed_orders,
        "unassigned_orders": unassigned_orders,
    }


async def export_orders_csv(
    db: AsyncSession,
    principal,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> str:
    query = (
        select(Order)
        .where(tenant_filter(Order, principal))
        .order_by(Order.created_at.desc())
        # Hard cap to prevent OOM on large tenants; callers can paginate using
        # date_from/date_to or request multiple export chunks.
        .limit(50_000)
    )
    if date_from:
        query = query.where(Order.created_at >= date_from)
    if date_to:
        query = query.where(Order.created_at <= date_to)

    result = await db.execute(query)
    orders = list(result.scalars().all())

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "order_id",
            "type",
            "status",
            "priority",
            "assigned_courier_id",
            "created_at",
            "updated_at",
            "latest_delivery",
            "attempt_count",
        ]
    )
    for order in orders:
        writer.writerow(
            [
                order.id,
                order.type.value,
                order.status.value,
                order.priority.value,
                order.assigned_courier_id,
                order.created_at.isoformat(),
                order.updated_at.isoformat(),
                order.latest_delivery.isoformat(),
                order.attempt_count,
            ]
        )

    return output.getvalue()


def get_runtime_performance_snapshot() -> dict:
    return {
        "dispatch_batch_latency_ms": perf_metrics.summary("dispatch_batch_latency_ms"),
        "dispatch_on_demand_latency_ms": perf_metrics.summary("dispatch_on_demand_latency_ms"),
        "eta_compute_latency_ms": perf_metrics.summary("eta_compute_latency_ms"),
        "eta_error_delta_ms": perf_metrics.summary("eta_error_delta_ms"),
        "osrm": get_osrm_metrics_snapshot(),
    }


def get_runtime_performance_metrics_text() -> str:
    return perf_metrics.export_prometheus(namespace="lastmile")


# ── Phase 2: ride analytics ────────────────────────────────────────────


_OPEN_RIDE_STATUSES = (
    OrderStatus.PENDING,
    OrderStatus.ASSIGNED,
    OrderStatus.PICKED_UP,
    OrderStatus.IN_TRANSIT,
)


async def get_ride_analytics(
    db: AsyncSession,
    principal,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    top_passenger_limit: int = 10,
) -> dict:
    """Aggregated stats for RIDE orders. Caller is assumed to have
    `rides_enabled` already; the endpoint layer enforces that.
    """
    base = select(Order).where(
        tenant_filter(Order, principal),
        Order.type == OrderType.RIDE,
    )
    if date_from:
        base = base.where(Order.created_at >= date_from)
    if date_to:
        base = base.where(Order.created_at <= date_to)

    sub = base.subquery()

    # Single roundtrip for status totals + airport count.
    result = await db.execute(
        select(
            func.count(sub.c.id),
            func.sum(case((sub.c.status == OrderStatus.PENDING, 1), else_=0)),
            func.sum(case((sub.c.status == OrderStatus.ASSIGNED, 1), else_=0)),
            func.sum(case((sub.c.status == OrderStatus.IN_TRANSIT, 1), else_=0)),
            func.sum(case((sub.c.status == OrderStatus.DELIVERED, 1), else_=0)),
            func.sum(case((sub.c.status == OrderStatus.CANCELLED, 1), else_=0)),
            func.sum(case((sub.c.status == OrderStatus.FAILED, 1), else_=0)),
            # JSONB stores `None` as the literal `'null'::jsonb` by default
            # (none_as_null=False), so check for both forms of "no flight".
            func.sum(
                case(
                    (
                        (sub.c.flight_info.is_not(None))
                        & (cast(sub.c.flight_info, String) != "null"),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
    )
    total, pending, assigned, in_transit, delivered, cancelled, failed, airport = (
        result.one()
    )

    # Per-passenger summary, top N.
    passenger_rows = (
        await db.execute(
            select(
                Order.passenger_name,
                func.count(Order.id).label("ride_count"),
                func.sum(case((Order.status == OrderStatus.DELIVERED, 1), else_=0)).label("completed"),
                func.sum(case((Order.status == OrderStatus.CANCELLED, 1), else_=0)).label("cancelled"),
            )
            .where(
                tenant_filter(Order, principal),
                Order.type == OrderType.RIDE,
                Order.passenger_name.is_not(None),
                *(
                    [Order.created_at >= date_from] if date_from else []
                ),
                *(
                    [Order.created_at <= date_to] if date_to else []
                ),
            )
            .group_by(Order.passenger_name)
            .order_by(func.count(Order.id).desc())
            .limit(top_passenger_limit)
        )
    ).all()

    upcoming = (
        int(pending or 0) + int(assigned or 0) + int(in_transit or 0)
    )

    return {
        "total_rides": int(total or 0),
        "completed_rides": int(delivered or 0),
        "cancelled_rides": int(cancelled or 0),
        "upcoming_rides": upcoming,
        "by_status": {
            "pending": int(pending or 0),
            "assigned": int(assigned or 0),
            "in_transit": int(in_transit or 0),
            "delivered": int(delivered or 0),
            "cancelled": int(cancelled or 0),
            "failed": int(failed or 0),
        },
        "top_passengers": [
            {
                "passenger_name": row.passenger_name,
                "ride_count": int(row.ride_count or 0),
                "completed_count": int(row.completed or 0),
                "cancelled_count": int(row.cancelled or 0),
            }
            for row in passenger_rows
        ],
        "airport_pickups": int(airport or 0),
    }


# ── Phase 2: fleet utilization ─────────────────────────────────────────


async def get_fleet_utilization(
    db: AsyncSession,
    principal,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    """Per-courier route count, distance, working hours, and order count.

    Hours are derived from `route.completed_at - route.started_at` summed
    across completed routes. Routes still in flight (no completed_at) are
    skipped from the hours total. Distances come from `actual_distance_km`
    when populated, otherwise `planned_distance_km` as a fallback.
    """
    from app.models.route import Route

    base = select(Route).where(
        tenant_filter(Route, principal),
    )
    if date_from:
        base = base.where(Route.created_at >= date_from)
    if date_to:
        base = base.where(Route.created_at <= date_to)

    # Per-courier aggregates. EXTRACT(EPOCH FROM ...) is portable enough
    # for both Postgres and SQLite (vitest tests don't hit this anyway).
    duration_s = func.extract("epoch", Route.completed_at - Route.started_at)

    routes_query = (
        select(
            Route.courier_id,
            func.count(Route.id).label("route_count"),
            func.coalesce(
                func.sum(
                    case(
                        (Route.actual_distance_km.is_not(None), Route.actual_distance_km),
                        else_=Route.planned_distance_km,
                    )
                ),
                0,
            ).label("distance_km"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Route.started_at.is_not(None))
                            & (Route.completed_at.is_not(None)),
                            duration_s,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("duration_s"),
        )
        .where(tenant_filter(Route, principal))
        .group_by(Route.courier_id)
    )
    if date_from:
        routes_query = routes_query.where(Route.created_at >= date_from)
    if date_to:
        routes_query = routes_query.where(Route.created_at <= date_to)

    courier_rows = (await db.execute(routes_query)).all()

    # Order count is computed separately via Order.assigned_courier_id so
    # we don't double-count when multiple orders share a route.
    orders_query = (
        select(
            Order.assigned_courier_id,
            func.count(Order.id).label("order_count"),
        )
        .where(
            tenant_filter(Order, principal),
            Order.assigned_courier_id.is_not(None),
        )
        .group_by(Order.assigned_courier_id)
    )
    if date_from:
        orders_query = orders_query.where(Order.created_at >= date_from)
    if date_to:
        orders_query = orders_query.where(Order.created_at <= date_to)
    order_count_by_courier = {
        row.assigned_courier_id: int(row.order_count or 0)
        for row in (await db.execute(orders_query)).all()
    }

    rows = []
    total_route_count = 0
    total_distance = 0.0
    total_hours = 0.0
    for r in courier_rows:
        hours = float(r.duration_s or 0) / 3600.0
        distance = float(r.distance_km or 0)
        rc = int(r.route_count or 0)
        rows.append(
            {
                "courier_id": str(r.courier_id),
                "route_count": rc,
                "total_distance_km": round(distance, 2),
                "total_hours": round(hours, 2),
                "order_count": order_count_by_courier.get(r.courier_id, 0),
            }
        )
        total_route_count += rc
        total_distance += distance
        total_hours += hours

    return {
        "rows": sorted(rows, key=lambda r: -r["total_distance_km"]),
        "total_route_count": total_route_count,
        "total_distance_km": round(total_distance, 2),
        "total_hours": round(total_hours, 2),
    }


# ── Optimization savings (baseline vs selected scenario) ───────────────


def _scenario_summary(scenario: RouteScenario | None) -> dict:
    """Read the persisted summary off a scenario, defaulting missing fields.

    The summary JSONB is shaped by `RouteScenarioSummary` (in
    `app/schemas/route_scenario.py`); we only pull the fields we need for the
    savings rollup and gracefully default the rest.
    """
    if scenario is None or not isinstance(scenario.summary, dict):
        return {}
    return scenario.summary


def _scenario_value(scenario: RouteScenario | None, key: str, default: float = 0.0) -> float:
    summary = _scenario_summary(scenario)
    raw = summary.get(key)
    try:
        return float(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


async def get_optimization_savings(
    db: AsyncSession,
    principal,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    """Aggregate "how much did route optimization save us?" over a date range.

    For each finished comparison with a selected scenario we compute the delta
    between the **human-dispatcher baseline** and the **selected scenario**.
    The response bundles the totals (for big-number KPIs) plus a per-day
    series the frontend uses to render a stacked bar chart.

    A comparison only contributes when:
      - `selected_scenario_id` is set (operator picked something),
      - the `human_dispatcher_baseline` scenario for the same comparison has a
        summary (we can't compute a delta without the baseline),
      - the selected scenario is not the baseline itself (no savings to claim
        if we manually picked the baseline).
    """
    query = (
        select(RouteScenarioComparison)
        .where(
            tenant_filter(RouteScenarioComparison, principal),
            RouteScenarioComparison.selected_scenario_id.is_not(None),
        )
        .order_by(RouteScenarioComparison.target_date.asc())
    )
    # Filter by operational day (`target_date`) so the range matches what
    # dispatchers think of as "this week" and stays stable across time zones;
    # `created_at` is when the row was written and can drift from that day.
    if date_from:
        d0 = date_from.date() if isinstance(date_from, datetime) else date_from
        if isinstance(d0, date):
            query = query.where(RouteScenarioComparison.target_date >= d0)
    if date_to:
        d1 = date_to.date() if isinstance(date_to, datetime) else date_to
        if isinstance(d1, date):
            query = query.where(RouteScenarioComparison.target_date <= d1)

    result = await db.execute(query)
    comparisons = list(result.scalars().all())

    per_day: list[dict] = []
    solver_counts: dict[str, int] = {}
    contributing = 0

    base_dist_total = 0.0
    opt_dist_total = 0.0
    base_dur_total = 0.0
    opt_dur_total = 0.0
    base_on_time_weighted_sum = 0.0
    opt_on_time_weighted_sum = 0.0
    on_time_weight_total = 0  # number of delivered stops behind the rate
    base_late_total = 0
    opt_late_total = 0
    base_energy_total = 0.0
    opt_energy_total = 0.0
    base_fuel_total = 0.0
    opt_fuel_total = 0.0
    base_co2_total = 0.0
    opt_co2_total = 0.0
    base_routes_total = 0
    opt_routes_total = 0

    for comparison in comparisons:
        scenarios = list(comparison.scenarios)
        baseline = next(
            (s for s in scenarios if s.key == HUMAN_DISPATCHER_BASELINE_KEY),
            None,
        )
        selected = next(
            (s for s in scenarios if s.id == comparison.selected_scenario_id),
            None,
        )
        if baseline is None or selected is None:
            continue
        if not _scenario_summary(baseline) or not _scenario_summary(selected):
            continue
        if selected.key == HUMAN_DISPATCHER_BASELINE_KEY:
            continue  # manually keeping baseline → no savings

        contributing += 1
        solver_counts[selected.key] = solver_counts.get(selected.key, 0) + 1

        base_dist = _scenario_value(baseline, "distance_km")
        opt_dist = _scenario_value(selected, "distance_km")
        base_dur = _scenario_value(baseline, "duration_minutes")
        opt_dur = _scenario_value(selected, "duration_minutes")
        base_on_time = _scenario_value(baseline, "on_time_rate")
        opt_on_time = _scenario_value(selected, "on_time_rate")
        # Weight the on-time delta by the assigned count so big days count more.
        weight = int(_scenario_value(selected, "assigned_count")) or 1
        base_late = int(_scenario_value(baseline, "late_minutes"))
        opt_late = int(_scenario_value(selected, "late_minutes"))
        base_energy = _scenario_value(baseline, "realized_energy_cost_try")
        opt_energy = _scenario_value(selected, "realized_energy_cost_try")
        base_fuel = _scenario_value(baseline, "fuel_liters")
        opt_fuel = _scenario_value(selected, "fuel_liters")
        base_co2 = _scenario_value(baseline, "co2_kg")
        opt_co2 = _scenario_value(selected, "co2_kg")
        base_routes = int(_scenario_value(baseline, "route_count"))
        opt_routes = int(_scenario_value(selected, "route_count"))

        base_dist_total += base_dist
        opt_dist_total += opt_dist
        base_dur_total += base_dur
        opt_dur_total += opt_dur
        base_on_time_weighted_sum += base_on_time * weight
        opt_on_time_weighted_sum += opt_on_time * weight
        on_time_weight_total += weight
        base_late_total += base_late
        opt_late_total += opt_late
        base_energy_total += base_energy
        opt_energy_total += opt_energy
        base_fuel_total += base_fuel
        opt_fuel_total += opt_fuel
        base_co2_total += base_co2
        opt_co2_total += opt_co2
        base_routes_total += base_routes
        opt_routes_total += opt_routes

        per_day.append(
            {
                "date": comparison.target_date.isoformat(),
                "selected_scenario_key": selected.key,
                "baseline_distance_km": round(base_dist, 2),
                "optimized_distance_km": round(opt_dist, 2),
                "baseline_duration_min": round(base_dur, 1),
                "optimized_duration_min": round(opt_dur, 1),
                "baseline_on_time_rate": round(base_on_time, 4),
                "optimized_on_time_rate": round(opt_on_time, 4),
                "baseline_energy_cost_try": round(base_energy, 2),
                "optimized_energy_cost_try": round(opt_energy, 2),
                "baseline_co2_kg": round(base_co2, 2),
                "optimized_co2_kg": round(opt_co2, 2),
            }
        )

    distance_saved = base_dist_total - opt_dist_total
    duration_saved = base_dur_total - opt_dur_total
    distance_saved_pct = (distance_saved / base_dist_total) if base_dist_total else 0.0
    duration_saved_pct = (duration_saved / base_dur_total) if base_dur_total else 0.0
    energy_saved = base_energy_total - opt_energy_total
    fuel_saved = base_fuel_total - opt_fuel_total
    co2_saved = base_co2_total - opt_co2_total
    routes_saved = base_routes_total - opt_routes_total
    base_on_time = (base_on_time_weighted_sum / on_time_weight_total) if on_time_weight_total else 0.0
    opt_on_time = (opt_on_time_weighted_sum / on_time_weight_total) if on_time_weight_total else 0.0
    on_time_delta = opt_on_time - base_on_time
    late_minutes_saved = base_late_total - opt_late_total

    return {
        "comparisons_count": contributing,
        "totals": {
            "baseline_distance_km": round(base_dist_total, 2),
            "optimized_distance_km": round(opt_dist_total, 2),
            "distance_saved_km": round(distance_saved, 2),
            "distance_saved_pct": round(distance_saved_pct, 4),
            "baseline_duration_min": round(base_dur_total, 1),
            "optimized_duration_min": round(opt_dur_total, 1),
            "duration_saved_min": round(duration_saved, 1),
            "duration_saved_pct": round(duration_saved_pct, 4),
            "baseline_on_time_rate": round(base_on_time, 4),
            "optimized_on_time_rate": round(opt_on_time, 4),
            "on_time_rate_delta": round(on_time_delta, 4),
            "late_minutes_saved": int(late_minutes_saved),
            "energy_cost_saved_try": round(energy_saved, 2),
            "fuel_saved_liters": round(fuel_saved, 2),
            "co2_saved_kg": round(co2_saved, 2),
            "routes_saved": int(routes_saved),
        },
        "per_day": per_day,
        "solver_mix": [
            {"scenario_key": key, "count": count}
            for key, count in sorted(solver_counts.items(), key=lambda kv: -kv[1])
        ],
    }


# ── Phase 2: airport pickup punctuality ────────────────────────────────


async def get_airport_punctuality(
    db: AsyncSession,
    principal,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    """On-time pickup rate by airport terminal for RIDE orders with
    `flight_info` populated.

    "On time" means the courier's actual_arrival at the pickup stop was
    at or before the order's `latest_pickup`. Stops without an
    actual_arrival are excluded — they haven't happened yet.

    Caller is assumed to have both `rides_enabled` and
    `ride_airport_features_enabled`; the endpoint enforces.
    """
    from app.models.route import RouteStop

    # Terminal extracted from flight_info JSONB.
    terminal_expr = Order.flight_info["terminal"].astext.label("terminal")

    base_filters = [
        tenant_filter(Order, principal),
        Order.type == OrderType.RIDE,
        Order.flight_info.is_not(None),
        cast(Order.flight_info, String) != "null",
        RouteStop.stop_type == "PICKUP",
        RouteStop.actual_arrival.is_not(None),
        Order.latest_pickup.is_not(None),
    ]
    if date_from:
        base_filters.append(Order.created_at >= date_from)
    if date_to:
        base_filters.append(Order.created_at <= date_to)

    on_time_case = case(
        (RouteStop.actual_arrival <= Order.latest_pickup, 1),
        else_=0,
    )

    query = (
        select(
            terminal_expr,
            func.count().label("total"),
            func.sum(on_time_case).label("on_time"),
        )
        .select_from(Order)
        .join(RouteStop, RouteStop.order_id == Order.id)
        .where(*base_filters)
        .group_by(terminal_expr)
        .order_by(func.count().desc())
    )

    rows: list[dict] = []
    overall_total = 0
    overall_on_time = 0
    for row in (await db.execute(query)).all():
        total = int(row.total or 0)
        on_time = int(row.on_time or 0)
        rate = (on_time / total) if total else 0.0
        rows.append(
            {
                "terminal": row.terminal or "(unspecified)",
                "total": total,
                "on_time": on_time,
                "on_time_rate": round(rate, 4),
            }
        )
        overall_total += total
        overall_on_time += on_time

    return {
        "rows": rows,
        "overall_total": overall_total,
        "overall_on_time": overall_on_time,
        "overall_on_time_rate": round(
            (overall_on_time / overall_total) if overall_total else 0.0, 4
        ),
    }


# ───── Cost Savings (Layer 1 — pilot headline KPI) ─────────────────────


def _zero_cost_totals_payload(*, routes: int = 0, deliveries: int = 0) -> dict:
    return {
        "total_fuel_cost_try": 0.0,
        "total_fuel_liters": 0.0,
        "total_co2_kg": 0.0,
        "total_distance_km": 0.0,
        "total_routes": routes,
        "total_deliveries": deliveries,
        "cost_per_delivery_try": None,
        "cost_per_km_try": None,
        "fuel_cost_saved_try": None,
        "co2_saved_kg": None,
        "distance_saved_km": None,
    }


async def get_cost_savings(
    db: AsyncSession,
    current_user,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    """Read fuel cost + CO₂ totals from `route_performance_snapshots`.

    Filters on `operational_date` so the response is timezone-stable.
    `date_from` / `date_to` are inclusive — None means "no bound on that
    side". A typical Insights query passes today + 7 days back.
    """
    filters = [tenant_filter(RoutePerformanceSnapshot, current_user)]
    if date_from is not None:
        filters.append(
            RoutePerformanceSnapshot.operational_date >= date_from.date()
        )
    if date_to is not None:
        filters.append(
            RoutePerformanceSnapshot.operational_date <= date_to.date()
        )

    rows = (
        await db.execute(
            select(
                RoutePerformanceSnapshot.operational_date,
                RoutePerformanceSnapshot.actual_fuel_cost_try,
                RoutePerformanceSnapshot.actual_fuel_liters,
                RoutePerformanceSnapshot.co2_kg,
                RoutePerformanceSnapshot.actual_distance_m,
                RoutePerformanceSnapshot.actual_stop_count,
                RoutePerformanceSnapshot.baseline_fuel_cost_try,
                RoutePerformanceSnapshot.baseline_distance_m,
            ).where(*filters)
        )
    ).all()

    if not rows:
        return {
            "routes_included": 0,
            "totals": _zero_cost_totals_payload(),
            "per_day": [],
        }

    # Per-day aggregation.
    by_day: dict[date, dict] = {}
    total_fuel_cost = 0.0
    total_fuel_l = 0.0
    total_co2_kg = 0.0
    total_distance_m = 0.0
    total_deliveries = 0
    total_baseline_fuel_cost = 0.0
    total_baseline_distance_m = 0.0
    have_baseline = False
    routes_included = 0

    for (
        op_date,
        fuel_cost,
        fuel_l,
        co2,
        dist_m,
        stop_count,
        baseline_fuel,
        baseline_distance,
    ) in rows:
        routes_included += 1
        bucket = by_day.setdefault(
            op_date,
            {
                "date": op_date.isoformat(),
                "total_fuel_cost_try": 0.0,
                "total_fuel_liters": 0.0,
                "total_co2_kg": 0.0,
                "total_distance_km": 0.0,
                "routes": 0,
                "_baseline_fuel_cost_try": 0.0,
                "_have_baseline": False,
            },
        )
        bucket["routes"] += 1
        if fuel_cost is not None:
            bucket["total_fuel_cost_try"] += float(fuel_cost)
            total_fuel_cost += float(fuel_cost)
        if fuel_l is not None:
            bucket["total_fuel_liters"] += float(fuel_l)
            total_fuel_l += float(fuel_l)
        if co2 is not None:
            bucket["total_co2_kg"] += float(co2)
            total_co2_kg += float(co2)
        if dist_m is not None:
            bucket["total_distance_km"] += float(dist_m) / 1000.0
            total_distance_m += float(dist_m)
        if stop_count is not None:
            total_deliveries += int(stop_count)
        if baseline_fuel is not None:
            bucket["_baseline_fuel_cost_try"] += float(baseline_fuel)
            bucket["_have_baseline"] = True
            total_baseline_fuel_cost += float(baseline_fuel)
            have_baseline = True
        if baseline_distance is not None:
            total_baseline_distance_m += float(baseline_distance)

    per_day = []
    for day in sorted(by_day):
        d = by_day[day]
        fuel_cost_saved = (
            d["_baseline_fuel_cost_try"] - d["total_fuel_cost_try"]
            if d["_have_baseline"]
            else None
        )
        co2_saved = None  # filled when baseline CO2 lands in Layer 2
        per_day.append(
            {
                "date": d["date"],
                "total_fuel_cost_try": round(d["total_fuel_cost_try"], 2),
                "total_fuel_liters": round(d["total_fuel_liters"], 3),
                "total_co2_kg": round(d["total_co2_kg"], 3),
                "total_distance_km": round(d["total_distance_km"], 2),
                "routes": d["routes"],
                "fuel_cost_saved_try": (
                    round(fuel_cost_saved, 2) if fuel_cost_saved is not None else None
                ),
                "co2_saved_kg": co2_saved,
            }
        )

    cost_per_delivery = (
        round(total_fuel_cost / total_deliveries, 2)
        if total_deliveries > 0
        else None
    )
    cost_per_km = (
        round(total_fuel_cost / (total_distance_m / 1000.0), 2)
        if total_distance_m > 0
        else None
    )
    totals = {
        "total_fuel_cost_try": round(total_fuel_cost, 2),
        "total_fuel_liters": round(total_fuel_l, 3),
        "total_co2_kg": round(total_co2_kg, 3),
        "total_distance_km": round(total_distance_m / 1000.0, 2),
        "total_routes": routes_included,
        "total_deliveries": total_deliveries,
        "cost_per_delivery_try": cost_per_delivery,
        "cost_per_km_try": cost_per_km,
        "fuel_cost_saved_try": (
            round(total_baseline_fuel_cost - total_fuel_cost, 2)
            if have_baseline
            else None
        ),
        "co2_saved_kg": None,
        "distance_saved_km": (
            round((total_baseline_distance_m - total_distance_m) / 1000.0, 2)
            if total_baseline_distance_m > 0
            else None
        ),
    }
    return {
        "routes_included": routes_included,
        "totals": totals,
        "per_day": per_day,
    }


# ───── Solver quality — Optimization-gains adjacent (Layer 2/3) ─────────


_ETA_ON_TIME_WINDOW_S = 15 * 60  # 15-minute "on-time" threshold


async def _eta_accuracy_stats(
    db: AsyncSession,
    *,
    current_user,
    date_from: datetime | None,
    date_to: datetime | None,
) -> dict:
    filters = [
        tenant_filter(ETAPredictionLog, current_user),
        ETAPredictionLog.predicted_by == ETAPredictedBy.PLANNER,
        ETAPredictionLog.actual_arrival.is_not(None),
        ETAPredictionLog.error_s.is_not(None),
    ]
    if date_from is not None:
        filters.append(ETAPredictionLog.predicted_at >= date_from)
    if date_to is not None:
        filters.append(ETAPredictionLog.predicted_at <= date_to)

    rows = (
        await db.execute(
            select(ETAPredictionLog.error_s).where(*filters)
        )
    ).all()
    errors = [int(r[0]) for r in rows if r[0] is not None]
    if not errors:
        return {
            "predictions": 0,
            "on_time_rate": None,
            "mean_abs_error_s": None,
            "p95_abs_error_s": None,
            "mean_error_signed_s": None,
            "on_time_window_s": _ETA_ON_TIME_WINDOW_S,
        }
    abs_errors = sorted(abs(e) for e in errors)
    n = len(errors)
    mean_abs = sum(abs_errors) / n
    mean_signed = sum(errors) / n
    p95_idx = max(0, int(0.95 * n) - 1)
    p95_abs = abs_errors[p95_idx]
    on_time_count = sum(1 for e in abs_errors if e <= _ETA_ON_TIME_WINDOW_S)
    return {
        "predictions": n,
        "on_time_rate": round(on_time_count / n, 4),
        "mean_abs_error_s": round(mean_abs, 1),
        "p95_abs_error_s": int(p95_abs),
        "mean_error_signed_s": round(mean_signed, 1),
        "on_time_window_s": _ETA_ON_TIME_WINDOW_S,
    }


async def _failure_cause_stats(
    db: AsyncSession,
    *,
    current_user,
    date_from: datetime | None,
    date_to: datetime | None,
) -> dict:
    filters = [
        tenant_filter(Order, current_user),
        Order.status == OrderStatus.FAILED,
    ]
    if date_from is not None:
        filters.append(Order.failed_at >= date_from)
    if date_to is not None:
        filters.append(Order.failed_at <= date_to)

    rows = (
        await db.execute(
            select(
                Order.failure_cause_category,
                func.count(Order.id),
            )
            .where(*filters)
            .group_by(Order.failure_cause_category)
        )
    ).all()
    total = sum(int(c) for _, c in rows)
    by_category = []
    for category, count in sorted(rows, key=lambda kv: int(kv[1]), reverse=True):
        cat_value = category.value if category is not None else "UNKNOWN"
        by_category.append(
            {
                "category": cat_value,
                "count": int(count),
                "share": round(int(count) / total, 4) if total > 0 else 0.0,
            }
        )
    return {"total_failures": total, "by_category": by_category}


async def _plan_stability_stats(
    db: AsyncSession,
    *,
    current_user,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int = 30,
) -> dict:
    filters = [tenant_filter(PlanStabilityScore, current_user)]
    if date_from is not None:
        filters.append(PlanStabilityScore.created_at >= date_from)
    if date_to is not None:
        filters.append(PlanStabilityScore.created_at <= date_to)

    rows = (
        await db.execute(
            select(
                PlanStabilityScore.created_at,
                PlanStabilityScore.stability_pct,
                PlanStabilityScore.orders_evaluated,
            )
            .where(*filters)
            .order_by(PlanStabilityScore.created_at.desc())
            .limit(limit)
        )
    ).all()
    if not rows:
        return {"samples": 0, "avg_stability_pct": None, "recent": []}
    # Reverse for chronological-ascending plot order.
    rows_chrono = list(reversed(rows))
    scored = [
        float(r[1]) for r in rows_chrono if r[1] is not None
    ]
    avg = sum(scored) / len(scored) if scored else None
    return {
        "samples": len(rows_chrono),
        "avg_stability_pct": round(avg, 4) if avg is not None else None,
        "recent": [
            {
                "created_at": r[0],
                "stability_pct": (float(r[1]) if r[1] is not None else None),
                "orders_evaluated": int(r[2]),
            }
            for r in rows_chrono
        ],
    }


async def get_solver_quality(
    db: AsyncSession,
    current_user,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    """Bundle the three Layer 2/3 solver-quality lenses into one
    response: ETA accuracy, failure causes, plan stability."""
    return {
        "eta_accuracy": await _eta_accuracy_stats(
            db, current_user=current_user, date_from=date_from, date_to=date_to
        ),
        "failure_causes": await _failure_cause_stats(
            db, current_user=current_user, date_from=date_from, date_to=date_to
        ),
        "plan_stability": await _plan_stability_stats(
            db, current_user=current_user, date_from=date_from, date_to=date_to
        ),
    }
