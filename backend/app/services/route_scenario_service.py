from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from redis import Redis
from sqlalchemy import delete, select
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.algorithms import osrm_client
from app.algorithms.warm_start_cache import clear_cache as clear_warm_start_cache
from app.config import settings
from app.database import get_async_session_factory
from app.models.courier import Courier
from app.models.depot import Depot
from app.models.enums import (
    AvailabilityStatus,
    FuelType,
    OptimizationRunStatus,
    OptimizationRunType,
    OrderStatus,
    RouteStatus,
    StopType,
)
from app.models.order import Order
from app.models.route import Route
from app.models.route_scenario import RouteScenario, RouteScenarioComparison
from app.schemas.operations_policy import FleetUtilization, RoutingObjective
from app.schemas.route import RunPreviewResponse
from app.schemas.route_scenario import (
    RouteScenarioComparisonResponse,
    RouteScenarioSummary,
)
from app.services import (
    operations_policy_service,
    optimization_run_service,
    traffic_profile_service,
)
from app.services.constraint_service import get_global_constraints
from app.services.dispatch_service import (
    _build_courier_profile_map,
    _build_location_index,
    _build_time_corrected_duration_matrices,
    _load_order_package_summaries,
    _persist_solution,
    _resolve_batch_solver_pass_time_limits,
    _resolve_courier_osrm_profiles,
    _serialize_cost_breakdown,
    _solve_batch_variant,
    _solve_human_dispatcher_baseline_variant,
    _validate_batch_solution,
)
from app.services.dispatch_vrp_helpers import should_skip_multi_trip
from app.services.energy_price_service import (
    EnergyPriceBasis,
    get_effective_energy_prices,
    resolve_turkey_province,
)
from app.services.order_service import apply_order_status_transition
from app.utils.compat import UTC, utcnow_naive
from app.utils.tenant_context import get_current_tenant_id, tenant_filter

logger = logging.getLogger(__name__)

DAILY_COMPARISON_ORDER_CAP = 1000
COMPARISON_LOCK_KEY = "optimization:batch:lock"
HUMAN_DISPATCHER_BASELINE_KEY = "human_dispatcher_baseline"

SCENARIO_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": HUMAN_DISPATCHER_BASELINE_KEY,
        "label": "Human dispatcher baseline",
        "routing_objective": "SERVICE_FIRST",
        "fleet_utilization": "FULL_FLEET",
        "solver_kind": "human_dispatcher",
    },
    {
        "key": "rich_alns_service_first",
        "label": "Rich ALNS (service-first)",
        "routing_objective": "SERVICE_FIRST",
        "fleet_utilization": "FULL_FLEET",
        "solver_kind": "rich_alns",
    },
    {
        "key": "rich_alns_fuel_aware",
        "label": "Rich ALNS (fuel-aware)",
        "routing_objective": "FUEL_AWARE",
        "fleet_utilization": "FULL_FLEET",
        "solver_kind": "rich_alns",
    },
    {
        "key": "rich_alns_efficient",
        "label": "Rich ALNS (efficient)",
        "routing_objective": "FUEL_AWARE",
        "fleet_utilization": "EFFICIENT",
        "solver_kind": "rich_alns",
    },
    {
        "key": "fuel_aware_full_fleet",
        "label": "Fuel-aware + Full fleet",
        "routing_objective": "FUEL_AWARE",
        "fleet_utilization": "FULL_FLEET",
        "solver_kind": "ortools_cvrptw",
    },
)


def generate_scenario_definitions() -> list[dict[str, Any]]:
    return [dict(item) for item in SCENARIO_DEFINITIONS]


# How many scenarios to run concurrently inside `run_comparison`. The rich_alns
# Cython hot path and OR-Tools both release the GIL during solve, so this is
# real CPU parallelism. Capped low so we don't oversubscribe a 2-core Celery
# worker; raise via settings if you have more cores to spare.
_SCENARIO_CONCURRENCY = 3

# Reused via `dispatch_vrp_helpers` so the production `dispatch_batch` path
# can apply the same skip-refinement gate without duplicating the helper.
from app.services.dispatch_vrp_helpers import (
    REFINEMENT_SKIP_THRESHOLD as _REFINEMENT_SKIP_THRESHOLD,
)
from app.services.dispatch_vrp_helpers import (
    matrix_max_relative_delta as _matrix_max_relative_delta,
)


async def _run_single_scenario(
    *,
    scenario_id: uuid.UUID,
    problem: dict[str, Any],
    semaphore: asyncio.Semaphore,
    capture_into: dict[str, Any] | None = None,
) -> bool:
    """Solve and persist one scenario inside its own AsyncSession.

    Returns True on success. Failures are logged + persisted as FAILED but do
    not raise — sibling scenarios run independently.

    If `capture_into` is provided, the scenario's in-memory VRPSolution is
    written under `capture_into["solution"]` on success. This lets a caller
    grab the solution without a DB round-trip — used for the OR-Tools wave-1
    warm-start hand-off into rich_alns wave 2.
    """
    async with semaphore:
        async with get_async_session_factory()() as db:
            scenario = await db.get(RouteScenario, scenario_id)
            if scenario is None:
                logger.error("Scenario %s vanished before run", scenario_id)
                return False
            try:
                scenario.status = "RUNNING"
                await db.commit()
                result = await _solve_and_persist_scenario(db, scenario=scenario, problem=problem)
                scenario.status = "SUCCEEDED"
                scenario.optimization_run_id = result["optimization_run_id"]
                scenario.summary = result["summary"]
                scenario.completed_at = utcnow_naive()
                await db.commit()
                if capture_into is not None:
                    capture_into["solution"] = result.get("solution")
                return True
            except Exception as exc:
                await db.rollback()
                logger.exception("Scenario %s failed", scenario.key)
                fresh = await db.get(RouteScenario, scenario_id)
                if fresh is not None:
                    fresh.status = "FAILED"
                    fresh.error_message = str(exc)
                    fresh.completed_at = utcnow_naive()
                    await db.commit()
                return False


