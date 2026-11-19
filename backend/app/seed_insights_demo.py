"""Realistic historical demo data for Insights (operations + fleet).

Populates ~35 days of orders with a mature-looking status mix (mostly
delivered, modest in-flight pipeline, low unassigned noise) and optional
completed routes so fleet utilization charts read plausibly.

All synthetic rows are tagged for safe cleanup:
  - `orders.external_ref` like `insights_demo_YYYY-MM-DD_NNNN`
  - `optimization_runs.task_id == INSIGHTS_DEMO_RUN_TASK_ID` for anchor runs
    that own synthetic fleet routes only

Usage (backend venv, DB reachable):
    cd backend && python -m app.seed_insights_demo [--force] [--days N] [--with-scenarios]

`--force` removes previously inserted insight-demo orders and demo fleet routes,
then re-seeds from scratch. Without `--force`, missing per-day indices are
topped up (idempotent).

`--with-scenarios` also runs `seed_optimization_savings_demo` for the same day
window so the Optimization gains tab has comparison data.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import re
import secrets
import sys
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.models.vehicle_tag  # noqa: F401  # register association tables
from app.database import async_session
from app.models.courier import Courier
from app.models.depot import Depot
from app.models.enums import (
    FailureCauseCategory,
    OptimizationRunStatus,
    OptimizationRunType,
    OrderPriority,
    OrderStatus,
    OrderType,
    RouteStatus,
)
from app.models.eta_prediction_log import ETAPredictedBy, ETAPredictionLog
from app.models.optimization_run import OptimizationRun
from app.models.order import Order
from app.models.order_history import OrderStatusHistory
from app.models.plan_stability_score import PlanStabilityScore
from app.models.route import Route, RouteStop
from app.models.route_performance_snapshot import RoutePerformanceSnapshot
from app.models.sla_alert import SLAAlert
from app.seed_helpers import ensure_default_tenant
from app.utils.compat import utcnow_naive

INSIGHTS_DEMO_PREFIX = "insights_demo_"
INSIGHTS_DEMO_RUN_TASK_ID = "insights_demo_fleet_anchor"

RNG_SEED = 20260512
rng = random.Random(RNG_SEED)

# Neighbourhoods (subset of seed_bulk_orders pool — Istanbul, no Silivri).
LOCATIONS: list[tuple[str, float, float]] = [
    ("Beşiktaş, İstanbul", 41.0426, 29.0064),
    ("Şişli, İstanbul", 41.0607, 28.9877),
    ("Kadıköy, İstanbul", 40.9906, 29.0231),
    ("Üsküdar, İstanbul", 41.0228, 29.0154),
    ("Bakırköy, İstanbul", 40.9816, 28.8731),
    ("Fatih, İstanbul", 41.0186, 28.9394),
    ("Beyoğlu, İstanbul", 41.0369, 28.9844),
    ("Maltepe, İstanbul", 40.9350, 29.1327),
    ("Ataşehir, İstanbul", 40.9952, 29.1213),
    ("Bağcılar, İstanbul", 41.0397, 28.8560),
    ("Kağıthane, İstanbul", 41.0787, 28.9718),
    ("Bahçelievler, İstanbul", 41.0024, 28.8622),
]

STREET_TEMPLATES = [
    "{n}. Sokak No:{h}",
    "Çiçek Sokak No:{h}",
    "Atatürk Caddesi No:{h}",
]

# Cumulative weights for status distribution (mature ops profile).
_STATUS_WEIGHTS: list[tuple[OrderStatus, float]] = [
    (OrderStatus.DELIVERED, 0.72),
    (OrderStatus.PENDING, 0.08),
    (OrderStatus.ASSIGNED, 0.06),
    (OrderStatus.PICKED_UP, 0.04),
    (OrderStatus.IN_TRANSIT, 0.05),
    (OrderStatus.FAILED, 0.02),
    (OrderStatus.CANCELLED, 0.015),
    (OrderStatus.DRAFT, 0.01),
    (OrderStatus.RESCHEDULED, 0.015),
    (OrderStatus.EXPIRED, 0.005),
]


def _jitter(lat: float, lon: float, *, radius_km: float) -> tuple[float, float]:
    dy = rng.uniform(-radius_km, radius_km) / 111.0
    dx = rng.uniform(-radius_km, radius_km) / (111.0 * max(0.2, abs(lat / 90.0)))
    return round(lat + dy, 6), round(lon + dx, 6)


def _street(area: str) -> str:
    n = rng.randint(1, 120)
    h = rng.randint(1, 80)
    return f"{rng.choice(STREET_TEMPLATES).format(n=n, h=h)}, {area}"


def _customer_email() -> str:
    return f"insights.demo.{rng.randint(1, 99999)}@example.com"


def _external_ref(target: date, idx: int) -> str:
    return f"{INSIGHTS_DEMO_PREFIX}{target.isoformat()}_{idx:04d}"


def _parse_existing_indices(refs: list[str | None], target: date) -> set[int]:
    prefix = f"{INSIGHTS_DEMO_PREFIX}{target.isoformat()}_"
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    out: set[int] = set()
    for ref in refs:
        if not ref:
            continue
        m = pat.match(ref)
        if m:
            out.add(int(m.group(1)))
    return out


def _orders_target_for_day(target: date) -> int:
    """More volume on weekdays, lighter on weekends."""
    wd = target.weekday()  # Mon=0
    if wd < 5:
        return rng.randint(48, 72)
    if wd == 5:
        return rng.randint(32, 48)
    return rng.randint(24, 38)


_FAILURE_CAUSE_MIX: list[tuple[FailureCauseCategory, list[str], float]] = [
    # (category, candidate subcategories, weight)
    (FailureCauseCategory.CUSTOMER, ["NOT_HOME", "REFUSED", "WRONG_RECIPIENT"], 0.55),
    (FailureCauseCategory.ADDRESS, ["WRONG_ADDRESS", "GATED_BUILDING", "ACCESS_DENIED"], 0.20),
    (FailureCauseCategory.COURIER, ["RAN_LATE", "VEHICLE_ISSUE"], 0.10),
    (FailureCauseCategory.WEATHER, ["HEAVY_RAIN", "SNOW", "FOG"], 0.07),
    (FailureCauseCategory.SYSTEM, ["GPS_DRIFT", "WEBHOOK_FAILURE"], 0.05),
    (FailureCauseCategory.OTHER, ["UNCATEGORIZED"], 0.03),
]


def _pick_failure_cause() -> tuple[FailureCauseCategory, str]:
    """Pick a (category, subcategory) for a failed-order seed row.

    The weights skew customer-side because that's what pilot ops teams
    typically see; the optimization-gains dashboard splitting customer
    vs operational causes is the value the typed taxonomy adds."""
    categories = [c for c, _, _ in _FAILURE_CAUSE_MIX]
    weights = [w for _, _, w in _FAILURE_CAUSE_MIX]
    cat = rng.choices(categories, weights=weights)[0]
    subcats = next(s for c, s, _ in _FAILURE_CAUSE_MIX if c == cat)
    return cat, rng.choice(subcats)


def _pick_status() -> OrderStatus:
    r = rng.random()
    cum = 0.0
    for st, w in _STATUS_WEIGHTS:
        cum += w
        if r < cum:
            return st
    return OrderStatus.DELIVERED


def _random_created_at(target: date) -> datetime:
    hour = rng.randint(7, 21)
    minute = rng.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55])
    return datetime(target.year, target.month, target.day, hour, minute, 0)


def _build_order(
    *,
    tenant_id: uuid.UUID,
    depot: Depot,
    courier_ids: list[uuid.UUID],
    target: date,
    idx: int,
    status: OrderStatus,
    today: date,
) -> Order:
    customer_loc = rng.choice(LOCATIONS)
    c_lat, c_lon = _jitter(customer_loc[1], customer_loc[2], radius_km=0.75)
    d_lat, d_lon = _jitter(depot.lat, depot.lon, radius_km=0.2)

    pickup_address = depot.address
    pickup_lat, pickup_lon = d_lat, d_lon
    delivery_address = _street(customer_loc[0])
    delivery_lat, delivery_lon = c_lat, c_lon

    base = datetime(target.year, target.month, target.day)
    earliest_pickup = base + timedelta(hours=rng.randint(6, 9), minutes=rng.choice([0, 15, 30]))
    latest_pickup = earliest_pickup + timedelta(hours=rng.randint(2, 4))
    earliest_delivery = base + timedelta(hours=rng.randint(10, 15), minutes=rng.choice([0, 15, 30]))
    latest_delivery = earliest_delivery + timedelta(hours=rng.randint(3, 6))

    created_at = _random_created_at(target)
    assigned_courier_id: uuid.UUID | None = None
    route_id: uuid.UUID | None = None
    failed_at: datetime | None = None

    if status in (
        OrderStatus.ASSIGNED,
        OrderStatus.PICKED_UP,
        OrderStatus.IN_TRANSIT,
        OrderStatus.DELIVERED,
        OrderStatus.FAILED,
    ):
        assigned_courier_id = rng.choice(courier_ids)

    # Default updated_at tracks last change
    updated_at = created_at

    if status == OrderStatus.DELIVERED:
        on_time = rng.random() < 0.87
        if on_time:
            slack = timedelta(minutes=rng.randint(5, int((latest_delivery - earliest_delivery).total_seconds() / 60) or 30))
            updated_at = min(latest_delivery - slack, latest_delivery)
        else:
            late_m = rng.randint(5, 90)
            updated_at = latest_delivery + timedelta(minutes=late_m)
    elif status == OrderStatus.FAILED:
        failed_at = base + timedelta(hours=rng.randint(11, 20))
        updated_at = failed_at + timedelta(minutes=rng.randint(1, 45))
    elif status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED):
        updated_at = created_at + timedelta(hours=rng.randint(1, 72))
    else:
        updated_at = created_at + timedelta(hours=rng.randint(1, 48))

    # Layer 3 — tag failed orders with a plausible typed cause so the
    # failure-cause breakdown card has real categories to show. Mix
    # roughly reflects pilot expectations (customer issues dominate).
    if status == OrderStatus.FAILED:
        cause_category, cause_subcategory = _pick_failure_cause()
    else:
        cause_category, cause_subcategory = None, None

    # A few same-day unassigned PENDING orders (exception center noise) — only on "today"
    if target == today and status == OrderStatus.PENDING and rng.random() < 0.12:
        assigned_courier_id = None
        earliest_delivery = datetime(today.year, today.month, today.day, 8, 0, 0)
        latest_delivery = datetime(today.year, today.month, today.day, 22, 0, 0)

    return Order(
        tenant_id=tenant_id,
        type=OrderType.ON_DEMAND,
        status=status,
        priority=rng.choices(
            [OrderPriority.LOW, OrderPriority.NORMAL, OrderPriority.HIGH, OrderPriority.URGENT],
            weights=[0.05, 0.72, 0.20, 0.03],
        )[0],
        depot_id=depot.id,
        pickup_address=pickup_address,
        pickup_lat=pickup_lat,
        pickup_lon=pickup_lon,
        delivery_address=delivery_address,
        delivery_lat=delivery_lat,
        delivery_lon=delivery_lon,
        weight_kg=round(rng.uniform(0.4, 22.0), 2),
        is_fragile=rng.random() < 0.12,
        is_temp_sensitive=rng.random() < 0.06,
        earliest_pickup=earliest_pickup,
        latest_pickup=latest_pickup,
        earliest_delivery=earliest_delivery,
        latest_delivery=latest_delivery,
        scheduled_for_date=target if status in (OrderStatus.ASSIGNED, OrderStatus.PICKED_UP, OrderStatus.IN_TRANSIT) else None,
        customer_email=_customer_email(),
        customer_phone=f"+90555{rng.randint(1000000, 9999999)}" if rng.random() < 0.8 else None,
        tracking_token=secrets.token_urlsafe(24),
        external_ref=_external_ref(target, idx),
        assigned_courier_id=assigned_courier_id,
        route_id=route_id,
        created_at=created_at,
        updated_at=updated_at,
        failed_at=failed_at,
        failure_cause_category=cause_category,
        failure_cause_subcategory=cause_subcategory,
    )


async def _wipe_demo_data(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    run_result = await session.execute(
        select(OptimizationRun.id).where(
            OptimizationRun.tenant_id == tenant_id,
            OptimizationRun.task_id == INSIGHTS_DEMO_RUN_TASK_ID,
        )
    )
    run_ids = [r[0] for r in run_result.all()]
    if run_ids:
        route_ids = (
            await session.execute(select(Route.id).where(Route.optimization_run_id.in_(run_ids)))
        ).scalars().all()
        if route_ids:
            stop_ids = (
                await session.execute(select(RouteStop.id).where(RouteStop.route_id.in_(route_ids)))
            ).scalars().all()
            if stop_ids:
                await session.execute(delete(SLAAlert).where(SLAAlert.stop_id.in_(stop_ids)))
            await session.execute(delete(RouteStop).where(RouteStop.route_id.in_(route_ids)))
            await session.execute(delete(Route).where(Route.id.in_(route_ids)))
        await session.execute(delete(OptimizationRun).where(OptimizationRun.id.in_(run_ids)))

    order_ids = (
        await session.execute(
            select(Order.id).where(
                Order.tenant_id == tenant_id,
                Order.external_ref.like(f"{INSIGHTS_DEMO_PREFIX}%"),
            )
        )
    ).scalars().all()
    if order_ids:
        await session.execute(
            delete(OrderStatusHistory).where(
                OrderStatusHistory.order_id.in_(order_ids)
            )
        )
        await session.execute(delete(RouteStop).where(RouteStop.order_id.in_(order_ids)))
        await session.execute(delete(Order).where(Order.id.in_(order_ids)))


async def _ensure_anchor_run(session: AsyncSession, tenant_id: uuid.UUID) -> OptimizationRun:
    existing = (
        await session.execute(
            select(OptimizationRun).where(
                OptimizationRun.tenant_id == tenant_id,
                OptimizationRun.task_id == INSIGHTS_DEMO_RUN_TASK_ID,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    run = OptimizationRun(
        tenant_id=tenant_id,
        task_id=INSIGHTS_DEMO_RUN_TASK_ID,
        run_type=OptimizationRunType.BATCH,
        status=OptimizationRunStatus.SUCCEEDED,
        routes_created=0,
        unassigned_count=0,
        started_at=utcnow_naive(),
        finished_at=utcnow_naive(),
    )
    session.add(run)
    await session.flush()
    return run


async def _seed_fleet_routes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    courier_ids: list[uuid.UUID],
    anchor_run: OptimizationRun,
    days: int,
    today: date,
) -> int:
    """Insert completed routes spread across the window (no RouteStop rows)."""
    n = 0
    routes_to_snapshot: list[Route] = []
    for offset in range(days):
        d = today - timedelta(days=offset)
        day_start = datetime(d.year, d.month, d.day, 0, 0, 0)
        routes_today = rng.randint(8, 20)
        for _ in range(routes_today):
            cid = rng.choice(courier_ids)
            start_h = rng.randint(8, 16)
            dur_h = rng.uniform(1.5, 5.5)
            started = day_start + timedelta(hours=start_h, minutes=rng.choice([0, 15, 30]))
            completed = started + timedelta(hours=dur_h)
            dist = round(rng.uniform(8.0, 52.0), 2)
            route = Route(
                tenant_id=tenant_id,
                courier_id=cid,
                status=RouteStatus.COMPLETED,
                planned_distance_km=dist,
                actual_distance_km=round(dist * rng.uniform(0.95, 1.08), 2),
                optimization_run_id=anchor_run.id,
                created_at=started - timedelta(minutes=rng.randint(5, 25)),
                started_at=started,
                completed_at=completed,
            )
            session.add(route)
            routes_to_snapshot.append(route)
            n += 1
    await session.flush()
    # Materialize plausible cost/fuel/CO2 snapshots so the Cost Savings
    # tab lights up with data on a fresh demo install. Production data
    # comes from the live `_maybe_complete_route` hook instead.
    await _seed_route_performance_snapshots(session, routes_to_snapshot)
    return n


# Synthetic per-km cost factors for demo snapshots. Calibrated against
# typical small-fleet economics in Türkiye (May 2026 prices):
#   * Diesel: ~40 TRY/L, 12 km/L → ~3.30 TRY/km
#   * Gasoline: ~44 TRY/L, 10 km/L → ~4.40 TRY/km
# CO2: gasoline mix ~2.31 kg/L. Real production rows use VehicleSpec +
# EnergyPriceSnapshot rather than these constants.
_DEMO_FUEL_PRICE_TRY_PER_LITER = 44.0
_DEMO_KM_PER_LITER = 11.0
_DEMO_CO2_KG_PER_LITER = 2.31
# Counterfactual baseline: 12–28% worse on fuel — same band the
# `seed_optimization_savings_demo` script uses for its synthetic
# scenarios so the two demo sources tell a consistent story.
_DEMO_BASELINE_WORSE_LOW = 0.12
_DEMO_BASELINE_WORSE_HIGH = 0.28


async def _seed_status_history_for_seeded_orders(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> int:
    """Backfill OrderStatusHistory rows for the insight-demo orders so
    the Forensics drawer renders a real timeline on demo installs.

    Production orders go through `apply_order_status_transition` which
    writes these rows automatically; the seed bypasses that flow for
    speed, so we synthesize per-order histories here based on each
    order's terminal status."""
    hist_rng = random.Random(RNG_SEED ^ 0xF1E1D)
    orders = (
        await session.execute(
            select(Order).where(
                Order.tenant_id == tenant_id,
                Order.external_ref.like(f"{INSIGHTS_DEMO_PREFIX}%"),
            )
        )
    ).scalars().all()
    if not orders:
        return 0

    # Status flow per terminal state — only the transitions the order
    # actually went through. Same flow the real lifecycle helpers
    # produce, just compressed into seed-time inserts.
    flows: dict[OrderStatus, list[OrderStatus]] = {
        OrderStatus.DELIVERED: [
            OrderStatus.PENDING,
            OrderStatus.ASSIGNED,
            OrderStatus.PICKED_UP,
            OrderStatus.IN_TRANSIT,
            OrderStatus.DELIVERED,
        ],
        OrderStatus.FAILED: [
            OrderStatus.PENDING,
            OrderStatus.ASSIGNED,
            OrderStatus.PICKED_UP,
            OrderStatus.IN_TRANSIT,
            OrderStatus.FAILED,
        ],
        OrderStatus.IN_TRANSIT: [
            OrderStatus.PENDING,
            OrderStatus.ASSIGNED,
            OrderStatus.PICKED_UP,
            OrderStatus.IN_TRANSIT,
        ],
        OrderStatus.PICKED_UP: [
            OrderStatus.PENDING,
            OrderStatus.ASSIGNED,
            OrderStatus.PICKED_UP,
        ],
        OrderStatus.ASSIGNED: [OrderStatus.PENDING, OrderStatus.ASSIGNED],
        OrderStatus.CANCELLED: [OrderStatus.PENDING, OrderStatus.CANCELLED],
        OrderStatus.RESCHEDULED: [
            OrderStatus.PENDING,
            OrderStatus.ASSIGNED,
            OrderStatus.FAILED,
            OrderStatus.RESCHEDULED,
        ],
        OrderStatus.EXPIRED: [OrderStatus.PENDING, OrderStatus.EXPIRED],
    }

    # Minimum span per terminal status — guarantees the synthesized
    # transitions actually span enough seconds that the chronological
    # sort produces the intended order even after small jitter.
    _MIN_SPAN_S = {
        OrderStatus.DELIVERED: 6 * 3600,
        OrderStatus.FAILED: 4 * 3600,
        OrderStatus.IN_TRANSIT: 3 * 3600,
        OrderStatus.PICKED_UP: 2 * 3600,
        OrderStatus.ASSIGNED: 1 * 3600,
        OrderStatus.CANCELLED: 1 * 3600,
        OrderStatus.RESCHEDULED: 4 * 3600,
        OrderStatus.EXPIRED: 12 * 3600,
    }

    rows: list[OrderStatusHistory] = []
    for order in orders:
        flow = flows.get(order.status)
        if not flow:
            continue
        # Anchor on created_at and fan transitions out to a span that's
        # both realistic *and* big enough that per-step jitter can't
        # swap adjacent rows.
        start = order.created_at
        end = order.updated_at or order.created_at
        observed_span_s = int((end - start).total_seconds())
        span_s = max(_MIN_SPAN_S.get(order.status, 3600), observed_span_s)
        n_transitions = len(flow)
        step_s = span_s // n_transitions
        # Jitter is bounded to a third of step_s so the order of
        # transitions is preserved.
        jitter_max = max(0, step_s // 3 - 1)
        prev_status: OrderStatus | None = None
        for i, status in enumerate(flow):
            jitter = hist_rng.randint(-jitter_max, jitter_max) if jitter_max > 0 else 0
            ts = start + timedelta(seconds=step_s * (i + 1) + jitter)
            rows.append(
                OrderStatusHistory(
                    order_id=order.id,
                    from_status=prev_status,
                    to_status=status,
                    reason=f"Seed: synthesized {status.value} transition",
                    created_at=ts,
                )
            )
            prev_status = status

    if rows:
        session.add_all(rows)
        await session.flush()
    return len(rows)


async def _seed_plan_stability_scores(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    anchor_run: OptimizationRun,
    days: int,
    today: date,
) -> int:
    """Insert one synthetic plan-stability score per day so the
    Optimization-gains card has a real trend line on fresh installs."""
    seed_rng = random.Random(RNG_SEED ^ 0xB1A5BABE)
    rows: list[PlanStabilityScore] = []
    for offset in range(days):
        d = today - timedelta(days=offset)
        # Stability typically lands 70–95% in healthy ops; occasional
        # dip when demand spikes or the fleet shifts.
        pct = round(seed_rng.uniform(0.62, 0.96), 4)
        evaluated = seed_rng.randint(25, 80)
        unchanged = int(round(evaluated * pct))
        rows.append(
            PlanStabilityScore(
                tenant_id=tenant_id,
                optimization_run_id=anchor_run.id,
                prior_run_id=anchor_run.id,
                orders_evaluated=evaluated,
                orders_unchanged=unchanged,
                orders_same_courier=int(round(unchanged * seed_rng.uniform(0.92, 1.0))),
                orders_same_day=int(round(unchanged * seed_rng.uniform(0.95, 1.0))),
                stability_pct=pct,
                created_at=datetime(d.year, d.month, d.day, 7, 30, 0),
            )
        )
    if rows:
        session.add_all(rows)
        await session.flush()
    return len(rows)


async def _seed_eta_predictions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    routes_with_snapshots: list[Route],
) -> int:
    """For a sample of completed routes, fabricate per-route ETA log
    rows with a realistic accuracy distribution. We can't tie these
    to real stops (the seed doesn't create RouteStops), so we pick
    NULL stop_ids — the dashboard aggregation doesn't read stop_id."""
    # Skip: ETA log requires a non-null stop_id. Without seeded stops,
    # rather than fake them we leave the ETA card empty until the first
    # real run lands. The seed's job is to make the dashboard look
    # plausible without pretending we have data we don't.
    del session, tenant_id, routes_with_snapshots
    return 0


