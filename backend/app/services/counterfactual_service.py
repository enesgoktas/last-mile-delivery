"""Run the counterfactual baseline alongside every real solve.

Layer 2a — load-bearing piece of the "did our solver save money?"
story. For every `dispatch_batch` or `dispatch_range` invocation:

  1. The real solver runs (Rich ALNS).
  2. This service runs the `human_dispatcher_baseline` greedy over the
     same `VRPInput` — typically <1 second on pilot-scale problems.
  3. We compute fuel cost + CO₂ for both solutions using the per-courier
     vehicle spec + a single province-fuel-price lookup, then persist:
       - One `optimization_counterfactual` row keyed by run_id
       - Per-route `baseline_fuel_cost_try` + `baseline_distance_m`
         denormalized onto `route_performance_snapshots` so the
         cost-savings dashboard can do a flat `SUM(...)`.

Failure is non-fatal: if the greedy crashes or the price lookup misses,
we log + skip without ever blocking the real run.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.algorithms.adapters.human_dispatcher import HumanDispatcherBaselineAdapter
from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.vrp_solver import VRPInput, VRPSolution
from app.models.courier import Courier
from app.models.energy_price import EnergyPriceSnapshot
from app.models.enums import FuelType
from app.models.optimization_counterfactual import (
    CounterfactualBaselineKind,
    OptimizationCounterfactual,
)
from app.models.route_performance_snapshot import RoutePerformanceSnapshot
from app.services.route_performance_snapshot_service import (
    _FUEL_CO2_KG_PER_LITER,
    _fallback_recent_fuel_price,
)

logger = logging.getLogger(__name__)


_BASELINE_TIME_BUDGET_S = 1.0


async def run_and_persist_counterfactual(
    db: AsyncSession,
    *,
    vrp_input: VRPInput,
    tenant_id: uuid.UUID,
    optimization_run_id: uuid.UUID,
    real_solution: VRPSolution,
    operational_date: date,
) -> OptimizationCounterfactual | None:
    """Run the greedy baseline and persist its cost vs the real solution.

    Returns the persisted counterfactual row, or None if the baseline
    couldn't run (e.g. empty input). The caller is responsible for
    flushing; this function does its own `add` only.

    Idempotent via the `(optimization_run_id, baseline_kind)` unique
    constraint — re-running over the same run updates the existing row.
    """
    if not vrp_input.orders or not vrp_input.vehicles:
        return None

    started = time.monotonic()
    try:
        baseline_result = HumanDispatcherBaselineAdapter().solve(
            vrp_input, SolveOptions(time_budget_s=_BASELINE_TIME_BUDGET_S)
        )
    except Exception:
        logger.warning(
            "counterfactual greedy failed for run %s — skipping",
            optimization_run_id,
            exc_info=True,
        )
        return None
    baseline = baseline_result.solution
    elapsed_s = time.monotonic() - started

    # ── Compute fuel + CO2 for both sides using each courier's vehicle
    # spec. The real solve's per-route fuel was already materialized to
    # `route_performance_snapshots`; we recompute the totals here for the
    # canonical counterfactual row, and write per-route baseline columns
    # back onto the snapshots in one bulk pass.
    courier_specs = await _load_courier_specs(
        db,
        courier_ids={
            _strip_day_suffix(v.physical_courier_id or v.courier_id)
            for v in vrp_input.vehicles
        },
    )
    fuel_price = await _resolve_fuel_price(
        db,
        courier_specs=courier_specs,
        operational_date=operational_date,
    )

    baseline_totals = _aggregate_solution(
        solution=baseline, courier_specs=courier_specs, fuel_price_try=fuel_price
    )
    real_totals = _aggregate_solution(
        solution=real_solution, courier_specs=courier_specs, fuel_price_try=fuel_price
    )

    distance_saved = baseline_totals["distance_m"] - real_totals["distance_m"]
    fuel_cost_saved = (
        (baseline_totals["fuel_cost_try"] or 0.0)
        - (real_totals["fuel_cost_try"] or 0.0)
        if baseline_totals["fuel_cost_try"] is not None
        and real_totals["fuel_cost_try"] is not None
        else None
    )
    co2_saved = (
        (baseline_totals["co2_kg"] or 0.0) - (real_totals["co2_kg"] or 0.0)
        if baseline_totals["co2_kg"] is not None
        and real_totals["co2_kg"] is not None
        else None
    )

    values = {
        "optimization_run_id": optimization_run_id,
        "tenant_id": tenant_id,
        "baseline_kind": CounterfactualBaselineKind.GREEDY_NEAREST_NEIGHBOR,
        "baseline_route_count": len(baseline.routes),
        "baseline_distance_m": baseline_totals["distance_m"],
        "baseline_duration_s": baseline_totals["duration_s"],
        "baseline_assigned_count": baseline_totals["assigned_count"],
        "baseline_unassigned_count": len(baseline.unassigned_orders),
        "baseline_fuel_liters": baseline_totals["fuel_liters"],
        "baseline_fuel_cost_try": baseline_totals["fuel_cost_try"],
        "baseline_co2_kg": baseline_totals["co2_kg"],
        "actual_distance_m": real_totals["distance_m"],
        "actual_fuel_cost_try": real_totals["fuel_cost_try"],
        "actual_co2_kg": real_totals["co2_kg"],
        "distance_saved_m": distance_saved,
        "fuel_cost_saved_try": fuel_cost_saved,
        "co2_saved_kg": co2_saved,
        "baseline_elapsed_s": elapsed_s,
        "baseline_status": baseline.solver_status.value,
    }
    stmt = (
        pg_insert(OptimizationCounterfactual)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["optimization_run_id", "baseline_kind"],
            set_={k: v for k, v in values.items() if k != "optimization_run_id"},
        )
        .returning(OptimizationCounterfactual.id)
    )
    cf_id = (await db.execute(stmt)).scalar_one()

    # ── Denormalize per-route baseline cost onto the snapshot rows so the
    # cost-savings dashboard reads a single indexed table.
    await _denormalize_baseline_to_snapshots(
        db,
        baseline_solution=baseline,
        courier_specs=courier_specs,
        fuel_price_try=fuel_price,
        optimization_run_id=optimization_run_id,
    )

    return await db.get(OptimizationCounterfactual, cf_id)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _strip_day_suffix(courier_id: str) -> str:
    """`<uuid>__day3` → `<uuid>`. Range dispatch expands couriers into
    virtual day-vehicles; the physical id is everything before the
    suffix."""
    if "__day" in courier_id:
        return courier_id.partition("__day")[0]
    return courier_id


async def _load_courier_specs(
    db: AsyncSession, *, courier_ids: set[str]
) -> dict[uuid.UUID, dict]:
    """Return {courier_uuid → {fuel_type, km_per_liter, co2_g_per_km,
    province_code}} for every referenced physical courier."""
    if not courier_ids:
        return {}
    uuids = []
    for raw in courier_ids:
        try:
            uuids.append(uuid.UUID(raw))
        except (ValueError, TypeError):
            continue
    if not uuids:
        return {}
    rows = (
        await db.execute(
            select(Courier).where(Courier.id.in_(uuids))
        )
    ).scalars().all()
    by_id: dict[uuid.UUID, dict] = {}
    for c in rows:
        vs = c.vehicle_spec if hasattr(c, "vehicle_spec") else None
        # Lazy-load if not pre-selected.
        if vs is None and c.vehicle_spec_id is not None:
            from app.models.vehicle_spec import VehicleSpec

            vs = await db.get(VehicleSpec, c.vehicle_spec_id)
        depot = None
        if c.depot_id is not None:
            from app.models.depot import Depot

            depot = await db.get(Depot, c.depot_id)
        by_id[c.id] = {
            "fuel_type": vs.fuel_type if vs is not None else None,
            "km_per_liter": (
                vs.fuel_efficiency_km_per_liter if vs is not None else None
            ),
            "co2_g_per_km": (
                vs.co2_emissions_g_per_km if vs is not None else None
            ),
            "province_code": depot.province_code if depot is not None else None,
        }
    return by_id


async def _resolve_fuel_price(
    db: AsyncSession,
    *,
    courier_specs: dict[uuid.UUID, dict],
    operational_date: date,
) -> float | None:
    """Pick a representative fuel price for the run. v1 strategy: use the
    most common (province, fuel_type) across the courier set and look it
    up via the same fallback helper that `route_performance_snapshot_service`
    uses for individual routes."""
    if not courier_specs:
        return None
    counts: dict[tuple[str | None, FuelType | None], int] = defaultdict(int)
    for spec in courier_specs.values():
        counts[(spec.get("province_code"), spec.get("fuel_type"))] += 1
    (province, fuel_type), _ = max(counts.items(), key=lambda kv: kv[1])
    if fuel_type is None:
        return None
    return await _fallback_recent_fuel_price(
        db,
        province_code=province,
        fuel_type=fuel_type,
        target_date=operational_date,
    )


def _aggregate_solution(
    *,
    solution: VRPSolution,
    courier_specs: dict[uuid.UUID, dict],
    fuel_price_try: float | None,
) -> dict:
    """Reduce a `VRPSolution` to {distance_m, duration_s, fuel_liters,
    fuel_cost_try, co2_kg, assigned_count} for storage."""
    total_distance_m = 0.0
    total_duration_s = 0
    total_fuel_liters = 0.0
    total_co2_kg = 0.0
    have_fuel = False
    assigned_count = 0
    for route in solution.routes:
        total_distance_m += route.total_distance_m
        total_duration_s += route.total_duration_s
        # Each DELIVERY stop counts as one assigned order.
        for stop in route.stops:
            if stop.stop_type == "DELIVERY":
                assigned_count += 1
        # Map to physical courier for fuel calc.
        physical_id = _strip_day_suffix(route.courier_id)
        try:
            courier_uuid = uuid.UUID(physical_id)
        except ValueError:
            continue
        spec = courier_specs.get(courier_uuid)
        if spec is None:
            continue
        km = route.total_distance_m / 1000.0
        if (
            spec["fuel_type"] is not None
            and spec["fuel_type"] != FuelType.ELECTRIC
            and spec.get("km_per_liter")
            and spec["km_per_liter"] > 0
        ):
            liters = km / spec["km_per_liter"]
            total_fuel_liters += liters
            have_fuel = True
            if spec.get("co2_g_per_km") is not None:
                total_co2_kg += (spec["co2_g_per_km"] * km) / 1000.0
            else:
                total_co2_kg += liters * _FUEL_CO2_KG_PER_LITER.get(
                    spec["fuel_type"], 2.31
                )
    fuel_cost_try = None
    if have_fuel and fuel_price_try is not None:
        fuel_cost_try = total_fuel_liters * fuel_price_try
    return {
        "distance_m": total_distance_m,
        "duration_s": total_duration_s,
        "fuel_liters": total_fuel_liters if have_fuel else None,
        "fuel_cost_try": fuel_cost_try,
        "co2_kg": total_co2_kg if have_fuel else None,
        "assigned_count": assigned_count,
    }


async def _denormalize_baseline_to_snapshots(
    db: AsyncSession,
    *,
    baseline_solution: VRPSolution,
    courier_specs: dict[uuid.UUID, dict],
    fuel_price_try: float | None,
    optimization_run_id: uuid.UUID,
) -> None:
    """Sum the greedy baseline's per-courier distance + fuel cost and
    write them onto each `route_performance_snapshots` row that belongs
    to this run. Lets the cost-savings dashboard answer date-range
    questions with a flat `SUM(baseline_fuel_cost_try - actual_fuel_cost_try)`.

    We attribute baseline cost to each real route proportionally to its
    contribution to the run's total real distance — a rough but
    monotonic mapping that's good enough for an aggregate KPI."""
    snapshots = (
        await db.execute(
            select(RoutePerformanceSnapshot).where(
                RoutePerformanceSnapshot.optimization_run_id == optimization_run_id
            )
        )
    ).scalars().all()
    if not snapshots:
        return

    # Total baseline distance + fuel cost (single number per kind).
    baseline_distance_m = sum(r.total_distance_m for r in baseline_solution.routes)
    baseline_fuel_cost_try = None
    if fuel_price_try is not None:
        total_liters = 0.0
        for route in baseline_solution.routes:
            physical_id = _strip_day_suffix(route.courier_id)
            try:
                spec = courier_specs.get(uuid.UUID(physical_id))
            except ValueError:
                spec = None
            if spec is None or not spec.get("km_per_liter"):
                continue
            km = route.total_distance_m / 1000.0
            total_liters += km / spec["km_per_liter"]
        baseline_fuel_cost_try = total_liters * fuel_price_try if total_liters else 0.0

    total_real_distance_m = sum(s.actual_distance_m or 0.0 for s in snapshots) or 1.0
    for s in snapshots:
        share = (s.actual_distance_m or 0.0) / total_real_distance_m
        s.baseline_distance_m = baseline_distance_m * share
        if baseline_fuel_cost_try is not None:
            s.baseline_fuel_cost_try = baseline_fuel_cost_try * share