async def create_comparison(
    db: AsyncSession,
    *,
    principal,
    target_date: date,
) -> RouteScenarioComparison:
    tenant_id = get_current_tenant_id(principal)
    policy = await operations_policy_service.get_operations_policy(db, principal)
    comparison = RouteScenarioComparison(
        tenant_id=tenant_id,
        target_date=target_date,
        status="QUEUED",
        current_policy_snapshot={"optimization": policy.optimization.model_dump(mode="json")},
    )
    db.add(comparison)
    await db.flush()

    for definition in SCENARIO_DEFINITIONS:
        db.add(
            RouteScenario(
                comparison_id=comparison.id,
                tenant_id=tenant_id,
                key=definition["key"],
                label=definition["label"],
                routing_objective=definition["routing_objective"],
                fleet_utilization=definition["fleet_utilization"],
                solver_kind=definition["solver_kind"],
                status="PENDING",
            )
        )
    await db.flush()
    await db.refresh(comparison, attribute_names=["scenarios"])
    comparison.scenarios.sort(key=_scenario_sort_key)
    return comparison


async def set_comparison_task_id(
    db: AsyncSession,
    *,
    comparison_id: uuid.UUID,
    task_id: str,
    principal,
) -> RouteScenarioComparison:
    comparison = await get_comparison_model(db, comparison_id, principal)
    comparison.task_id = task_id
    comparison.status = "STARTED"
    await db.flush()
    await db.refresh(comparison, attribute_names=["scenarios"])
    comparison.scenarios.sort(key=_scenario_sort_key)
    return comparison


async def get_comparison_model(
    db: AsyncSession,
    comparison_id: uuid.UUID,
    principal,
) -> RouteScenarioComparison:
    result = await db.execute(
        select(RouteScenarioComparison)
        .where(
            RouteScenarioComparison.id == comparison_id,
            tenant_filter(RouteScenarioComparison, principal),
        )
        .options(selectinload(RouteScenarioComparison.scenarios))
    )
    comparison = result.scalar_one_or_none()
    if comparison is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario comparison not found")
    comparison.scenarios.sort(key=_scenario_sort_key)
    return comparison


async def get_comparison(
    db: AsyncSession,
    comparison_id: uuid.UUID,
    principal,
) -> RouteScenarioComparisonResponse:
    return _comparison_response(await get_comparison_model(db, comparison_id, principal))


async def get_scenario_preview(
    db: AsyncSession,
    *,
    comparison_id: uuid.UUID,
    scenario_id: uuid.UUID,
    principal,
) -> RunPreviewResponse:
    comparison = await get_comparison_model(db, comparison_id, principal)
    scenario = _find_scenario(comparison, scenario_id)
    if scenario.optimization_run_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Scenario has no route preview yet")
    return await optimization_run_service.get_run_preview(db, scenario.optimization_run_id, principal)


async def delete_comparison(
    db: AsyncSession,
    *,
    comparison_id: uuid.UUID,
    principal,
) -> int:
    comparison = await get_comparison_model(db, comparison_id, principal)
    if comparison.status == "APPLIED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Applied comparisons cannot be discarded")
    deleted_routes = await _delete_draft_routes_for_scenarios(db, comparison.scenarios)
    await db.delete(comparison)
    await db.flush()
    return deleted_routes


async def select_scenario(
    db: AsyncSession,
    *,
    comparison_id: uuid.UUID,
    scenario_id: uuid.UUID,
    principal,
) -> tuple[RouteScenarioComparisonResponse, int, int]:
    tenant_id = get_current_tenant_id(principal)
    lock_owner = str(uuid.uuid4())
    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    acquired = redis_client.set(COMPARISON_LOCK_KEY, lock_owner, nx=True, ex=300)
    if not acquired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Another optimization is running")

    try:
        comparison = await get_comparison_model(db, comparison_id, principal)
        if comparison.status == "APPLIED":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Scenario comparison is already applied")
        scenario = _find_scenario(comparison, scenario_id)
        if scenario.optimization_run_id is None or scenario.status != "SUCCEEDED":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only successful scenarios can be selected")

        selected_routes = await _load_draft_routes(db, [scenario.optimization_run_id], tenant_id)
        if not selected_routes:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Selected scenario has no draft routes")

        order_ids = _order_ids_from_routes(selected_routes)
        order_result = await db.execute(
            select(Order).where(
                Order.id.in_(order_ids),
                tenant_filter(Order, tenant_id),
            )
        )
        orders = {order.id: order for order in order_result.scalars().all()}
        changed = [
            str(order_id)
            for order_id in order_ids
            if orders.get(order_id) is None or orders[order_id].status != OrderStatus.PENDING
        ]
        if changed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Some scenario orders are no longer pending",
                    "order_ids": changed,
                },
            )

        approved_routes = await _promote_selected_routes(db, selected_routes, orders)
        sibling_scenarios = [item for item in comparison.scenarios if item.id != scenario.id]
        deleted_routes = await _delete_draft_routes_for_scenarios(db, sibling_scenarios)

        scenario.status = "SELECTED"
        comparison.status = "APPLIED"
        comparison.selected_scenario_id = scenario.id
        comparison.applied_at = utcnow_naive()
        for sibling in sibling_scenarios:
            sibling.status = "DISCARDED" if sibling.status == "SUCCEEDED" else sibling.status
        clear_warm_start_cache(str(tenant_id))
        await db.flush()
        await db.refresh(comparison, attribute_names=["scenarios"])
        return _comparison_response(comparison), approved_routes, deleted_routes
    finally:
        _release_owned_lock(redis_client, key=COMPARISON_LOCK_KEY, owner=lock_owner)