async def _seed_route_performance_snapshots(
    session: AsyncSession, routes: list[Route]
) -> None:
    """Insert one `RoutePerformanceSnapshot` per seed route with plausible
    fuel/cost/CO2 numbers + a counterfactual baseline column so the
    'fuel saved' KPI has something to render on a fresh install."""
    if not routes:
        return
    seed_rng = random.Random(RNG_SEED ^ 0xC051DA7A)
    rows: list[RoutePerformanceSnapshot] = []
    for route in routes:
        actual_distance_km = float(route.actual_distance_km or route.planned_distance_km or 0.0)
        actual_distance_m = actual_distance_km * 1000.0
        liters = actual_distance_km / _DEMO_KM_PER_LITER if _DEMO_KM_PER_LITER else 0.0
        fuel_cost = liters * _DEMO_FUEL_PRICE_TRY_PER_LITER
        co2_kg = liters * _DEMO_CO2_KG_PER_LITER
        baseline_factor = 1.0 + seed_rng.uniform(
            _DEMO_BASELINE_WORSE_LOW, _DEMO_BASELINE_WORSE_HIGH
        )
        baseline_fuel_cost = fuel_cost * baseline_factor
        baseline_distance_m = actual_distance_m * baseline_factor
        operational_date = (
            route.completed_at.date()
            if route.completed_at is not None
            else (route.started_at.date() if route.started_at else date.today())
        )
        planned_distance_m = float(route.planned_distance_km or 0.0) * 1000.0
        planned_duration_s = (
            int((route.completed_at - route.started_at).total_seconds())
            if route.started_at and route.completed_at
            else 0
        )
        actual_duration_s = planned_duration_s
        rows.append(
            RoutePerformanceSnapshot(
                route_id=route.id,
                tenant_id=route.tenant_id,
                courier_id=route.courier_id,
                optimization_run_id=route.optimization_run_id,
                operational_date=operational_date,
                planned_distance_m=planned_distance_m,
                planned_duration_s=planned_duration_s,
                planned_stop_count=seed_rng.randint(5, 15),
                actual_distance_m=actual_distance_m,
                actual_duration_s=actual_duration_s,
                actual_stop_count=seed_rng.randint(5, 14),
                delta_arrival_p50_s=seed_rng.randint(-180, 300),
                delta_arrival_p95_s=seed_rng.randint(120, 900),
                delta_arrival_max_s=seed_rng.randint(300, 1500),
                actual_fuel_liters=round(liters, 3),
                fuel_unit_price_try=_DEMO_FUEL_PRICE_TRY_PER_LITER,
                actual_fuel_cost_try=round(fuel_cost, 2),
                actual_distance_cost_try=round(fuel_cost, 2),
                total_route_cost_try=round(fuel_cost, 2),
                co2_kg=round(co2_kg, 3),
                baseline_fuel_cost_try=round(baseline_fuel_cost, 2),
                baseline_distance_m=round(baseline_distance_m, 2),
            )
        )
    session.add_all(rows)
    await session.flush()


