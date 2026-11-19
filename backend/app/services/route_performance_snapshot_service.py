"""Materialize a `RoutePerformanceSnapshot` when a route completes.

Inputs we already have:
  * `Route.actual_distance_km` (or planned_distance_km as fallback)
  * `Route.started_at` / `Route.completed_at` → actual duration
  * `RouteStop.planned_arrival` vs `actual_arrival` → per-stop arrival delta
  * `Courier.vehicle_spec.fuel_efficiency_km_per_liter`
  * `Courier.vehicle_spec.co2_emissions_g_per_km`
  * `Courier.vehicle_spec.fuel_type`
  * `Courier.depot.province_code` + `EnergyPriceSnapshot` for fuel_unit_price

Outputs the headliner cost KPI (`actual_fuel_cost_try`) plus the
sustainability secondary (`co2_kg`). The counterfactual baseline
columns stay NULL — Layer 2 fills them when the greedy alongside-solver
lands.

Idempotent: each route has a unique constraint on `route_performance_snapshots`,
so calling `materialize_route_snapshot(route_id)` twice updates the
existing row instead of duplicating.
"""
from __future__ import annotations

import logging
import statistics
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.courier import Courier
from app.models.depot import Depot
from app.models.energy_price import EnergyPriceSnapshot
from app.models.enums import FuelType
from app.models.route import Route, RouteStop
from app.models.route_performance_snapshot import RoutePerformanceSnapshot
from app.models.vehicle_spec import VehicleSpec

logger = logging.getLogger(__name__)


# Fuel-to-CO2 conversion factors. Source: EPA reference values; close
# enough for ESG dashboards. Used only when VehicleSpec.co2_emissions_g_per_km
# isn't available.
_FUEL_CO2_KG_PER_LITER = {
    FuelType.GASOLINE: 2.31,
    FuelType.DIESEL: 2.68,
    FuelType.LPG: 1.51,
    FuelType.HYBRID: 1.85,
    FuelType.ELECTRIC: 0.0,
}


async def materialize_route_snapshot(
    db: AsyncSession, route_id: uuid.UUID
) -> RoutePerformanceSnapshot | None:
    """Build (or refresh) the snapshot row for one completed route.

    Returns the persisted snapshot, or None if the route wasn't found.
    Idempotent on (route_id) via ON CONFLICT — re-running over the same
    route updates the row in place.
    """
    route = (
        await db.execute(
            select(Route)
            .where(Route.id == route_id)
            .options(
                selectinload(Route.stops),
                selectinload(Route.courier).selectinload(Courier.vehicle_spec),
                selectinload(Route.courier).selectinload(Courier.depot),
            )
        )
    ).scalar_one_or_none()
    if route is None:
        logger.warning(
            "materialize_route_snapshot: route %s not found", route_id
        )
        return None

    operational_date = _operational_date_for(route)
    stops = list(route.stops)
    deliveries = [s for s in stops if s.stop_type.value == "DELIVERY"]

    # Planned aggregates from solver output.
    planned_distance_m = (route.planned_distance_km or 0.0) * 1000.0
    planned_stop_count = len(stops)
    planned_duration_s = _compute_planned_duration_s(stops)

    # Actual from execution.
    actual_distance_m = (
        (route.actual_distance_km or 0.0) * 1000.0
        if route.actual_distance_km is not None
        else planned_distance_m
    )
    actual_duration_s = _compute_actual_duration_s(route)
    actual_stop_count = sum(1 for s in stops if s.actual_arrival is not None)

    # Per-stop arrival delta percentiles (positive = late).
    deltas = [
        int((s.actual_arrival - s.planned_arrival).total_seconds())
        for s in deliveries
        if s.actual_arrival is not None and s.planned_arrival is not None
    ]
    p50 = p95 = max_delta = None
    if deltas:
        sorted_deltas = sorted(deltas)
        p50 = int(statistics.median(sorted_deltas))
        max_delta = sorted_deltas[-1]
        p95 = sorted_deltas[max(0, int(0.95 * len(sorted_deltas)) - 1)]

    # Fuel + cost — the headliner.
    fuel_summary = _compute_fuel_and_cost(
        actual_distance_m=actual_distance_m,
        operational_date=operational_date,
        vehicle_spec=route.courier.vehicle_spec if route.courier else None,
        depot=route.courier.depot if route.courier else None,
    )
    fuel_unit_price_try = fuel_summary.get("unit_price_try")
    actual_fuel_liters = fuel_summary.get("fuel_liters")
    actual_fuel_cost_try = fuel_summary.get("fuel_cost_try")
    co2_kg = fuel_summary.get("co2_kg")

    # Fall back to the energy-price lookup over a small recent window if
    # no exact-date row exists for the depot's province.
    if fuel_unit_price_try is None and route.courier and route.courier.depot:
        fuel_unit_price_try = await _fallback_recent_fuel_price(
            db,
            province_code=route.courier.depot.province_code,
            fuel_type=(
                route.courier.vehicle_spec.fuel_type
                if route.courier.vehicle_spec is not None
                else None
            ),
            target_date=operational_date,
        )
        if (
            fuel_unit_price_try is not None
            and actual_fuel_liters is not None
        ):
            actual_fuel_cost_try = fuel_unit_price_try * actual_fuel_liters

    # Approximate "distance cost" — what the route would have cost in
    # currency at a flat per-km rate (today: same as fuel cost; later
    # tiers can add toll, depreciation, etc.).
    actual_distance_cost_try = actual_fuel_cost_try
    total_route_cost_try = actual_fuel_cost_try

    values = {
        "route_id": route.id,
        "tenant_id": route.tenant_id,
        "courier_id": route.courier_id,
        "optimization_run_id": route.optimization_run_id,
        "operational_date": operational_date,
        "planned_distance_m": planned_distance_m,
        "planned_duration_s": planned_duration_s,
        "planned_stop_count": planned_stop_count,
        "planned_capacity_used_pct": None,  # filled by a follow-up cron join
        "actual_distance_m": actual_distance_m,
        "actual_duration_s": actual_duration_s,
        "actual_stop_count": actual_stop_count,
        "delta_arrival_p50_s": p50,
        "delta_arrival_p95_s": p95,
        "delta_arrival_max_s": max_delta,
        "actual_fuel_liters": actual_fuel_liters,
        "fuel_unit_price_try": fuel_unit_price_try,
        "actual_fuel_cost_try": actual_fuel_cost_try,
        "actual_distance_cost_try": actual_distance_cost_try,
        "total_route_cost_try": total_route_cost_try,
        "co2_kg": co2_kg,
    }
    stmt = (
        pg_insert(RoutePerformanceSnapshot)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["route_id"],
            set_={
                k: v for k, v in values.items() if k not in ("route_id",)
            },
        )
        .returning(RoutePerformanceSnapshot.id)
    )
    snapshot_id = (await db.execute(stmt)).scalar_one()
    return await db.get(RoutePerformanceSnapshot, snapshot_id)