async def run_comparison(
    *,
    comparison_id: uuid.UUID,
    tenant_id: uuid.UUID,
    target_date: date,
) -> dict[str, Any]:
    if not await osrm_client.check_osrm_health():
        await _mark_comparison_failed(
            comparison_id,
            tenant_id,
            "Routing service is unavailable",
            scenario_status="FAILED",
        )
        return {"status": "FAILED", "solver_status": "OSRM_UNAVAILABLE"}

    async with get_async_session_factory()() as db:
        comparison = await _load_worker_comparison(db, comparison_id, tenant_id)
        comparison.status = "RUNNING"
        await db.commit()

        try:
            problem = await _build_daily_problem(db, tenant_id=tenant_id, target_date=target_date)
        except Exception as exc:
            await db.rollback()
            logger.exception("Scenario comparison problem build failed")
            comparison = await _load_worker_comparison(db, comparison_id, tenant_id)
            comparison.status = "FAILED"
            comparison.error_message = str(exc)
            comparison.completed_at = utcnow_naive()
            for scenario in comparison.scenarios:
                scenario.status = "FAILED"
                scenario.error_message = str(exc)
                scenario.completed_at = utcnow_naive()
            await db.commit()
            return {"status": "FAILED", "error": str(exc)}

        if problem["early_result"] is not None:
            comparison = await _load_worker_comparison(db, comparison_id, tenant_id)
            comparison.status = problem["early_result"]["status"]
            comparison.error_message = problem["early_result"].get("error_message")
            comparison.completed_at = utcnow_naive()
            for scenario in comparison.scenarios:
                scenario.status = problem["early_result"]["scenario_status"]
                scenario.summary = problem["early_result"].get("summary")
                scenario.error_message = problem["early_result"].get("error_message")
                scenario.completed_at = utcnow_naive()
            await db.commit()
            return problem["early_result"]

        # Wave 1 = scenarios that don't depend on another solver's output
        # (OR-Tools is the warm-start producer, human dispatcher needs no
        # warm-start). Both run concurrently. Wave 2 = rich_alns variants
        # which read OR-Tools' captured solution as warm-start.
        wave1_scenarios = [
            s for s in comparison.scenarios
            if s.solver_kind in ("ortools_cvrptw", "human_dispatcher")
        ]
        wave2_scenario_ids = [
            s.id for s in comparison.scenarios
            if s.solver_kind not in ("ortools_cvrptw", "human_dispatcher")
        ]

    # Two-wave scheduling so rich_alns scenarios can warm-start from OR-Tools'
    # output. Wave 1 = OR-Tools + human dispatcher (concurrent, neither needs
    # the other). After wave 1 we drop the OR-Tools VRPSolution into
    # `problem["ortools_warm_start"]`, which `_solve_scenario_vrp` reads for
    # rich_alns scenarios and forwards to the rich_alns adapter as
    # `SolveOptions.warm_start_solution`. Wave 2 (rich_alns variants) then
    # runs concurrently. If OR-Tools failed or isn't in the comparison, wave 2
    # just runs without warm-start.
    semaphore = asyncio.Semaphore(_SCENARIO_CONCURRENCY)
    ortools_capture: dict[str, Any] = {}
    wave1_results = await asyncio.gather(
        *[
            _run_single_scenario(
                scenario_id=s.id,
                problem=problem,
                semaphore=semaphore,
                # Only capture OR-Tools' output; human_dispatcher is in wave 1
                # for parallelism but its solution isn't a warm-start source.
                capture_into=(
                    ortools_capture if s.solver_kind == "ortools_cvrptw" else None
                ),
            )
            for s in wave1_scenarios
        ],
        return_exceptions=False,
    )
    if ortools_capture.get("solution") is not None:
        problem["ortools_warm_start"] = ortools_capture["solution"]
        logger.info(
            "OR-Tools wave-1 warm-start captured: %d routes, %d unassigned",
            len(ortools_capture["solution"].routes),
            len(ortools_capture["solution"].unassigned_orders),
        )

    wave2_results = await asyncio.gather(
        *[
            _run_single_scenario(scenario_id=sid, problem=problem, semaphore=semaphore)
            for sid in wave2_scenario_ids
        ],
        return_exceptions=False,
    )
    success_count = sum(1 for ok in (*wave1_results, *wave2_results) if ok)

    async with get_async_session_factory()() as db:

        comparison = await _load_worker_comparison(db, comparison_id, tenant_id)
        comparison.status = (
            "READY"
            if success_count == len(SCENARIO_DEFINITIONS)
            else "PARTIAL"
            if success_count > 0
            else "FAILED"
        )
        comparison.completed_at = utcnow_naive()
        if success_count == 0:
            comparison.error_message = "All route scenarios failed"
        await db.commit()
        return {
            "status": comparison.status,
            "comparison_id": str(comparison.id),
            "successful_scenarios": success_count,
        }