async def main(*, days: int = 35, force: bool = False, with_scenarios: bool = False) -> tuple[int, int]:
    today = date.today()
    days = max(7, min(days, 90))

    async with async_session() as session:
        tenant = await ensure_default_tenant(session)

        depots = (
            await session.execute(
                select(Depot).where(Depot.tenant_id == tenant.id, Depot.is_active == True)  # noqa: E712
            )
        ).scalars().all()
        if not depots:
            print("[seed_insights_demo] No active depots for tenant; run seed_demo or seed_bulk_orders first.")
            return 0, 0
        depot = depots[0]

        courier_rows = (
            await session.execute(
                select(Courier).where(Courier.tenant_id == tenant.id).limit(24)
            )
        ).scalars().all()
        if not courier_rows:
            print("[seed_insights_demo] No couriers found; run seed_demo first.")
            return 0, 0
        courier_ids = [c.id for c in courier_rows]

        if force:
            await _wipe_demo_data(session, tenant.id)
            await session.commit()
            print("[seed_insights_demo] removed prior insight-demo orders and demo fleet routes")

        anchor = await _ensure_anchor_run(session, tenant.id)

        orders_added = 0
        for offset in range(days):
            target = today - timedelta(days=offset)
            want = _orders_target_for_day(target)

            existing_refs = (
                await session.execute(
                    select(Order.external_ref).where(
                        Order.tenant_id == tenant.id,
                        Order.external_ref.like(f"{INSIGHTS_DEMO_PREFIX}{target.isoformat()}_%"),
                    )
                )
            ).scalars().all()
            have = _parse_existing_indices(list(existing_refs), target)
            missing = [i for i in range(1, want + 1) if i not in have]
            batch: list[Order] = []
            for idx in missing:
                st = _pick_status()
                batch.append(
                    _build_order(
                        tenant_id=tenant.id,
                        depot=depot,
                        courier_ids=courier_ids,
                        target=target,
                        idx=idx,
                        status=st,
                        today=today,
                    )
                )
            if batch:
                session.add_all(batch)
                orders_added += len(batch)
        await session.flush()

        route_count = int(
            (await session.scalar(select(func.count(Route.id)).where(Route.optimization_run_id == anchor.id))) or 0
        )
        routes_added = 0
        if route_count == 0:
            routes_added = await _seed_fleet_routes(
                session,
                tenant_id=tenant.id,
                courier_ids=courier_ids,
                anchor_run=anchor,
                days=days,
                today=today,
            )

        anchor.routes_created = int(
            (await session.scalar(select(func.count(Route.id)).where(Route.optimization_run_id == anchor.id))) or 0
        )

        # Plan-stability + counterfactual snapshots are populated in
        # parallel with the fleet routes; the cron task does the live
        # version once the pilot runs real solves.
        stability_existing = int(
            (
                await session.scalar(
                    select(func.count(PlanStabilityScore.id)).where(
                        PlanStabilityScore.tenant_id == tenant.id
                    )
                )
            )
            or 0
        )
        if stability_existing == 0:
            await _seed_plan_stability_scores(
                session,
                tenant_id=tenant.id,
                anchor_run=anchor,
                days=min(days, 30),
                today=today,
            )

        # Backfill OrderStatusHistory so the Forensics drawer demos a
        # real timeline on fresh installs. Idempotent at the seed level
        # because we only run when the table is empty for these orders.
        history_existing = int(
            (
                await session.scalar(
                    select(func.count(OrderStatusHistory.id))
                    .join(Order, Order.id == OrderStatusHistory.order_id)
                    .where(
                        Order.tenant_id == tenant.id,
                        Order.external_ref.like(f"{INSIGHTS_DEMO_PREFIX}%"),
                    )
                )
            )
            or 0
        )
        if history_existing == 0:
            await _seed_status_history_for_seeded_orders(
                session, tenant_id=tenant.id
            )

        await session.commit()
        print(
            f"[seed_insights_demo] added {orders_added} orders, {routes_added} fleet routes "
            f"({days} day window ending {today})"
        )

    if with_scenarios:
        from app.seed_optimization_savings_demo import main as seed_scenarios_main

        await seed_scenarios_main(days=min(days, 35), force=force)

    return orders_added, routes_added


def cli() -> None:
    parser = argparse.ArgumentParser(description="Seed realistic Insights demo data")
    parser.add_argument("--days", type=int, default=35, help="Past days to cover (7–90, default 35)")
    parser.add_argument("--force", action="store_true", help="Remove prior insight-demo rows then re-seed")
    parser.add_argument(
        "--with-scenarios",
        action="store_true",
        help="Also seed route-scenario comparisons (see seed_optimization_savings_demo)",
    )
    args = parser.parse_args()
    asyncio.run(main(days=args.days, force=args.force, with_scenarios=args.with_scenarios))
    sys.exit(0)


if __name__ == "__main__":
    cli()