def _operational_date_for(route: Route) -> date:
    """Pick the operational date for the route — completed_at > started_at >
    earliest planned_arrival."""
    if route.completed_at is not None:
        return route.completed_at.date()
    if route.started_at is not None:
        return route.started_at.date()
    if route.stops:
        first = min(route.stops, key=lambda s: s.planned_arrival)
        return first.planned_arrival.date()
    return date.today()


def _compute_planned_duration_s(stops: list[RouteStop]) -> int:
    if not stops:
        return 0
    earliest = min(s.planned_arrival for s in stops)
    latest_dep = max(
        ((s.planned_departure or s.planned_arrival) for s in stops),
        default=earliest,
    )
    return int((latest_dep - earliest).total_seconds())


def _compute_actual_duration_s(route: Route) -> int | None:
    if route.started_at and route.completed_at:
        return int((route.completed_at - route.started_at).total_seconds())
    return None


def _compute_fuel_and_cost(
    *,
    actual_distance_m: float,
    operational_date: date,
    vehicle_spec: VehicleSpec | None,
    depot: Depot | None,
) -> dict:
    """Compute fuel_liters, fuel_cost_try, and co2_kg given a route's
    actual distance + the courier's vehicle + the depot's province.

    The fuel-price lookup is deferred to the caller (async DB join);
    this helper just runs the arithmetic given what's in hand.
    """
    if vehicle_spec is None or actual_distance_m <= 0:
        return {}
    distance_km = actual_distance_m / 1000.0
    # Fuel volume burned. NULL for electric vehicles (no liquid fuel).
    fuel_liters = None
    if (
        vehicle_spec.fuel_type != FuelType.ELECTRIC
        and vehicle_spec.fuel_efficiency_km_per_liter
        and vehicle_spec.fuel_efficiency_km_per_liter > 0
    ):
        fuel_liters = distance_km / vehicle_spec.fuel_efficiency_km_per_liter

    # CO2 — prefer the vehicle's per-km coefficient if set, else fall back
    # to the fuel-volume × kg/L factor.
    co2_kg = None
    if vehicle_spec.co2_emissions_g_per_km is not None:
        co2_kg = (vehicle_spec.co2_emissions_g_per_km * distance_km) / 1000.0
    elif fuel_liters is not None:
        co2_kg = fuel_liters * _FUEL_CO2_KG_PER_LITER.get(
            vehicle_spec.fuel_type, 2.31
        )

    return {
        "fuel_liters": fuel_liters,
        "co2_kg": co2_kg,
        # `unit_price_try` and `fuel_cost_try` left for the DB-aware
        # caller below; this pure-math helper doesn't query.
    }


async def _fallback_recent_fuel_price(
    db: AsyncSession,
    *,
    province_code: str | None,
    fuel_type: FuelType | None,
    target_date: date,
    lookback_days: int = 14,
) -> float | None:
    """Look up the most recent EnergyPriceSnapshot within ±14 days of
    `target_date` for the given (province, fuel_type). Returns None if
    nothing close enough is on file."""
    if fuel_type is None:
        return None
    earliest = target_date - timedelta(days=lookback_days)
    latest = target_date + timedelta(days=lookback_days)
    filters = [
        EnergyPriceSnapshot.fuel_type == fuel_type,
        EnergyPriceSnapshot.effective_date >= earliest,
        EnergyPriceSnapshot.effective_date <= latest,
    ]
    if province_code is not None:
        filters.append(EnergyPriceSnapshot.province_code == province_code)
    row = (
        await db.execute(
            select(EnergyPriceSnapshot)
            .where(*filters)
            .order_by(EnergyPriceSnapshot.effective_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None and province_code is not None:
        # Try without the province filter as a wider safety net.
        row = (
            await db.execute(
                select(EnergyPriceSnapshot)
                .where(
                    EnergyPriceSnapshot.fuel_type == fuel_type,
                    EnergyPriceSnapshot.effective_date >= earliest,
                    EnergyPriceSnapshot.effective_date <= latest,
                )
                .order_by(EnergyPriceSnapshot.effective_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return float(row.price_try) if row is not None else None