def build_scenario_optimization_settings(
    *,
    base_settings,
    routing_objective: RoutingObjective,
    fleet_utilization: FleetUtilization,
    energy_prices: dict[FuelType, float],
):
    fallback_prices = {
        **base_settings.fuel_prices,
        **energy_prices,
    }
    scenario_settings = operations_policy_service.optimization_settings_for_public_controls(
        routing_objective,
        fleet_utilization=fleet_utilization,
        fuel_prices=fallback_prices,
    )
    return scenario_settings.model_copy(
        update={
            "decomposition_order_threshold": base_settings.decomposition_order_threshold,
            "max_lateness_minutes": base_settings.max_lateness_minutes,
            "max_overtime_minutes": base_settings.max_overtime_minutes,
        }
    )


def summarize_solution(
    *,
    solution,
    orders: Sequence[Order],
    vehicles,
    price_basis_by_courier: dict[str, EnergyPriceBasis],
) -> RouteScenarioSummary:
    order_by_id = {str(order.id): order for order in orders}
    vehicle_by_id = {vehicle.courier_id: vehicle for vehicle in vehicles}
    non_empty_routes = [route for route in solution.routes if route.stops]
    assigned_order_ids = {
        stop.order_id
        for route in non_empty_routes
        for stop in route.stops
        if stop.order_id and stop.stop_type in ("PICKUP", "DELIVERY")
    }
    deliveries = [
        stop
        for route in non_empty_routes
        for stop in route.stops
        if stop.stop_type == "DELIVERY" and stop.order_id
    ]
    on_time_count = 0
    late_minutes = 0
    for stop in deliveries:
        order = order_by_id.get(stop.order_id)
        if order is None:
            continue
        late_seconds = max(stop.arrival_time_s - _seconds_since_midnight(order.latest_delivery), 0)
        if late_seconds == 0:
            on_time_count += 1
        late_minutes += int((late_seconds + 59) // 60)

    energy_cost_try = 0.0
    fuel_liters = 0.0
    electric_kwh = 0.0
    co2_g = 0.0
    co2_available = False
    source_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

    for route in non_empty_routes:
        vehicle = vehicle_by_id.get(route.courier_id)
        if vehicle is None:
            continue
        basis = price_basis_by_courier.get(route.courier_id) or next(iter(price_basis_by_courier.values()))
        for source in basis.sources:
            source_by_key[
                (
                    str(source.get("fuel_type")),
                    str(source.get("source_name")),
                    str(source.get("province_code")),
                )
            ] = source
        distance_km = route.total_distance_m / 1000.0
        fuel_type = vehicle.fuel_type or FuelType.DIESEL
        efficiency = vehicle.fuel_efficiency_km_per_liter
        if efficiency is None or efficiency <= 0:
            continue
        amount = distance_km / efficiency
        unit_price = basis.prices.get(fuel_type)
        if unit_price is None and fuel_type == FuelType.HYBRID:
            unit_price = basis.prices.get(FuelType.GASOLINE)
        if unit_price is None:
            unit_price = basis.prices.get(FuelType.DIESEL) or basis.prices.get(FuelType.GASOLINE) or 0.0
        energy_cost_try += amount * unit_price
        if fuel_type == FuelType.ELECTRIC:
            electric_kwh += amount
        else:
            fuel_liters += amount
        if vehicle.co2_g_per_km is not None:
            co2_available = True
            co2_g += distance_km * vehicle.co2_g_per_km

    objective_breakdown = _serialize_cost_breakdown(solution.cost_breakdown)
    source_statuses = {str(source.get("freshness_status")) for source in source_by_key.values()}
    freshness_status = "FRESH"
    if "STALE" in source_statuses:
        freshness_status = "STALE"
    elif "FALLBACK" in source_statuses:
        freshness_status = "FALLBACK"

    return RouteScenarioSummary(
        route_count=len(non_empty_routes),
        assigned_count=len(assigned_order_ids),
        unassigned_count=len(solution.unassigned_orders),
        vehicles_used=len({route.courier_id for route in non_empty_routes}),
        distance_km=round(solution.total_distance_m / 1000.0, 2),
        duration_minutes=round(solution.total_duration_s / 60.0, 1),
        on_time_rate=round(on_time_count / len(deliveries), 4) if deliveries else 0.0,
        late_minutes=late_minutes,
        realized_energy_cost_try=round(energy_cost_try, 2),
        fuel_liters=round(fuel_liters, 3),
        electric_kwh=round(electric_kwh, 3),
        co2_kg=round(co2_g / 1000.0, 3) if co2_available else None,
        solver_status=solution.solver_status.value,
        objective_breakdown=objective_breakdown,
        energy_price_sources=list(source_by_key.values()),
        energy_price_freshness_status=freshness_status,
        warnings=_energy_warnings(source_by_key.values()),
    )


async def _build_daily_problem(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    target_date: date,
) -> dict[str, Any]:
    date_from = datetime(target_date.year, target_date.month, target_date.day)
    date_to = date_from + timedelta(days=1)
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    order_filters = [
        Order.status == OrderStatus.PENDING,
        Order.latest_delivery > now_naive,
        Order.earliest_delivery < date_to,
        Order.latest_delivery >= date_from,
        tenant_filter(Order, tenant_id),
    ]
    total = (
        await db.execute(select(sa_func.count(Order.id)).where(*order_filters))
    ).scalar_one()
    if total > DAILY_COMPARISON_ORDER_CAP:
        return {
            "early_result": {
                "status": "FAILED",
                "scenario_status": "FAILED",
                "solver_status": "DAILY_CAP_EXCEEDED",
                "error_message": (
                    f"Route comparison is capped at {DAILY_COMPARISON_ORDER_CAP} orders per day; "
                    f"{total} pending orders matched {target_date.isoformat()}."
                ),
            }
        }

    result = await db.execute(select(Order).where(*order_filters).limit(DAILY_COMPARISON_ORDER_CAP))
    orders = list(result.scalars().all())
    if not orders:
        summary = RouteScenarioSummary(solver_status="NO_ORDERS").model_dump(mode="json")
        return {
            "early_result": {
                "status": "READY",
                "scenario_status": "SUCCEEDED",
                "summary": summary,
            }
        }

    result = await db.execute(
        select(Courier)
        .where(
            Courier.availability_status.in_([
                AvailabilityStatus.ONLINE,
                AvailabilityStatus.ON_BREAK,
            ]),
            tenant_filter(Courier, tenant_id),
        )
        .options(selectinload(Courier.vehicle_spec), selectinload(Courier.depot))
    )
    couriers = list(result.scalars().all())
    if not couriers:
        summary = RouteScenarioSummary(
            assigned_count=0,
            unassigned_count=len(orders),
            solver_status="NO_COURIERS",
        ).model_dump(mode="json")
        return {
            "early_result": {
                "status": "READY",
                "scenario_status": "SUCCEEDED",
                "summary": summary,
            }
        }

    # Pull operations_policy up here so the multi-depot flag can gate
    # the OSRM matrix size (additional reload depots only enter the
    # location index when the flag is on). The later `policy = ...` load
    # below is removed; this same variable is reused for the rest of
    # the function.
    policy = await operations_policy_service.get_operations_policy(db, tenant_id)
    locations, loc_index = _build_location_index(
        orders,
        couriers,
        multi_depot_enabled=bool(
            policy.feature_flags.multi_depot_vehicles_enabled
        ),
    )
    osrm_profiles = sorted(_resolve_courier_osrm_profiles(couriers))
    # OSRM table requests are independent per profile and don't share a DB
    # session — fan out so the per-profile latency overlaps instead of
    # serializing. With N profiles this drops the OSRM phase from
    # sum(latencies) to max(latencies).
    matrix_results = await asyncio.gather(
        *[osrm_client.get_distance_matrix(locations, profile=profile) for profile in osrm_profiles]
    )
    matrix_by_profile = dict(zip(osrm_profiles, matrix_results, strict=True))
    matrix = matrix_by_profile.get("driving") or next(iter(matrix_by_profile.values()))
    target_time = datetime.combine(target_date, datetime.now(UTC).time()).replace(tzinfo=None)
    adjusted_by_profile = {
        profile: await traffic_profile_service.adjust_duration_matrix(
            db,
            coords=locations,
            durations=profile_matrix.durations,
            at_time=target_time,
        )
        for profile, profile_matrix in matrix_by_profile.items()
    }
    adjusted_matrix = adjusted_by_profile.get("driving") or next(iter(adjusted_by_profile.values()))

    hourly_by_profile: dict[str, dict[int, list[list[int]]]] = {}
    if settings.TIME_DEPENDENT_TRAVEL_ENABLED:
        for profile, profile_matrix in matrix_by_profile.items():
            hourly_by_profile[profile] = await traffic_profile_service.build_hourly_duration_matrices(
                db,
                coords=locations,
                durations=profile_matrix.durations,
                at_time=target_time,
            )

    # `policy` already loaded above (before the location-index build) so
    # the multi-depot flag could gate the OSRM matrix size.
    global_constraints = await get_global_constraints(db, tenant_id)
    order_packages = await _load_order_package_summaries(db, [order.id for order in orders], tenant_id=tenant_id)
    all_depot_rows = (
        await db.execute(
            select(Depot).where(
                Depot.tenant_id == tenant_id,
                Depot.is_active == True,  # noqa: E712
            )
        )
    ).scalars().all()
    warehouse_locs = {(round(depot.lat, 4), round(depot.lon, 4)) for depot in all_depot_rows}
    courier_depot_locs = {
        str(courier.id): (round(courier.depot_lat, 4), round(courier.depot_lon, 4))
        for courier in couriers
    }
    price_basis_by_courier = await _build_price_basis_by_courier(
        db,
        couriers=couriers,
        as_of=target_date,
        fallback_prices=policy.optimization.fuel_prices,
    )
    skip_multi_trip = should_skip_multi_trip(orders, global_constraints)

    return {
        "early_result": None,
        "orders": orders,
        "couriers": couriers,
        "locations": locations,
        "loc_index": loc_index,
        "matrix": matrix,
        "matrix_by_profile": matrix_by_profile,
        "adjusted_matrix": adjusted_matrix,
        "adjusted_by_profile": adjusted_by_profile,
        "hourly_by_profile": hourly_by_profile,
        "target_time": target_time,
        "global_constraints": global_constraints,
        "order_packages": order_packages,
        "base_optimization_settings": policy.optimization,
        "price_basis_by_courier": price_basis_by_courier,
        "courier_depot_locs": courier_depot_locs,
        "warehouse_locs": warehouse_locs,
        "skip_multi_trip": skip_multi_trip,
    }


async def _solve_and_persist_scenario(
    db: AsyncSession,
    *,
    scenario: RouteScenario,
    problem: dict[str, Any],
) -> dict[str, Any]:
    scenario_settings = build_scenario_optimization_settings(
        base_settings=problem["base_optimization_settings"],
        routing_objective=scenario.routing_objective,
        fleet_utilization=scenario.fleet_utilization,
        energy_prices=_merged_energy_prices(problem["price_basis_by_courier"].values()),
    )
    solution, vrp_vehicles, multi_trip_vehicles, _cluster_vrp_inputs, solver_metrics = await _solve_scenario_vrp(
        db,
        scenario=scenario,
        problem=problem,
        scenario_settings=scenario_settings,
    )

    validation = _validate_batch_solution(
        solution,
        problem["orders"],
        vrp_vehicles,
        multi_trip_vehicles,
        problem["order_packages"],
        scenario_settings,
    )
    run = await optimization_run_service.create_run(
        db,
        tenant_id=scenario.tenant_id,
        run_type=OptimizationRunType.BATCH,
        task_id=None,
    )
    persisted = await _persist_solution(
        db,
        solution,
        problem["orders"],
        run.id,
        draft=True,
        service_anchor=problem["target_time"],
        courier_depot_locs=problem["courier_depot_locs"],
        warehouse_locs=problem["warehouse_locs"],
    )
    summary = summarize_solution(
        solution=solution,
        orders=problem["orders"],
        vehicles=vrp_vehicles,
        price_basis_by_courier=problem["price_basis_by_courier"],
    ).model_dump(mode="json")
    summary["order_ids"] = [str(order.id) for order in problem["orders"]]
    summary["unassigned_order_ids"] = list(solution.unassigned_orders)
    summary["validation"] = validation.summary()
    summary["route_cost_breakdowns"] = persisted["route_cost_breakdowns"]
    summary["solver_metrics"] = solver_metrics
    summary["alns_telemetry"] = solver_metrics.get("alns_telemetry")
    summary["global_feasibility_summary"] = solver_metrics.get("global_feasibility_summary")
    summary["cluster_feasibility_summary"] = solver_metrics.get("cluster_feasibility_summary")
    summary["seed_solution_summary"] = solver_metrics.get("seed_solution_summary")
    summary["repair_summary"] = solver_metrics.get("repair_summary")
    summary["optimization_trace"] = solver_metrics.get("optimization_trace")
    summary["reload_summary"] = solver_metrics.get("reload_summary")
    summary["unassigned_classification_summary"] = solver_metrics.get("unassigned_classification_summary")
    summary["assignment_count_status"] = solver_metrics.get("assignment_count_status")
    summary["invalid_preview"] = validation.error_count > 0

    result = {
        "routes_created": persisted["routes_created"],
        "unassigned_count": len(solution.unassigned_orders),
        "unassigned_order_ids": list(solution.unassigned_orders),
        "solver_status": solution.solver_status.value,
        "total_distance_m": solution.total_distance_m,
        "total_duration_s": solution.total_duration_s,
        "objective_breakdown": summary["objective_breakdown"],
        "cost_breakdown": summary["objective_breakdown"],
        "route_cost_breakdowns": persisted["route_cost_breakdowns"],
        "scenario_key": scenario.key,
        "solver_metrics": solver_metrics,
    }
    await optimization_run_service.complete_run(
        db,
        run_id=run.id,
        status_value=OptimizationRunStatus.PENDING_APPROVAL,
        result=result,
    )
    return {"optimization_run_id": run.id, "summary": summary, "solution": solution}


async def _solve_scenario_vrp(
    db: AsyncSession,
    *,
    scenario: RouteScenario,
    problem: dict[str, Any],
    scenario_settings,
):
    distance_matrices = {
        profile: matrix.distances
        for profile, matrix in problem["matrix_by_profile"].items()
    }
    duration_matrices = {
        profile: adjusted.durations
        for profile, adjusted in problem["adjusted_by_profile"].items()
    }
    if scenario.solver_kind == "human_dispatcher":
        return await _solve_human_dispatcher_baseline_variant(
            db=db,
            tenant_id=str(scenario.tenant_id),
            orders=problem["orders"],
            couriers=problem["couriers"],
            locations=problem["locations"],
            loc_index=problem["loc_index"],
            distance_matrix=problem["matrix"].distances,
            duration_matrix=problem["adjusted_matrix"].durations,
            distance_matrices=distance_matrices,
            duration_matrices=duration_matrices,
            global_constraints=problem["global_constraints"],
            order_packages=problem["order_packages"],
            optimization_settings=scenario_settings,
            skip_multi_trip=problem["skip_multi_trip"],
        )

    spread_fleet = scenario.fleet_utilization == "FULL_FLEET"
    refinement_enabled = bool(settings.TIME_DEPENDENT_TRAVEL_ENABLED and problem["hourly_by_profile"])
    primary_solver_limit, refinement_solver_limit = _resolve_batch_solver_pass_time_limits(
        len(problem["orders"]),
        scenario_settings,
        refinement_enabled=refinement_enabled,
    )
    # Pull a warm-start solution from the comparison-shared dict (populated by
    # the OR-Tools wave-1 run when scenario is rich_alns). None for OR-Tools
    # itself or when no warm start is available.
    warm_start_solution = (
        problem.get("ortools_warm_start") if scenario.solver_kind == "rich_alns" else None
    )
    solution, vrp_vehicles, multi_trip_vehicles, cluster_vrp_inputs, solver_metrics = await _solve_batch_variant(
        db=db,
        tenant_id=str(scenario.tenant_id),
        orders=problem["orders"],
        couriers=problem["couriers"],
        locations=problem["locations"],
        loc_index=problem["loc_index"],
        distance_matrix=problem["matrix"].distances,
        duration_matrix=problem["adjusted_matrix"].durations,
        distance_matrices=distance_matrices,
        duration_matrices=duration_matrices,
        global_constraints=problem["global_constraints"],
        order_packages=problem["order_packages"],
        optimization_settings=scenario_settings,
        cache_solution_result=False,
        spread_fleet=spread_fleet,
        skip_multi_trip=problem["skip_multi_trip"],
        solver_time_limit_seconds=primary_solver_limit,
        solver_kind=scenario.solver_kind,
        warm_start_solution=warm_start_solution,
    )

    if settings.TIME_DEPENDENT_TRAVEL_ENABLED and problem["hourly_by_profile"] and solution.routes:
        courier_profile_map = _build_courier_profile_map(problem["couriers"])
        corrected_duration_matrices = _build_time_corrected_duration_matrices(
            hourly_by_profile=problem["hourly_by_profile"],
            solution=solution,
            courier_profile_map=courier_profile_map,
            departure_hour=problem["target_time"].hour,
        )
        # Skip the refinement pass if the time-corrected matrix is essentially
        # identical to the baseline-adjusted matrix the primary solve used. By
        # definition, when max relative delta on any cell is below ~5%, the
        # primary solution under the corrected matrix evaluates to within ~5%
        # on cost/lateness — already near-optimal under the new conditions.
        # The full second `_solve_batch_variant` call is the most expensive
        # piece of a scenario (~30-60s per ALNS variant); skipping it when no
        # refinement work would actually happen is a free win. The threshold
        # is conservative enough that meaningful traffic corrections still run
        # the full refinement.
        max_delta = _matrix_max_relative_delta(
            duration_matrices, corrected_duration_matrices
        )
        if max_delta < _REFINEMENT_SKIP_THRESHOLD:
            logger.info(
                "Skipping refinement pass for scenario %s: max traffic delta "
                "%.2f%% < %.2f%% threshold",
                scenario.key, max_delta * 100, _REFINEMENT_SKIP_THRESHOLD * 100,
            )
        else:
            logger.info(
                "Running refinement pass for scenario %s: max traffic delta %.2f%%",
                scenario.key, max_delta * 100,
            )
            # Refinement pass: warm-start from the FIRST pass's solution rather
            # than from OR-Tools' original. The first pass already incorporated
            # (or rejected) the OR-Tools warm-start; passing it again under
            # corrected travel matrices marked it HARD_INFEASIBLE in testing,
            # which collapsed the engine. Use the primary solution as the only
            # warm start for the refinement run.
            solution, vrp_vehicles, multi_trip_vehicles, cluster_vrp_inputs, solver_metrics = await _solve_batch_variant(
                db=db,
                tenant_id=str(scenario.tenant_id),
                orders=problem["orders"],
                couriers=problem["couriers"],
                locations=problem["locations"],
                loc_index=problem["loc_index"],
                distance_matrix=problem["matrix"].distances,
                duration_matrix=problem["adjusted_matrix"].durations,
                distance_matrices=distance_matrices,
                duration_matrices=corrected_duration_matrices,
                global_constraints=problem["global_constraints"],
                order_packages=problem["order_packages"],
                optimization_settings=scenario_settings,
                cache_solution_result=False,
                spread_fleet=spread_fleet,
                skip_multi_trip=problem["skip_multi_trip"],
                solver_time_limit_seconds=refinement_solver_limit,
                solver_kind=scenario.solver_kind,
                warm_start_solution=solution if scenario.solver_kind == "rich_alns" else None,
            )

    return solution, vrp_vehicles, multi_trip_vehicles, cluster_vrp_inputs, solver_metrics


async def _build_price_basis_by_courier(
    db: AsyncSession,
    *,
    couriers: Sequence[Courier],
    as_of: date,
    fallback_prices: dict[FuelType, float],
) -> dict[str, EnergyPriceBasis]:
    basis_by_key: dict[tuple[str | None, str | None], EnergyPriceBasis] = {}
    basis_by_courier: dict[str, EnergyPriceBasis] = {}
    for courier in couriers:
        depot = getattr(courier, "depot", None)
        if depot is not None and (depot.province_code or depot.province_name):
            province_code = depot.province_code
            province_name = depot.province_name
        else:
            resolved = resolve_turkey_province(
                address=getattr(depot, "address", None),
                lat=courier.depot_lat,
                lon=courier.depot_lon,
            )
            province_code = resolved.province_code
            province_name = resolved.province_name
        key = (province_code, province_name)
        if key not in basis_by_key:
            basis_by_key[key] = await get_effective_energy_prices(
                db,
                province_code=province_code,
                province_name=province_name,
                as_of=as_of,
                fallback_prices=fallback_prices,
            )
        basis_by_courier[str(courier.id)] = basis_by_key[key]
    return basis_by_courier


def _merged_energy_prices(price_bases: Sequence[EnergyPriceBasis]) -> dict[FuelType, float]:
    merged: dict[FuelType, float] = {}
    for basis in price_bases:
        for fuel_type, price in basis.prices.items():
            merged.setdefault(fuel_type, price)
    return merged


async def _load_worker_comparison(
    db: AsyncSession,
    comparison_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> RouteScenarioComparison:
    result = await db.execute(
        select(RouteScenarioComparison)
        .where(
            RouteScenarioComparison.id == comparison_id,
            RouteScenarioComparison.tenant_id == tenant_id,
        )
        .options(selectinload(RouteScenarioComparison.scenarios))
    )
    comparison = result.scalar_one_or_none()
    if comparison is None:
        raise ValueError("Scenario comparison not found")
    comparison.scenarios.sort(key=_scenario_sort_key)
    return comparison


async def _mark_comparison_failed(
    comparison_id: uuid.UUID,
    tenant_id: uuid.UUID,
    error_message: str,
    *,
    scenario_status: str,
) -> None:
    async with get_async_session_factory()() as db:
        comparison = await _load_worker_comparison(db, comparison_id, tenant_id)
        comparison.status = "FAILED"
        comparison.error_message = error_message
        comparison.completed_at = utcnow_naive()
        for scenario in comparison.scenarios:
            scenario.status = scenario_status
            scenario.error_message = error_message
            scenario.completed_at = utcnow_naive()
        await db.commit()


def _scenario_sort_key(scenario: RouteScenario) -> int:
    order = {definition["key"]: idx for idx, definition in enumerate(SCENARIO_DEFINITIONS)}
    return order.get(scenario.key, 999)


def _find_scenario(comparison: RouteScenarioComparison, scenario_id: uuid.UUID) -> RouteScenario:
    scenario = next((item for item in comparison.scenarios if item.id == scenario_id), None)
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found")
    return scenario


def _comparison_response(comparison: RouteScenarioComparison) -> RouteScenarioComparisonResponse:
    comparison.scenarios.sort(key=_scenario_sort_key)
    return RouteScenarioComparisonResponse.model_validate(comparison)


async def _load_draft_routes(
    db: AsyncSession,
    run_ids: Sequence[uuid.UUID | None],
    tenant_id: uuid.UUID,
) -> list[Route]:
    resolved_run_ids = [run_id for run_id in run_ids if run_id is not None]
    if not resolved_run_ids:
        return []
    result = await db.execute(
        select(Route)
        .where(
            Route.optimization_run_id.in_(resolved_run_ids),
            Route.status == RouteStatus.DRAFT,
            tenant_filter(Route, tenant_id),
        )
        .options(selectinload(Route.stops))
    )
    return list(result.scalars().unique().all())


def _order_ids_from_routes(routes: Sequence[Route]) -> set[uuid.UUID]:
    return {
        stop.order_id
        for route in routes
        for stop in route.stops
        if stop.order_id is not None
    }


async def _promote_selected_routes(
    db: AsyncSession,
    routes: Sequence[Route],
    orders: dict[uuid.UUID, Order],
) -> int:
    from app.services.lifecycle_history_service import (
        apply_route_status_transition,
    )

    approved = 0
    for route in routes:
        await apply_route_status_transition(
            db,
            route,
            RouteStatus.PLANNED,
            reason="Route scenario approved by dispatcher",
        )
        order_schedule_dates: dict[uuid.UUID, date] = {}
        for stop in sorted(route.stops, key=lambda item: item.sequence_number):
            if stop.order_id is None:
                continue
            if stop.stop_type == StopType.DELIVERY:
                order_schedule_dates[stop.order_id] = stop.planned_arrival.date()
            else:
                order_schedule_dates.setdefault(stop.order_id, stop.planned_arrival.date())
        for order_id, schedule_date in order_schedule_dates.items():
            order = orders[order_id]
            order.assigned_courier_id = route.courier_id
            order.route_id = route.id
            order.scheduled_for_date = schedule_date
            await apply_order_status_transition(
                db,
                order,
                OrderStatus.ASSIGNED,
                reason="Route scenario selected",
            )
        approved += 1
    return approved


async def _delete_draft_routes_for_scenarios(
    db: AsyncSession,
    scenarios: Sequence[RouteScenario],
) -> int:
    run_ids = [scenario.optimization_run_id for scenario in scenarios if scenario.optimization_run_id is not None]
    if not run_ids:
        return 0
    result = await db.execute(
        select(Route.id).where(
            Route.optimization_run_id.in_(run_ids),
            Route.status == RouteStatus.DRAFT,
        )
    )
    route_ids = list(result.scalars().all())
    if not route_ids:
        return 0
    await db.execute(delete(Route).where(Route.id.in_(route_ids)))
    return len(route_ids)


def _seconds_since_midnight(value: datetime) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def _energy_warnings(sources: Sequence[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    statuses = {str(source.get("freshness_status")) for source in sources}
    if "STALE" in statuses:
        warnings.append("One or more energy prices are stale.")
    if "FALLBACK" in statuses:
        warnings.append("One or more energy prices use fallback tariff data.")
    return warnings


def _release_owned_lock(redis_client: Redis, *, key: str, owner: str) -> None:
    release_script = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """
    redis_client.eval(release_script, 1, key, owner)
