"""
Dispatch Service — orchestrates optimization algorithms and persists results.

This is the bridge between the algorithm layer (VRP solver, insertion
heuristic, OSRM client) and the database layer (Route, RouteStop, Order).

Three entry points:
  • dispatch_batch()        — scheduled batch run (all PENDING orders)
  • dispatch_on_demand(id)  — single on-demand order via insertion heuristic
  • handle_courier_offline  — abort route, move orders back to PENDING
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.algorithms import osrm_client
from app.algorithms.constraint_diagnostics import diagnose_unassigned
from app.algorithms.feasibility_diagnostics import (
    diagnose_global_feasibility,
    summarize_cluster_feasibility,
    summarize_global_feasibility,
)
from app.algorithms.geo_clustering import (
    cluster_orders,
    should_cluster,
)
from app.algorithms.human_dispatcher_baseline import solve_human_dispatcher_baseline
from app.algorithms.insertion_heuristic import (
    EligibleCourier,
    ExistingStop,
    InsertionCandidate,
    NewOrder,
    find_best_insertion,
)
from app.algorithms.lexicographic_solver import lexicographic_solve_batch
from app.algorithms.solution_validator import RouteConstraints, ValidationResult
from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.solver_registry import get_solver
from app.algorithms.two_opt import two_opt_improve
from app.algorithms.vrp_solver import (
    OrderData,
    PlannedRoute,
    SolverStatus,
    VehicleData,
    VRPInput,
    VRPSolution,
    build_solution_cost_breakdown,
    build_vrp_input,
)
from app.algorithms.warm_start_cache import (
    build_initial_routes,
)
from app.algorithms.warm_start_cache import (
    cache_solution as cache_warm_start,
)
from app.config import settings
from app.database import get_async_session_factory
from app.models.courier import Courier
from app.models.depot import Depot
from app.models.enums import (
    AvailabilityStatus,
    DispatchDecisionKind,
    OptimizationRunStatus,
    OrderStatus,
    OrderType,
    RouteStatus,
    StopStatus,
    StopType,
)
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.schemas.operations_policy import OptimizationSettings
from app.services import (
    operations_policy_service,
    route_service,
    traffic_profile_service,
)
from app.services.dispatch_vrp_helpers import (
    build_location_index as _build_location_index,
)
from app.services.dispatch_vrp_helpers import (
    build_vrp_data_structures as _build_vrp_data_structures,
)
from app.services.dispatch_vrp_helpers import (
    gather_candidate_chargers_for_couriers as _gather_candidate_chargers,
)
from app.services.dispatch_vrp_helpers import (
    datetime_to_day_seconds as _datetime_to_day_seconds,
)
from app.services.dispatch_vrp_helpers import (
    estimate_route_starting_load_grams as _estimate_route_starting_load_grams,
)
from app.services.dispatch_vrp_helpers import (
    estimate_route_starting_load_packages as _estimate_route_starting_load_packages,
)
from app.services.dispatch_vrp_helpers import (
    estimate_route_starting_load_passengers as _estimate_route_starting_load_passengers,
)
from app.services.dispatch_vrp_helpers import (
    estimate_route_starting_load_volume_cm3 as _estimate_route_starting_load_volume_cm3,
)
from app.services.dispatch_vrp_helpers import (
    expand_virtual_vehicles as _expand_virtual_vehicles,
)
from app.services.dispatch_vrp_helpers import (
    REFINEMENT_SKIP_THRESHOLD,
    matrix_max_relative_delta,
)
from app.services.dispatch_vrp_helpers import (
    load_order_package_summaries as _load_order_package_summaries,
)
from app.services.dispatch_vrp_helpers import (
    merge_cluster_solutions as _merge_cluster_solutions,
)
from app.services.dispatch_vrp_helpers import (
    remap_order as _remap_order,
)
from app.services.dispatch_vrp_helpers import (
    rescue_unassigned_pre_stitch as _rescue_unassigned_pre_stitch,
)
from app.services.dispatch_vrp_helpers import (
    remap_vehicle as _remap_vehicle,
)
from app.services.dispatch_vrp_helpers import (
    resolve_pickup_window_seconds as _resolve_pickup_window_seconds,
)
from app.services.dispatch_vrp_helpers import (
    resolve_vehicle_positions as _resolve_vehicle_positions,
)
from app.services.dispatch_vrp_helpers import (
    should_skip_multi_trip as _should_skip_multi_trip,
)
from app.services.dispatch_vrp_helpers import (
    stitch_multi_trip_routes as _stitch_multi_trip_routes,
)
from app.services.dispatch_vrp_helpers import (
    _unmap_route_indices,
)
from app.services.dispatch_vrp_helpers import (
    time_to_seconds as _time_to_seconds,
)
from app.services.dispatch_vrp_helpers import (
    validate_batch_solution as _validate_batch_solution,
)
from app.services.dispatch_decision_log_service import (
    record_decisions_for_solution,
)
from app.services.lifecycle_history_service import (
    apply_route_status_transition,
    apply_stop_status_transition,
)
from app.services.order_service import (
    apply_order_status_transition,
    apply_order_status_transitions_bulk,
)
from app.services.vehicle_routing_profile import resolve_osrm_profile_from_values
from app.utils.compat import UTC
from app.utils.geo import haversine_meters, point_in_geojson_polygon
from app.utils.perf_metrics import perf_metrics
from app.utils.tenant_context import tenant_filter

logger = logging.getLogger(__name__)

ACTIVE_ROUTE_STATUSES = (RouteStatus.PLANNED, RouteStatus.ACTIVE)
TERMINAL_STOP_STATUSES = (StopStatus.COMPLETED, StopStatus.FAILED)


def _serialize_cost_breakdown(cost_breakdown) -> dict | None:
    if cost_breakdown is None:
        return None
    return {
        "total_generalized_cost": cost_breakdown.total_generalized_cost,
        "distance_cost": cost_breakdown.distance_cost,
        "labor_cost": cost_breakdown.labor_cost,
        "fuel_cost": cost_breakdown.fuel_cost,
        "fixed_route_cost": cost_breakdown.fixed_route_cost,
        "lateness_penalty": cost_breakdown.lateness_penalty,
        "overtime_penalty": cost_breakdown.overtime_penalty,
        "unserved_penalty": cost_breakdown.unserved_penalty,
        "late_minutes": cost_breakdown.late_minutes,
        "overtime_minutes": cost_breakdown.overtime_minutes,
        "active_vehicle_count": cost_breakdown.active_vehicle_count,
    }


def _should_use_decomposition(
    order_count: int,
    optimization_settings: OptimizationSettings | None,
) -> bool:
    if optimization_settings is not None:
        return order_count > optimization_settings.decomposition_order_threshold
    return should_cluster(order_count)


def _resolve_batch_solver_time_limit(
    order_count: int,
    optimization_settings: OptimizationSettings | None,
) -> int | None:
    """Pick a search-time budget that scales with problem size.

    The previous schedule (30/60/120 s) burned a full 30 s on trivial 5–10
    order batches even though `PARALLEL_CHEAPEST_INSERTION` reaches the same
    solution in well under a second.  The revised tiered curve gives small
    batches a snappy ceiling, keeps the typical 50-order workload at the same
    60 s budget, and grants real-world large batches more headroom for
    `GUIDED_LOCAL_SEARCH` to escape local minima.
    """
    if optimization_settings is None:
        return None
    if order_count <= 10:
        return 8
    if order_count <= 30:
        return 20
    if order_count <= 80:
        return 60
    if order_count <= 200:
        return 90
    return 150


def _resolve_batch_solver_pass_time_limits(
    order_count: int,
    optimization_settings: OptimizationSettings | None,
    *,
    refinement_enabled: bool,
) -> tuple[int | None, int | None]:
    """Return primary/refinement pass budgets under one user-visible cap."""
    total_limit = _resolve_batch_solver_time_limit(order_count, optimization_settings)
    if total_limit is None or not refinement_enabled:
        return total_limit, None
    if total_limit <= 1:
        return total_limit, None

    refinement_limit = max(1, total_limit // 3)
    primary_limit = max(1, total_limit - refinement_limit)
    return primary_limit, refinement_limit


def _two_opt_validation_kwargs(vrp_input: VRPInput) -> dict:
    return {
        "route_constraints": {
            vehicle.courier_id: RouteConstraints(
                capacity_grams=vehicle.capacity_grams,
                capacity_volume_cm3=vehicle.capacity_volume_cm3,
                max_packages=vehicle.max_packages,
                start_time_s=vehicle.start_time_s,
                end_time_s=vehicle.hard_end_time_s or vehicle.end_time_s,
                max_stops=vehicle.max_stops,
                max_distance_m=vehicle.max_distance_m,
                # Phase 3 multi-depot — pass the canonical tuple plus
                # the legacy depot_index so single-depot vehicles keep
                # the same depot_indices behavior via the back-compat
                # property.
                allowed_depot_indices=vehicle.allowed_depot_indices,
                end_depot_index=vehicle.end_depot_index,
                depot_index=vehicle.depot_index,
            )
            for vehicle in vrp_input.vehicles
        },
        "order_weights": {order.order_id: order.weight_grams for order in vrp_input.orders},
        "order_volumes": {order.order_id: order.volume_cm3 for order in vrp_input.orders},
        "order_package_counts": {order.order_id: order.package_count for order in vrp_input.orders},
        "order_time_windows": {
            order.order_id: (
                order.earliest_time_s,
                order.hard_latest_time_s or order.latest_time_s,
            )
            for order in vrp_input.orders
        },
        "order_pickup_windows": {
            order.order_id: (
                order.pickup_earliest_time_s or 0,
                order.pickup_latest_time_s
                if order.pickup_latest_time_s is not None
                else (order.hard_latest_time_s or order.latest_time_s),
            )
            for order in vrp_input.orders
        },
        "depot_pickup_order_ids": {
            order.order_id
            for order in vrp_input.orders
            if order.pickup_is_depot
        },
    }


def _allocate_decomposed_solver_time_limits(
    cluster_order_counts: list[int],
    total_time_limit_seconds: int | None,
    optimization_settings: OptimizationSettings | None,
) -> list[int | None]:
    """Split a decomposed solve's total budget across clusters.

    OR-Tools applies `time_limit_seconds` per solve call. In decomposed mode
    each geographic cluster is solved independently, so passing the full
    150-second budget to every cluster turns a 150-second scenario into a
    many-minute job. This allocation keeps the sum of cluster budgets within
    the caller's budget while still weighting larger clusters more heavily.
    """
    if total_time_limit_seconds is None:
        return [None for _ in cluster_order_counts]
    if not cluster_order_counts:
        return []

    budget = max(1, total_time_limit_seconds)
    weights = [max(1, count) for count in cluster_order_counts]
    total_weight = sum(weights)
    caps = [
        _resolve_batch_solver_time_limit(count, optimization_settings) or budget
        for count in cluster_order_counts
    ]

    raw_allocations = [budget * weight / total_weight for weight in weights]
    limits = [
        max(1, min(cap, int(raw)))
        for cap, raw in zip(caps, raw_allocations, strict=True)
    ]

    current_total = sum(limits)
    if current_total > budget:
        for idx in sorted(range(len(limits)), key=lambda i: (weights[i], -limits[i])):
            while current_total > budget and limits[idx] > 1:
                limits[idx] -= 1
                current_total -= 1
            if current_total <= budget:
                break
        return limits

    fractional_order = sorted(
        range(len(limits)),
        key=lambda i: (
            raw_allocations[i] - int(raw_allocations[i]),
            weights[i],
        ),
        reverse=True,
    )
    while current_total < budget:
        changed = False
        for idx in fractional_order:
            if current_total >= budget:
                break
            if limits[idx] >= caps[idx]:
                continue
            limits[idx] += 1
            current_total += 1
            changed = True
        if not changed:
            break
    return limits


def _solution_assigned_order_ids(solution: VRPSolution) -> set[str]:
    return {
        stop.order_id
        for route in solution.routes
        for stop in route.stops
        if stop.order_id and stop.stop_type not in ("DEPOT_RETURN", "DEPOT_RELOAD")
    }


def _solution_reload_summary(solution: VRPSolution) -> dict:
    """Aggregate DEPOT_RELOAD telemetry from the solver's output.

    Multi-depot (Phase 6): `per_depot_location` keys reload counts by the
    location_index of the depot where the reload happened. Dashboards can
    map these to depot names via the tenant's depot list. The legacy
    `total_reload_count` + `per_route` fields are preserved for
    back-compat with any consumer that hasn't picked up the new key.
    """
    per_route: dict[str, int] = {}
    per_depot_location: dict[int, int] = {}
    total = 0
    for route in solution.routes:
        count = 0
        for stop in route.stops:
            if stop.stop_type == "DEPOT_RELOAD":
                count += 1
                # location_index is `int | None` on PlannedStop; only bucket
                # reloads with a known location.
                loc = getattr(stop, "location_index", None)
                if loc is not None:
                    per_depot_location[loc] = per_depot_location.get(loc, 0) + 1
        per_route[route.courier_id] = count
        total += count
    return {
        "total_reload_count": total,
        "per_route": per_route,
        "per_depot_location": per_depot_location,
    }


def _classify_solution_unassigned(solution: VRPSolution, feasibility) -> dict:
    feasible_by_order = {item.order_id: item.globally_feasible for item in feasibility}
    summary = {
        "total_unassigned": len(solution.unassigned_orders),
        "globally_infeasible": 0,
        "route_interaction_or_solver_budget": 0,
    }
    for order_id in solution.unassigned_orders:
        if feasible_by_order.get(order_id, True):
            summary["route_interaction_or_solver_budget"] += 1
        else:
            summary["globally_infeasible"] += 1
    return summary


def _solution_generalized_cost(solution: VRPSolution) -> int:
    if solution.cost_breakdown is not None:
        return solution.cost_breakdown.total_generalized_cost
    return solution.total_distance_m


def _service_day_anchor(
    orders: list[Order],
    *,
    date_from: datetime | None = None,
) -> datetime:
    if date_from is not None:
        return date_from.replace(hour=0, minute=0, second=0, microsecond=0)
    if orders:
        service_date = min(order.earliest_delivery.date() for order in orders)
        return datetime(service_date.year, service_date.month, service_date.day)
    return datetime.now(UTC).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)


def _seconds_to_service_datetime(seconds_from_midnight: int, service_anchor: datetime) -> datetime:
    return service_anchor.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        seconds=seconds_from_midnight
    )


def _solution_metrics_summary(solution: VRPSolution, validation: ValidationResult) -> dict:
    return {
        "solver_status": solution.solver_status.value,
        "assigned_count": len(_solution_assigned_order_ids(solution)),
        "unassigned_count": len(solution.unassigned_orders),
        "route_count": len([route for route in solution.routes if route.stops]),
        "total_distance_m": solution.total_distance_m,
        "total_duration_s": solution.total_duration_s,
        "generalized_cost": _solution_generalized_cost(solution),
        "validation_error_count": validation.error_count,
        "validation_warning_count": validation.warning_count,
    }


def _build_solver_metrics(
    *,
    diagnostic_vrp_input: VRPInput,
    solution: VRPSolution,
    validation: ValidationResult,
    selected_solver: str,
    selected_stage: str,
    solver_time_limit_seconds: int | None,
    spread_fleet: bool,
    primary_summary_key: str = "primary_ortools_summary",
    primary_validation_key: str = "primary_ortools_validation",
    adapter_metrics: dict | None = None,
    adapter_elapsed_s: float | None = None,
) -> dict:
    feasibility = diagnose_global_feasibility(diagnostic_vrp_input)
    feasible_order_ids = {item.order_id for item in feasibility if item.globally_feasible}
    unassigned_feasible_ids = set(solution.unassigned_orders) & feasible_order_ids
    solution_summary = _solution_metrics_summary(solution, validation)
    trace_item = {
        "stage": selected_stage,
        "solver": selected_solver,
        **solution_summary,
    }
    metrics = {
        "selected_solver": selected_solver,
        "selected_stage": selected_stage,
        "solver_time_limit_seconds": solver_time_limit_seconds,
        "spread_fleet": spread_fleet,
        "global_feasibility_summary": summarize_global_feasibility(feasibility),
        "cluster_feasibility_summary": summarize_cluster_feasibility(
            diagnostic_vrp_input,
            feasibility,
        ),
        "seed_solution_summary": None,
        "repair_summary": None,
        "optimization_trace": [trace_item],
        "reload_summary": _solution_reload_summary(solution),
        "unassigned_classification_summary": _classify_solution_unassigned(
            solution,
            feasibility,
        ),
        "assignment_count_status": (
            "all_globally_feasible_assigned"
            if not unassigned_feasible_ids
            else "best_known_within_budget"
        ),
        "lexicographic_fallback_attempted": False,
        "lexicographic_fallback_selected": False,
        primary_summary_key: solution_summary,
        primary_validation_key: validation.summary(),
    }
    if adapter_metrics:
        metrics["alns_telemetry"] = adapter_metrics
    if adapter_elapsed_s is not None:
        metrics["adapter_elapsed_s"] = adapter_elapsed_s
    return metrics


def _lexicographic_fallback_reason(
    solution: VRPSolution,
    validation: ValidationResult,
    diagnostic_vrp_input: VRPInput | None = None,
) -> str | None:
    if validation.error_count > 0:
        return "primary_validation_failed"
    if solution.solver_status == SolverStatus.GREEDY_FALLBACK:
        return "primary_ortools_greedy_fallback"
    if solution.solver_status in (SolverStatus.NOT_FOUND, SolverStatus.ERROR):
        return "primary_ortools_unavailable"
    if diagnostic_vrp_input is not None and not _solution_assigned_order_ids(solution):
        feasibility = diagnose_global_feasibility(diagnostic_vrp_input)
        feasible_order_ids = {item.order_id for item in feasibility if item.globally_feasible}
        if feasible_order_ids:
            unassigned_feasible_ids = set(solution.unassigned_orders) & feasible_order_ids
            if unassigned_feasible_ids == feasible_order_ids:
                return "primary_assignment_collapse"
    return None


async def _maybe_use_lexicographic_fallback(
    *,
    primary_solution: VRPSolution,
    vrp_vehicles: list[VehicleData],
    primary_multi_trip_vehicles: list[VehicleData],
    diagnostic_vrp_input: VRPInput,
    primary_validation: ValidationResult,
    primary_metrics: dict,
    orders: list[Order],
    order_packages: dict,
    optimization_settings: OptimizationSettings | None,
    global_constraints: dict,
    solve_time_limit_seconds: int | None,
    spread_fleet: bool,
) -> tuple[VRPSolution, list[VehicleData], dict]:
    reason = _lexicographic_fallback_reason(
        primary_solution,
        primary_validation,
        diagnostic_vrp_input,
    )
    if reason is None or not settings.FLEXIBLE_RELOAD_SOLVER_ENABLED:
        return primary_solution, primary_multi_trip_vehicles, primary_metrics

    reload_time_s = int(global_constraints.get("depot_reload_time_minutes", 15) or 15) * 60
    fallback_budget = max(1, min(solve_time_limit_seconds or 1, 5))
    lex_result = await asyncio.to_thread(
        lexicographic_solve_batch,
        diagnostic_vrp_input,
        time_limit_seconds=fallback_budget,
        reload_time_s=reload_time_s,
        max_reload_count=None,
        spread_fleet=spread_fleet,
    )
    fallback_validation = _validate_batch_solution(
        lex_result.solution,
        orders,
        vrp_vehicles,
        vrp_vehicles,
        order_packages,
        optimization_settings,
    )

    primary_trace = list(primary_metrics.get("optimization_trace") or [])
    fallback_trace = [
        {
            **item,
            "stage": f"lexicographic_fallback:{item.get('stage', 'unknown')}",
            "solver": "lexicographic_fallback",
        }
        for item in lex_result.diagnostics.get("optimization_trace", [])
    ]
    fallback_summary = _solution_metrics_summary(lex_result.solution, fallback_validation)
    selection_event = {
        "stage": "solver_selection",
        "reason": reason,
        "primary_validation_error_count": primary_validation.error_count,
        "fallback_validation_error_count": fallback_validation.error_count,
        "fallback_assigned_count": fallback_summary["assigned_count"],
        "fallback_unassigned_count": fallback_summary["unassigned_count"],
    }

    shared_fallback_metrics = {
        "lexicographic_fallback_attempted": True,
        "lexicographic_fallback_reason": reason,
        "lexicographic_fallback_budget_seconds": fallback_budget,
        "lexicographic_fallback_summary": fallback_summary,
        "lexicographic_fallback_validation": fallback_validation.summary(),
        "lexicographic_fallback_diagnostics": lex_result.diagnostics,
    }

    if fallback_validation.error_count == 0:
        metrics = {
            **lex_result.diagnostics,
            **shared_fallback_metrics,
            "selected_solver": "lexicographic_fallback",
            "selected_stage": "lexicographic_fallback",
            "solver_time_limit_seconds": solve_time_limit_seconds,
            "spread_fleet": spread_fleet,
            "lexicographic_fallback_selected": True,
            "primary_ortools_summary": primary_metrics.get("primary_ortools_summary"),
            "primary_ortools_validation": primary_validation.summary(),
            "optimization_trace": [
                *primary_trace,
                *fallback_trace,
                {**selection_event, "selected_solver": "lexicographic_fallback"},
            ],
        }
        return lex_result.solution, vrp_vehicles, metrics

    metrics = {
        **primary_metrics,
        **shared_fallback_metrics,
        "lexicographic_fallback_selected": False,
        "optimization_trace": [
            *primary_trace,
            *fallback_trace,
            {**selection_event, "selected_solver": primary_metrics.get("selected_solver")},
        ],
    }
    return primary_solution, primary_multi_trip_vehicles, metrics


def _resolve_courier_osrm_profiles(couriers: list[Courier]) -> set[str]:
    profiles: set[str] = set()
    for courier in couriers:
        spec = getattr(courier, "vehicle_spec", None)
        profiles.add(
            resolve_osrm_profile_from_values(
                courier.vehicle_type,
                spec=spec,
                gross_weight_kg=round(courier.max_weight_kg),
            )
        )
    return profiles or {"driving"}


def _build_courier_profile_map(couriers: list[Courier]) -> dict[str, str]:
    """Return {courier_id_str: osrm_profile} for use in time-corrected matrices."""
    result: dict[str, str] = {}
    for courier in couriers:
        spec = getattr(courier, "vehicle_spec", None)
        result[str(courier.id)] = resolve_osrm_profile_from_values(
            courier.vehicle_type,
            spec=spec,
            gross_weight_kg=round(courier.max_weight_kg),
        )
    return result


def _build_time_corrected_duration_matrices(
    *,
    hourly_by_profile: dict[str, dict[int, list[list[int]]]],
    solution: VRPSolution,
    courier_profile_map: dict[str, str],
    departure_hour: int,
) -> dict[str, list[list[int]]]:
    """Build per-profile duration matrices where each arc uses the traffic
    multiplier from the hour the vehicle is actually expected to traverse it.

    Starting from the departure-hour base matrix, we update individual arc
    costs to reflect the real traversal hour derived from the pass-1 solution's
    departure_time_s values.  This is a single fixed-point iteration that
    captures the bulk of time-of-day variation for routes spanning 2–5 hours.
    """
    corrected: dict[str, list[list[int]]] = {}
    for profile, hourly in hourly_by_profile.items():
        base = hourly.get(departure_hour, next(iter(hourly.values())))
        corrected[profile] = [row[:] for row in base]

    for route in solution.routes:
        profile = courier_profile_map.get(route.courier_id, "driving")
        hourly = hourly_by_profile.get(profile)
        if not hourly or len(route.stops) < 2:
            continue
        mat = corrected[profile]
        for i in range(len(route.stops) - 1):
            from_stop = route.stops[i]
            to_stop = route.stops[i + 1]
            traversal_hour = (from_stop.departure_time_s % 86400) // 3600
            hour_mat = hourly.get(traversal_hour)
            if hour_mat is not None:
                mat[from_stop.location_index][to_stop.location_index] = (
                    hour_mat[from_stop.location_index][to_stop.location_index]
                )

    return corrected


def _rescue_then_merge_rich_alns_clusters(
    cluster_raw: list[tuple[VRPSolution, list[int], list[VehicleData]]],
    *,
    skip_multi_trip: bool,
    locations: list[tuple[float, float]],
    distance_matrix: list[list[int]],
    duration_matrix: list[list[int]],
    distance_matrices: dict[str, list[list[int]]] | None,
    duration_matrices: dict[str, list[list[int]]] | None,
    global_constraints: dict,
    orders: list[OrderData],
    vrp_vehicles: list[VehicleData],
    max_lateness_minutes: int = 0,
) -> tuple[VRPSolution, dict]:
    """Run a global cheapest-insertion rescue across cluster boundaries, then stitch.

    Each cluster's solver only saw its own slice of the fleet — orders that fit
    no vehicle in cluster A may still fit one in cluster B. This pass un-maps
    each cluster's pre-stitch routes back to global indices, runs the rescue
    over the full virtual-vehicle pool, and stitches the result globally.
    """
    global_virtual_vehicles = (
        list(vrp_vehicles)
        if skip_multi_trip
        else _expand_virtual_vehicles(vrp_vehicles, global_constraints)
    )

    all_routes_global: list[PlannedRoute] = []
    all_unassigned: set[str] = set()
    pre_rescue_unassigned_count = 0
    for c_raw, sorted_loc_indices, _c_multi_trip in cluster_raw:
        for route in c_raw.routes:
            all_routes_global.append(_unmap_route_indices(route, sorted_loc_indices))
        all_unassigned.update(c_raw.unassigned_orders)
        pre_rescue_unassigned_count += len(c_raw.unassigned_orders)

    global_vrp_input = build_vrp_input(
        locations=locations,
        distance_matrix=distance_matrix,
        duration_matrix=duration_matrix,
        orders=orders,
        vehicles=global_virtual_vehicles,
        distance_matrices=distance_matrices,
        duration_matrices=duration_matrices,
    )

    rescued_routes, newly_assigned, inner_metrics = _rescue_unassigned_pre_stitch(
        raw_routes=all_routes_global,
        unassigned_order_ids=all_unassigned,
        virtual_vehicles=global_virtual_vehicles,
        vrp_input=global_vrp_input,
        max_lateness_minutes=max_lateness_minutes,
    )

    final_unassigned = sorted(all_unassigned - newly_assigned)
    orders_by_id = {o.order_id: o for o in orders}
    pre_stitch_solution = VRPSolution(
        routes=rescued_routes,
        unassigned_orders=final_unassigned,
        total_distance_m=sum(r.total_distance_m for r in rescued_routes),
        total_duration_s=sum(r.total_duration_s for r in rescued_routes),
        solver_status=SolverStatus.FEASIBLE if rescued_routes else SolverStatus.NOT_FOUND,
        cost_breakdown=build_solution_cost_breakdown(
            routes=rescued_routes,
            unassigned_order_ids=final_unassigned,
            orders_by_id=orders_by_id,
        ),
    )
    final_solution = (
        pre_stitch_solution
        if skip_multi_trip
        else _stitch_multi_trip_routes(
            pre_stitch_solution,
            global_virtual_vehicles,
            locations,
            duration_matrix,
        )
    )

    metrics = {
        "pre_rescue_unassigned": pre_rescue_unassigned_count,
        "rescued_count": len(newly_assigned),
        "final_unassigned": len(final_unassigned),
        "rescued_order_ids": sorted(newly_assigned),
        **inner_metrics,
    }
    logger.info(
        "rich_alns cross-cluster rescue: pre=%d rescued=%d final_unassigned=%d "
        "validator_rejections=%d rebuild_failures=%d timed_out=%s elapsed=%.1fs",
        pre_rescue_unassigned_count, len(newly_assigned), len(final_unassigned),
        inner_metrics.get("validator_rejections", 0),
        inner_metrics.get("rebuild_failures", 0),
        inner_metrics.get("timed_out", False),
        inner_metrics.get("elapsed_s", 0.0),
    )
    return final_solution, metrics


async def _solve_batch_variant(
    *,
    db: AsyncSession,
    tenant_id: str,
    orders: list[Order],
    couriers: list[Courier],
    locations: list[tuple[float, float]],
    loc_index: dict[tuple[float, float], int],
    distance_matrix: list[list[int]],
    duration_matrix: list[list[int]],
    distance_matrices: dict[str, list[list[int]]] | None = None,
    duration_matrices: dict[str, list[list[int]]] | None = None,
    global_constraints: dict,
    order_packages: dict,
    optimization_settings: OptimizationSettings | None,
    cache_solution_result: bool,
    skip_multi_trip: bool = False,
    spread_fleet: bool = True,
    solver_time_limit_seconds: int | None = None,
    solver_kind: str = "rich_alns",
    warm_start_solution: VRPSolution | None = None,
    operations_policy=None,
    candidate_chargers_by_courier=None,
) -> tuple[VRPSolution, list[VehicleData], list[VehicleData], list[VRPInput], dict]:
    effective_optimization_settings = (
        optimization_settings
        if optimization_settings is not None or not settings.FLEXIBLE_RELOAD_SOLVER_ENABLED
        else OptimizationSettings()
    )
    vrp_orders, vrp_vehicles = _build_vrp_data_structures(
        orders,
        couriers,
        loc_index,
        global_constraints,
        order_packages,
        effective_optimization_settings,
        operations_policy=operations_policy,
        candidate_chargers_by_courier=candidate_chargers_by_courier,
    )
    cluster_vrp_inputs: list[VRPInput] = []

    solve_time_limit_seconds = (
        solver_time_limit_seconds
        if solver_time_limit_seconds is not None
        else _resolve_batch_solver_time_limit(
            len(vrp_orders),
            optimization_settings,
        )
    )
    diagnostic_vrp_input = build_vrp_input(
        locations=locations,
        distance_matrix=distance_matrix,
        duration_matrix=duration_matrix,
        orders=vrp_orders,
        vehicles=vrp_vehicles,
        distance_matrices=distance_matrices,
        duration_matrices=duration_matrices,
    )

    if _should_use_decomposition(len(vrp_orders), optimization_settings):
        order_delivery_locs = [locations[o.delivery_index] for o in vrp_orders]
        vehicle_depot_locs = await _resolve_vehicle_positions(
            couriers, locations, vrp_vehicles, db
        )
        clusters = cluster_orders(order_delivery_locs, vehicle_depot_locs)

        all_multi_trip_vehicles: list[VehicleData] = []
        cluster_solutions: list[VRPSolution] = []
        # For rich_alns: collect raw cluster results (pre-stitch, cluster-local
        # indices) so we can run a global cheapest-insertion rescue pass before
        # stitching. Each cluster's solver only saw its own fleet partition; an
        # order that fit no vehicle in cluster A may still fit one in B.
        cluster_raw_for_rescue: list[tuple[VRPSolution, list[int], list[VehicleData]]] = []

        cluster_time_limits = _allocate_decomposed_solver_time_limits(
            [cluster.order_count for cluster in clusters],
            solve_time_limit_seconds,
            optimization_settings,
        )
        if solve_time_limit_seconds is not None:
            logger.info(
                "Decomposed VRP solver budget: %d orders, %d clusters, "
                "%ss total -> cluster limits %s",
                len(vrp_orders),
                len(clusters),
                solve_time_limit_seconds,
                cluster_time_limits,
            )

        # Fleet sharing strategy depends on the solver:
        #   rich_alns → each cluster sees the full physical fleet. The
        #     in-cluster ALNS picks freely from all vehicles, and the
        #     cross-cluster rescue (cb03828) picks up whatever the cluster
        #     solves leave behind. This is what lifts rich_alns from ~502 to
        #     ~600 assigned on the 625-order test.
        #   ortools_cvrptw → keep the demand-share partition. Branch-and-bound
        #     does not scale gracefully when each cluster has 150 orders
        #     against the full 75-virtual-vehicle fleet within the per-cluster
        #     time slice — that combination dropped ortools from 530 → 327.
        #     The narrower vehicle slice is what lets the B&B converge.
        async def _solve_one_cluster(cluster, cluster_time_limit):
            """Self-contained per-cluster build + solve. Returns a tuple of all
            artefacts the outer code needs to merge — runs concurrently with
            sibling clusters via `asyncio.gather` below.
            """
            c_orders = [vrp_orders[i] for i in cluster.order_indices]
            if solver_kind == "rich_alns":
                c_vehicles = list(vrp_vehicles)
            else:
                c_vehicles = [vrp_vehicles[i] for i in cluster.vehicle_indices]
            c_multi_trip = c_vehicles if skip_multi_trip else _expand_virtual_vehicles(c_vehicles, global_constraints)

            used_loc_indices: set[int] = set()
            for order in c_orders:
                used_loc_indices.add(order.pickup_index)
                used_loc_indices.add(order.delivery_index)
            for vehicle in c_multi_trip:
                used_loc_indices.add(vehicle.depot_index)
            sorted_loc_indices = sorted(used_loc_indices)
            old_to_new = {old: new for new, old in enumerate(sorted_loc_indices)}

            c_locations = [locations[i] for i in sorted_loc_indices]
            c_dist = [
                [distance_matrix[r][c] for c in sorted_loc_indices]
                for r in sorted_loc_indices
            ]
            c_dur = [
                [duration_matrix[r][c] for c in sorted_loc_indices]
                for r in sorted_loc_indices
            ]
            c_dist_matrices = {
                profile: [
                    [profile_matrix[r][c] for c in sorted_loc_indices]
                    for r in sorted_loc_indices
                ]
                for profile, profile_matrix in (distance_matrices or {}).items()
            } or None
            c_dur_matrices = {
                profile: [
                    [profile_matrix[r][c] for c in sorted_loc_indices]
                    for r in sorted_loc_indices
                ]
                for profile, profile_matrix in (duration_matrices or {}).items()
            } or None

            c_remapped_orders = [_remap_order(order, old_to_new) for order in c_orders]
            c_remapped_vehicles = [_remap_vehicle(vehicle, old_to_new) for vehicle in c_multi_trip]

            c_input = build_vrp_input(
                locations=c_locations,
                distance_matrix=c_dist,
                duration_matrix=c_dur,
                orders=c_remapped_orders,
                vehicles=c_remapped_vehicles,
                distance_matrices=c_dist_matrices,
                duration_matrices=c_dur_matrices,
            )
            c_initial = build_initial_routes(c_input, tenant_id=tenant_id)
            # Don't forward the global warm-start into per-cluster solves — its
            # location_indices and order_ids reference the global problem, not
            # the cluster's remapped subproblem, and `import_solution` then
            # IndexErrors against the cluster's smaller distance matrix.
            # Warm-start only makes sense for the non-decomposed (single-shot)
            # path below, where the matrix space matches.
            c_raw = (await asyncio.to_thread(
                get_solver(solver_kind).solve,
                c_input,
                SolveOptions(
                    time_budget_s=float(cluster_time_limit or 60),
                    spread_fleet=spread_fleet,
                    initial_routes=c_initial,
                ),
            )).solution
            cluster_skip_two_opt = solver_kind == "rich_alns"
            if not cluster_skip_two_opt and c_raw.solver_status not in (SolverStatus.NOT_FOUND, SolverStatus.ERROR, SolverStatus.GREEDY_FALLBACK):
                two_opt_budget = min(1.0, (cluster_time_limit or 5) * 0.15)
                c_raw = await asyncio.to_thread(
                    two_opt_improve, c_raw, c_dur, c_dist,
                    time_budget_s=two_opt_budget,
                    **_two_opt_validation_kwargs(c_input),
                )
            return {
                "c_input": c_input,
                "c_raw": c_raw,
                "c_remapped_vehicles": c_remapped_vehicles,
                "sorted_loc_indices": sorted_loc_indices,
                "c_multi_trip": c_multi_trip,
                "c_locations": c_locations,
                "c_dur": c_dur,
            }

        # Run cluster solves concurrently. The Cython hot path in rich_alns
        # releases the GIL during the solve loop and OR-Tools' B&B runs in
        # C++, so this is real CPU parallelism rather than I/O multiplexing.
        # Each cluster's submatrices and route data are independent; results
        # are collected in deterministic input order so the warm-start cache
        # write below picks the same cluster every time.
        cluster_results = await asyncio.gather(
            *[
                _solve_one_cluster(cluster, cluster_time_limit)
                for cluster, cluster_time_limit in zip(clusters, cluster_time_limits, strict=True)
            ],
        )
        for result in cluster_results:
            cluster_vrp_inputs.append(result["c_input"])
            all_multi_trip_vehicles.extend(result["c_remapped_vehicles"])
            if solver_kind == "rich_alns":
                cluster_raw_for_rescue.append(
                    (result["c_raw"], result["sorted_loc_indices"], result["c_multi_trip"])
                )
            else:
                c_solution = (
                    result["c_raw"]
                    if skip_multi_trip
                    else _stitch_multi_trip_routes(
                        result["c_raw"],
                        result["c_remapped_vehicles"],
                        result["c_locations"],
                        result["c_dur"],
                    )
                )
                cluster_solutions.append(c_solution)
        # Warm-start cache write: do it once, deterministically, on the first
        # cluster's result (matches the post-loop end state of the prior
        # serial code where each iteration overwrote the previous). Compare
        # Routes runs with `cache_solution_result=False` so this is a no-op
        # there; only batch dispatch flows hit this branch.
        if cache_solution_result and cluster_results:
            first_raw = cluster_results[0]["c_raw"]
            if first_raw.solver_status != SolverStatus.GREEDY_FALLBACK:
                cache_warm_start(first_raw, tenant_id=tenant_id)

        rescue_metrics: dict | None = None
        if solver_kind == "rich_alns" and cluster_raw_for_rescue:
            solution, rescue_metrics = _rescue_then_merge_rich_alns_clusters(
                cluster_raw_for_rescue,
                skip_multi_trip=skip_multi_trip,
                locations=locations,
                distance_matrix=distance_matrix,
                duration_matrix=duration_matrix,
                distance_matrices=distance_matrices,
                duration_matrices=duration_matrices,
                global_constraints=global_constraints,
                orders=vrp_orders,
                vrp_vehicles=vrp_vehicles,
                max_lateness_minutes=(
                    effective_optimization_settings.max_lateness_minutes
                    if effective_optimization_settings is not None
                    else 0
                ),
            )
        else:
            solution = _merge_cluster_solutions(cluster_solutions)
        validation = _validate_batch_solution(
            solution,
            orders,
            vrp_vehicles,
            all_multi_trip_vehicles,
            order_packages,
            effective_optimization_settings,
        )
        solver_metrics = _build_solver_metrics(
            diagnostic_vrp_input=diagnostic_vrp_input,
            solution=solution,
            validation=validation,
            selected_solver=solver_kind,
            selected_stage=f"{solver_kind}_decomposed",
            solver_time_limit_seconds=solve_time_limit_seconds,
            spread_fleet=spread_fleet,
        )
        if rescue_metrics is not None:
            solver_metrics["cross_cluster_rescue"] = rescue_metrics
        # Skip the lexicographic fallback when our cross-cluster rescue
        # produced the solution. The fallback is designed for OR-tools-style
        # collapse (greedy fallback / NOT_FOUND / mass-unassigned), where
        # running another full solve is worth the cost. After a rescue that
        # already moved hundreds of orders into routes, a single residual
        # validator warning isn't worth a 5-second secondary solve per
        # scenario — and three concurrent ALNS scenarios all triggering it
        # was the source of the multi-minute hang.
        if rescue_metrics is not None:
            selected_multi_trip_vehicles = all_multi_trip_vehicles
            solver_metrics["lexicographic_fallback_skipped_after_rescue"] = True
        else:
            solution, selected_multi_trip_vehicles, solver_metrics = await _maybe_use_lexicographic_fallback(
                primary_solution=solution,
                vrp_vehicles=vrp_vehicles,
                primary_multi_trip_vehicles=all_multi_trip_vehicles,
                diagnostic_vrp_input=diagnostic_vrp_input,
                primary_validation=validation,
                primary_metrics=solver_metrics,
                orders=orders,
                order_packages=order_packages,
                optimization_settings=effective_optimization_settings,
                global_constraints=global_constraints,
                solve_time_limit_seconds=solve_time_limit_seconds,
                spread_fleet=spread_fleet,
            )
        return (
            solution,
            vrp_vehicles,
            selected_multi_trip_vehicles,
            cluster_vrp_inputs,
            solver_metrics,
        )

    multi_trip_vehicles = vrp_vehicles if skip_multi_trip else _expand_virtual_vehicles(vrp_vehicles, global_constraints)
    vrp_input = build_vrp_input(
        locations=locations,
        distance_matrix=distance_matrix,
        duration_matrix=duration_matrix,
        orders=vrp_orders,
        vehicles=multi_trip_vehicles,
        distance_matrices=distance_matrices,
        duration_matrices=duration_matrices,
    )
    cluster_vrp_inputs.append(vrp_input)
    initial_routes = build_initial_routes(vrp_input, tenant_id=tenant_id)
    _solve_result = await asyncio.to_thread(
        get_solver(solver_kind).solve,
        vrp_input,
        SolveOptions(
            time_budget_s=float(solve_time_limit_seconds or 60),
            spread_fleet=spread_fleet,
            initial_routes=initial_routes,
            warm_start_solution=warm_start_solution,
        ),
    )
    raw_solution = _solve_result.solution
    _adapter_metrics = _solve_result.metrics
    _adapter_elapsed_s = _solve_result.elapsed_s
    skip_two_opt = solver_kind == "rich_alns"
    if not skip_two_opt and raw_solution.solver_status not in (SolverStatus.NOT_FOUND, SolverStatus.ERROR, SolverStatus.GREEDY_FALLBACK):
        two_opt_budget = min(1.0, (solve_time_limit_seconds or 5) * 0.15)
        raw_solution = await asyncio.to_thread(
            two_opt_improve, raw_solution, duration_matrix, distance_matrix,
            time_budget_s=two_opt_budget,
            **_two_opt_validation_kwargs(vrp_input),
        )
    solution = raw_solution if skip_multi_trip else _stitch_multi_trip_routes(raw_solution, multi_trip_vehicles, locations, duration_matrix)
    validation = _validate_batch_solution(
        solution,
        orders,
        vrp_vehicles,
        multi_trip_vehicles,
        order_packages,
        effective_optimization_settings,
    )
    solver_metrics = _build_solver_metrics(
        diagnostic_vrp_input=diagnostic_vrp_input,
        solution=solution,
        validation=validation,
        selected_solver=solver_kind,
        selected_stage=f"{solver_kind}_primary",
        solver_time_limit_seconds=solve_time_limit_seconds,
        spread_fleet=spread_fleet,
        adapter_metrics=_adapter_metrics,
        adapter_elapsed_s=_adapter_elapsed_s,
    )
    solution, selected_multi_trip_vehicles, solver_metrics = await _maybe_use_lexicographic_fallback(
        primary_solution=solution,
        vrp_vehicles=vrp_vehicles,
        primary_multi_trip_vehicles=multi_trip_vehicles,
        diagnostic_vrp_input=diagnostic_vrp_input,
        primary_validation=validation,
        primary_metrics=solver_metrics,
        orders=orders,
        order_packages=order_packages,
        optimization_settings=effective_optimization_settings,
        global_constraints=global_constraints,
        solve_time_limit_seconds=solve_time_limit_seconds,
        spread_fleet=spread_fleet,
    )
    if (
        cache_solution_result
        and solver_metrics.get("selected_solver") != "lexicographic_fallback"
        and validation.error_count == 0
        and raw_solution.solver_status != SolverStatus.GREEDY_FALLBACK
    ):
        cache_warm_start(raw_solution, tenant_id=tenant_id)
    return solution, vrp_vehicles, selected_multi_trip_vehicles, cluster_vrp_inputs, solver_metrics


async def _solve_human_dispatcher_baseline_variant(
    *,
    db: AsyncSession,
    tenant_id: str,
    orders: list[Order],
    couriers: list[Courier],
    locations: list[tuple[float, float]],
    loc_index: dict[tuple[float, float], int],
    distance_matrix: list[list[int]],
    duration_matrix: list[list[int]],
    distance_matrices: dict[str, list[list[int]]] | None = None,
    duration_matrices: dict[str, list[list[int]]] | None = None,
    global_constraints: dict,
    order_packages: dict,
    optimization_settings: OptimizationSettings | None,
    skip_multi_trip: bool = False,
    operations_policy=None,
) -> tuple[VRPSolution, list[VehicleData], list[VehicleData], list[VRPInput], dict]:
    del db, tenant_id
    vrp_orders, vrp_vehicles = _build_vrp_data_structures(
        orders,
        couriers,
        loc_index,
        global_constraints,
        order_packages,
        optimization_settings,
        operations_policy=operations_policy,
    )
    baseline_vehicles = (
        vrp_vehicles
        if skip_multi_trip
        else _expand_virtual_vehicles(vrp_vehicles, global_constraints)
    )
    vrp_input = build_vrp_input(
        locations=locations,
        distance_matrix=distance_matrix,
        duration_matrix=duration_matrix,
        orders=vrp_orders,
        vehicles=baseline_vehicles,
        distance_matrices=distance_matrices,
        duration_matrices=duration_matrices,
    )
    raw_solution = await asyncio.to_thread(solve_human_dispatcher_baseline, vrp_input)
    solution = (
        raw_solution
        if skip_multi_trip
        else _stitch_multi_trip_routes(
            raw_solution,
            baseline_vehicles,
            locations,
            duration_matrix,
        )
    )
    diagnostic_vrp_input = build_vrp_input(
        locations=locations,
        distance_matrix=distance_matrix,
        duration_matrix=duration_matrix,
        orders=vrp_orders,
        vehicles=vrp_vehicles,
        distance_matrices=distance_matrices,
        duration_matrices=duration_matrices,
    )
    baseline_validation = _validate_batch_solution(
        solution,
        orders,
        vrp_vehicles,
        baseline_vehicles,
        order_packages,
        optimization_settings,
    )
    baseline_metrics: dict = {
        **_build_solver_metrics(
            diagnostic_vrp_input=diagnostic_vrp_input,
            solution=solution,
            validation=baseline_validation,
            selected_solver="human_dispatcher_baseline",
            selected_stage="human_greedy",
            solver_time_limit_seconds=None,
            spread_fleet=False,
            primary_summary_key="baseline_summary",
            primary_validation_key="baseline_validation",
        ),
        "baseline_raw_assigned_count": len(_solution_assigned_order_ids(solution)),
        "baseline_flexible_completion_applied": False,
    }
    return solution, vrp_vehicles, baseline_vehicles, [vrp_input], baseline_metrics


# ---------------------------------------------------------------------------
# 1. BATCH OPTIMIZATION
# ---------------------------------------------------------------------------

async def dispatch_batch(
    *,
    optimization_run_id: uuid.UUID | None = None,
    draft: bool = False,
    departure_time: datetime | None = None,
    tenant_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    """Run batch VRP optimization for PENDING orders.

    When date_from/date_to are provided (date-scoped mode):
      - All order types are eligible (ON_DEMAND, SCHEDULED, BULK, REVERSE)
      - Only orders with earliest_delivery in [date_from, date_to) are included
    Without date bounds, defaults to SCHEDULED/BULK within the planning horizon.

    Flow:
      0. Pre-flight: verify OSRM is reachable
      1. Query PENDING orders
      2. Query all ONLINE couriers
      3. Build unique location list + distance matrix via OSRM
      4. Build VRPInput and run solver
      5. Persist solution → Route + RouteStop records, update Order status
      6. Return summary dict
    """
    started = time.monotonic()

    def _batch_result(payload: dict) -> dict:
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        perf_metrics.observe_ms("dispatch_batch_latency_ms", latency_ms)
        payload.setdefault("dispatch_batch_latency_ms", latency_ms)
        return payload

    if not await osrm_client.check_osrm_health():
        logger.error("Batch optimization aborted: OSRM is unreachable")
        return _batch_result({
            "routes_created": 0,
            "unassigned_count": 0,
            "unassigned_order_ids": [],
            "solver_status": "OSRM_UNAVAILABLE",
        })

    if tenant_id is None:
        now_naive = datetime.now(UTC).replace(tzinfo=None)
        async with get_async_session_factory()() as db:
            discovery_filters = [
                Order.status == OrderStatus.PENDING,
                Order.latest_delivery > now_naive,
            ]
            if date_from is not None and date_to is not None:
                discovery_filters += [
                    Order.earliest_delivery < date_to,
                    Order.latest_delivery >= date_from,
                ]
            else:
                horizon_cutoff = now_naive + timedelta(hours=settings.DISPATCH_PLANNING_HORIZON_HOURS)
                discovery_filters += [
                    Order.type.in_([
                        OrderType.SCHEDULED,
                        OrderType.BULK,
                        OrderType.PICKUP_AND_DELIVERY,
                        OrderType.RETURN,
                    ]),
                    Order.earliest_delivery <= horizon_cutoff,
                ]
            tenant_result = await db.execute(
                select(Order.tenant_id).where(*discovery_filters).distinct()
            )
            tenant_ids = list(tenant_result.scalars().all())

        if not tenant_ids:
            return _batch_result({
                "routes_created": 0,
                "unassigned_count": 0,
                "unassigned_order_ids": [],
                "solver_status": "NO_ORDERS",
            })

        aggregated = {
            "routes_created": 0,
            "unassigned_count": 0,
            "unassigned_order_ids": [],
            "solver_status": "MULTI_TENANT",
        }
        for scoped_tenant_id in tenant_ids:
            tenant_result = await dispatch_batch(
                optimization_run_id=None,
                draft=draft,
                departure_time=departure_time,
                tenant_id=scoped_tenant_id,
                date_from=date_from,
                date_to=date_to,
            )
            aggregated["routes_created"] += tenant_result.get("routes_created", 0)
            aggregated["unassigned_count"] += tenant_result.get("unassigned_count", 0)
            aggregated["unassigned_order_ids"].extend(tenant_result.get("unassigned_order_ids", []))
        return _batch_result(aggregated)

    async with get_async_session_factory()() as db:
        # 1. Fetch pending orders.
        #    Date-scoped mode: all types within [date_from, date_to).
        #    Default mode: SCHEDULED/BULK within the planning horizon.
        now_naive = datetime.now(UTC).replace(tzinfo=None)
        order_filters = [
            Order.status == OrderStatus.PENDING,
            Order.latest_delivery > now_naive,
            tenant_filter(Order, tenant_id),
        ]
        if date_from is not None and date_to is not None:
            order_filters += [
                Order.earliest_delivery < date_to,
                Order.latest_delivery >= date_from,
            ]
        else:
            horizon_cutoff = now_naive + timedelta(hours=settings.DISPATCH_PLANNING_HORIZON_HOURS)
            order_filters += [
                Order.type.in_([
                    OrderType.SCHEDULED,
                    OrderType.BULK,
                    OrderType.PICKUP_AND_DELIVERY,
                    OrderType.RETURN,
                ]),
                Order.earliest_delivery <= horizon_cutoff,
            ]
        result = await db.execute(
            select(Order).where(*order_filters).limit(1000)  # OOM Prevention
        )
        orders = list(result.scalars().all())

        if not orders:
            logger.info("Batch optimization: no pending orders")
            return _batch_result({
                "routes_created": 0,
                "unassigned_count": 0,
                "unassigned_order_ids": [],
                "solver_status": "NO_ORDERS",
            })

        # 2. Fetch all active couriers (ONLINE + ON_BREAK).
        # Batch planning assigns routes for future delivery windows, so a courier
        # currently on break will be available well before their deliveries are due.
        result = await db.execute(
            select(Courier).where(
                Courier.availability_status.in_([
                    AvailabilityStatus.ONLINE,
                    AvailabilityStatus.ON_BREAK,
                ]),
                tenant_filter(Courier, tenant_id),
            ).options(selectinload(Courier.vehicle_spec))
        )
        couriers = list(result.scalars().all())

        if not couriers:
            logger.warning("Batch optimization: no available couriers")
            return _batch_result({
                "routes_created": 0,
                "unassigned_count": len(orders),
                "unassigned_order_ids": [str(order.id) for order in orders],
                "solver_status": "NO_COURIERS",
            })

        # 3. Gather candidate charging stations (Phase 2.3) — must happen
        # BEFORE the location index + matrix build so the chargers' coords
        # are part of the OSRM matrix. Needs operations_policy, so load it
        # here (it's also re-used by the rest of the function below — the
        # later load is removed).
        operations_policy = await operations_policy_service.get_operations_policy(db, tenant_id)
        candidate_chargers_by_courier = await _gather_candidate_chargers(
            db, couriers, operations_policy
        )
        charger_coords: list[tuple[float, float]] = []
        for stations in candidate_chargers_by_courier.values():
            for s in stations:
                charger_coords.append((s.lat, s.lon))

        # 3b. Build unique location list (with charger coords pre-included
        # so the matrix covers them). Multi-depot couriers also contribute
        # their additional reload depots' coordinates when the tenant flag
        # is on.
        locations, loc_index = _build_location_index(
            orders,
            couriers,
            extra_coords=charger_coords,
            multi_depot_enabled=bool(
                operations_policy.feature_flags.multi_depot_vehicles_enabled
            ),
        )

        # 4. Get distance matrices from OSRM, one per vehicle routing profile.
        # Fan out OSRM calls per profile — they're independent HTTP round-trips
        # with no shared state, so the per-profile latency overlaps instead of
        # serializing. (Same pattern as `_build_daily_problem` in
        # route_scenario_service.py.)
        osrm_profiles = sorted(_resolve_courier_osrm_profiles(couriers))
        matrix_results = await asyncio.gather(
            *[osrm_client.get_distance_matrix(locations, profile=profile) for profile in osrm_profiles]
        )
        matrix_by_profile = dict(zip(osrm_profiles, matrix_results, strict=True))
        matrix = matrix_by_profile.get("driving") or next(iter(matrix_by_profile.values()))
        target_time = departure_time or datetime.now(UTC).replace(tzinfo=None)
        # Traffic adjustment is per-profile and uses the DB session. SQLAlchemy
        # AsyncSession isn't safe for concurrent use, so we keep these serial
        # on a single session — parallelizing would require spinning up
        # separate sessions per profile, which is more complex than the saving
        # is worth (each call is fast and there are usually 1-3 profiles).
        adjusted_by_profile = {}
        for profile, profile_matrix in matrix_by_profile.items():
            adjusted_by_profile[profile] = await traffic_profile_service.adjust_duration_matrix(
                db,
                coords=locations,
                durations=profile_matrix.durations,
                at_time=target_time,
            )
        adjusted_matrix = adjusted_by_profile.get("driving") or next(iter(adjusted_by_profile.values()))

        # 4b. Time-dependent travel: pre-build 24-hour matrices per profile so we
        #     can do a second fixed-point solve after the first solution reveals
        #     which hours each arc is actually traversed. Same single-session
        #     constraint — keep serial.
        hourly_by_profile: dict[str, dict[int, list[list[int]]]] = {}
        if settings.TIME_DEPENDENT_TRAVEL_ENABLED:
            for profile, profile_matrix in matrix_by_profile.items():
                hourly_by_profile[profile] = (
                    await traffic_profile_service.build_hourly_duration_matrices(
                        db,
                        coords=locations,
                        durations=profile_matrix.durations,
                        at_time=target_time,
                    )
                )

        # 5. Load constraints and build VRP input
        from app.services.constraint_service import (
            get_global_constraints,
        )
        global_constraints = await get_global_constraints(db, tenant_id)
        # operations_policy already loaded above (at the top of the
        # dispatch_batch flow, before the charger gather).
        optimization_settings = operations_policy.optimization

        order_packages = await _load_order_package_summaries(db, [o.id for o in orders], tenant_id=tenant_id)
        economic_optimizer_enabled = optimization_settings.economic_batch_optimizer_enabled
        shadow_mode_enabled = (
            optimization_settings.shadow_mode_enabled
            and not economic_optimizer_enabled
            and not draft
        )
        primary_optimization_settings = (
            optimization_settings if economic_optimizer_enabled else None
        )
        spread_fleet = optimization_settings.fleet_utilization == "FULL_FLEET"

        skip_multi_trip = _should_skip_multi_trip(
            orders, global_constraints, operations_policy=operations_policy,
        )

        refinement_enabled = bool(settings.TIME_DEPENDENT_TRAVEL_ENABLED and hourly_by_profile)
        primary_solver_limit, refinement_solver_limit = _resolve_batch_solver_pass_time_limits(
            len(orders),
            primary_optimization_settings,
            refinement_enabled=refinement_enabled,
        )

        solution, vrp_vehicles, multi_trip_vehicles, cluster_vrp_inputs, solver_metrics = await _solve_batch_variant(
            db=db,
            tenant_id=str(tenant_id),
            orders=orders,
            couriers=couriers,
            locations=locations,
            loc_index=loc_index,
            distance_matrix=matrix.distances,
            duration_matrix=adjusted_matrix.durations,
            distance_matrices={
                profile: profile_matrix.distances
                for profile, profile_matrix in matrix_by_profile.items()
            },
            duration_matrices={
                profile: adjusted.durations
                for profile, adjusted in adjusted_by_profile.items()
            },
            global_constraints=global_constraints,
            order_packages=order_packages,
            optimization_settings=optimization_settings,
            cache_solution_result=True,
            spread_fleet=spread_fleet,
            skip_multi_trip=skip_multi_trip,
            solver_time_limit_seconds=primary_solver_limit,
            solver_kind="rich_alns",
            operations_policy=operations_policy,
            candidate_chargers_by_courier=candidate_chargers_by_courier,
        )

        # 6b. Time-dependent fixed-point refinement: re-solve with arc costs
        #     corrected for the actual traversal hour from pass-1 routes.
        # Skip the refinement entirely when the corrected matrix barely
        # moves vs the baseline (max relative cell delta below 5%). At that
        # threshold the primary solution is already at the corrected
        # optimum and re-solving is duplicated work — same logic as
        # `_solve_scenario_vrp` (91c737a). The full pass still runs when
        # traffic actually shifts.
        if settings.TIME_DEPENDENT_TRAVEL_ENABLED and hourly_by_profile and solution.routes:
            courier_profile_map = _build_courier_profile_map(couriers)
            corrected_duration_matrices = _build_time_corrected_duration_matrices(
                hourly_by_profile=hourly_by_profile,
                solution=solution,
                courier_profile_map=courier_profile_map,
                departure_hour=target_time.hour,
            )
            baseline_duration_matrices = {
                profile: adjusted.durations
                for profile, adjusted in adjusted_by_profile.items()
            }
            max_delta = matrix_max_relative_delta(
                baseline_duration_matrices, corrected_duration_matrices
            )
            if max_delta < REFINEMENT_SKIP_THRESHOLD:
                logger.info(
                    "dispatch_batch: skipping refinement pass (max traffic delta "
                    "%.2f%% < %.2f%% threshold)",
                    max_delta * 100, REFINEMENT_SKIP_THRESHOLD * 100,
                )
            else:
                logger.info(
                    "dispatch_batch: running refinement pass (max traffic delta %.2f%%)",
                    max_delta * 100,
                )
                solution, vrp_vehicles, multi_trip_vehicles, cluster_vrp_inputs, solver_metrics = (
                    await _solve_batch_variant(
                        db=db,
                        tenant_id=str(tenant_id),
                        orders=orders,
                        couriers=couriers,
                        locations=locations,
                        loc_index=loc_index,
                        distance_matrix=matrix.distances,
                        duration_matrix=adjusted_matrix.durations,
                        distance_matrices={
                            profile: profile_matrix.distances
                            for profile, profile_matrix in matrix_by_profile.items()
                        },
                        duration_matrices=corrected_duration_matrices,
                        global_constraints=global_constraints,
                        order_packages=order_packages,
                        optimization_settings=optimization_settings,
                        cache_solution_result=True,
                        spread_fleet=spread_fleet,
                        skip_multi_trip=skip_multi_trip,
                        solver_time_limit_seconds=refinement_solver_limit,
                        solver_kind="rich_alns",
                        operations_policy=operations_policy,
                        candidate_chargers_by_courier=candidate_chargers_by_courier,
                    )
                )

        shadow_solution = None
        if shadow_mode_enabled:
            try:
                shadow_solution, _, _, _, _ = await _solve_batch_variant(
                    db=db,
                    tenant_id=str(tenant_id),
                    orders=orders,
                    couriers=couriers,
                    locations=locations,
                    loc_index=loc_index,
                    distance_matrix=matrix.distances,
                    duration_matrix=adjusted_matrix.durations,
                    distance_matrices={
                        profile: profile_matrix.distances
                        for profile, profile_matrix in matrix_by_profile.items()
                    },
                    duration_matrices={
                        profile: adjusted.durations
                        for profile, adjusted in adjusted_by_profile.items()
                    },
                    global_constraints=global_constraints,
                    order_packages=order_packages,
                    optimization_settings=optimization_settings,
                    cache_solution_result=False,
                    skip_multi_trip=skip_multi_trip,
                    solver_kind="ortools_cvrptw",
                    operations_policy=operations_policy,
                    candidate_chargers_by_courier=candidate_chargers_by_courier,
                )
            except Exception:
                logger.warning(
                    "Shadow solution failed — skipping shadow comparison. "
                    "Primary solution is unaffected.",
                    exc_info=True,
                )

        # 9. Validate solution
        validation = _validate_batch_solution(
            solution,
            orders,
            vrp_vehicles,
            multi_trip_vehicles,
            order_packages,
            optimization_settings,
            operations_policy=operations_policy,
        )
        if validation.error_count > 0 and not draft:
            return _batch_result({
                "routes_created": 0,
                "unassigned_count": len(solution.unassigned_orders),
                "unassigned_order_ids": solution.unassigned_orders,
                "solver_status": "VALIDATION_FAILED",
                "validation": validation.summary(),
                "solver_metrics": solver_metrics,
            })

        # 9b. Inject mid-route charging stops for EV couriers whose
        # planned route exceeds remaining battery range. The solver's
        # ``max_distance_m`` cap was inflated upstream to allow longer
        # routes under ``ev_auto_charging_enabled``; this step lays down
        # the actual ``__CHARGER_*`` synthetic stops the persistence
        # layer turns into ``StopType.CHARGING`` RouteStops. Uses the
        # real OSRM distance matrix and per-vehicle battery_kwh so charge
        # duration tracks the actual energy delta and station power.
        if operations_policy.feature_flags.ev_auto_charging_enabled:
            from app.services.charging_post_processor import (
                inject_charging_stops_into_solution,
            )
            orders_by_id_for_charging = {str(o.id): o for o in orders}
            full_range_m_by_courier: dict[str, int] = {}
            initial_charge_m_by_courier: dict[str, int] = {}
            vehicle_spec_by_courier: dict = {}
            for courier in couriers:
                if not getattr(courier, "max_battery_range_km", None):
                    continue
                full_range_m = int(courier.max_battery_range_km * 1000)
                pct = int(getattr(courier, "current_battery_pct", None) or 100)
                full_range_m_by_courier[str(courier.id)] = full_range_m
                initial_charge_m_by_courier[str(courier.id)] = max(
                    0, int(full_range_m * pct / 100)
                )
                if courier.vehicle_spec is not None:
                    vehicle_spec_by_courier[str(courier.id)] = courier.vehicle_spec
            if full_range_m_by_courier:
                ev_settings = operations_policy.ev_settings
                # Fetch the current ELECTRIC ₺/kWh tariff once per
                # dispatch run so per-route charging_cost lands in the
                # cost breakdown. Fallback (None) → 0 cost, surfaced as
                # "kWh known, cost unknown" rather than blocking dispatch.
                electricity_price_try_per_kwh: float | None = None
                try:
                    from app.services.energy_price_service import (
                        get_effective_energy_prices,
                    )
                    from app.models.enums import FuelType
                    basis = await get_effective_energy_prices(db)
                    electricity_price_try_per_kwh = basis.prices.get(FuelType.ELECTRIC)
                except Exception as exc:
                    logger.warning(
                        "dispatch: failed to fetch electricity tariff for "
                        "charging_cost accounting (will report kWh without "
                        "₺): %s", exc,
                    )
                solution = inject_charging_stops_into_solution(
                    solution,
                    candidate_chargers_by_courier=candidate_chargers_by_courier,
                    orders_by_id=orders_by_id_for_charging,
                    full_range_m_by_courier=full_range_m_by_courier,
                    initial_charge_m_by_courier=initial_charge_m_by_courier,
                    vehicle_spec_by_courier=vehicle_spec_by_courier,
                    distance_matrix=matrix.distances,
                    loc_index=loc_index,
                    safety_threshold=ev_settings.route_safety_threshold,
                    target_pct=ev_settings.default_charge_target_pct,
                    max_evs_per_station_per_hour=ev_settings.max_evs_per_station_per_hour,
                    electricity_price_try_per_kwh=electricity_price_try_per_kwh,
                )

        # 10. Persist solution
        run_id = optimization_run_id or uuid.uuid4()
        courier_depot_locs = {
            str(c.id): (round(c.depot_lat, 4), round(c.depot_lon, 4))
            for c in couriers
        }
        # Build a set of every active warehouse coordinate for this tenant.
        # Used to detect initial preload PICKUP stops at any warehouse
        # (not just the courier's legacy home depot field) so the multi-trip
        # multi-warehouse model collapses them into a single "load up" event.
        all_depot_rows = (await db.execute(
            select(Depot.lat, Depot.lon).where(
                Depot.tenant_id == tenant_id,
                Depot.is_active == True,  # noqa: E712
            )
        )).all()
        warehouse_locs: set[tuple[float, float]] = {
            (round(lat, 4), round(lon, 4)) for lat, lon in all_depot_rows
        }
        persisted_solution = await _persist_solution(
            db,
            solution,
            orders,
            run_id,
            draft=draft,
            service_anchor=_service_day_anchor(orders, date_from=date_from),
            courier_depot_locs=courier_depot_locs,
            warehouse_locs=warehouse_locs,
        )

        # Layer 2a — promote solver metrics + run greedy counterfactual.
        # Both are non-fatal: a failure logs and continues without
        # blocking the real commit.
        try:
            from app.services.optimization_metrics_promotion import (
                promote_solver_metrics,
            )
            from app.utils.compat import UTC as _UTC
            from datetime import datetime as _dt

            await promote_solver_metrics(
                db,
                run_id=run_id,
                solver_kind="rich_alns",
                metrics=getattr(solve_result, "metrics", None) if "solve_result" in locals() else None,
                time_budget_used_s=(
                    float(getattr(solve_result, "elapsed_s", 0.0))
                    if "solve_result" in locals()
                    else None
                ),
            )
        except Exception:
            logger.exception(
                "dispatch_batch: solver metrics promotion failed for run %s",
                run_id,
            )

        try:
            from app.services.counterfactual_service import (
                run_and_persist_counterfactual,
            )

            await run_and_persist_counterfactual(
                db,
                vrp_input=vrp_input,
                tenant_id=tenant_id,
                optimization_run_id=run_id,
                real_solution=solution,
                operational_date=_service_day_anchor(orders, date_from=date_from).date(),
            )
        except Exception:
            logger.exception(
                "dispatch_batch: counterfactual baseline failed for run %s",
                run_id,
            )

        await db.commit()

        # 11. Diagnose unassigned orders (using per-cluster inputs)
        unassigned_diagnostics = []
        if solution.unassigned_orders:
            for c_input in cluster_vrp_inputs:
                c_order_ids = {o.order_id for o in c_input.orders}
                c_unassigned = [oid for oid in solution.unassigned_orders if oid in c_order_ids]
                if c_unassigned:
                    diag = diagnose_unassigned(c_input, c_unassigned)
                    unassigned_diagnostics.extend(d.to_dict() for d in diag)

        # Compute route quality metrics
        total_assigned = len({
            s.order_id
            for r in solution.routes
            for s in r.stops
            if s.stop_type not in ("DEPOT_RETURN", "DEPOT_RELOAD")
        })
        total_orders = len(orders)
        non_empty_routes = [r for r in solution.routes if r.stops]
        quality_metrics = {
            "total_routes": len(non_empty_routes),
            "total_orders_assigned": total_assigned,
            "total_orders_unassigned": len(solution.unassigned_orders),
            "assignment_rate": round(total_assigned / total_orders, 4) if total_orders > 0 else 0,
            "avg_stops_per_route": (
                round(sum(len(r.stops) for r in non_empty_routes) / len(non_empty_routes), 2)
                if non_empty_routes else 0
            ),
            "avg_route_duration_s": (
                round(sum(r.total_duration_s for r in non_empty_routes) / len(non_empty_routes), 1)
                if non_empty_routes else 0
            ),
            "avg_route_distance_m": (
                round(sum(r.total_distance_m for r in non_empty_routes) / len(non_empty_routes), 1)
                if non_empty_routes else 0
            ),
            "matrix_source": "osrm",
            "objective_breakdown": _serialize_cost_breakdown(solution.cost_breakdown),
        }
        cost_breakdown = _serialize_cost_breakdown(solution.cost_breakdown)
        route_cost_breakdowns = persisted_solution["route_cost_breakdowns"]
        shadow_comparison = None
        if shadow_solution is not None:
            legacy_cost = _serialize_cost_breakdown(solution.cost_breakdown) or {}
            economic_cost = _serialize_cost_breakdown(shadow_solution.cost_breakdown) or {}
            shadow_comparison = {
                "legacy": {
                    "solver_status": solution.solver_status.value,
                    "unassigned_count": len(solution.unassigned_orders),
                    "total_distance_m": solution.total_distance_m,
                    "total_duration_s": solution.total_duration_s,
                    "cost_breakdown": legacy_cost,
                },
                "economic": {
                    "solver_status": shadow_solution.solver_status.value,
                    "unassigned_count": len(shadow_solution.unassigned_orders),
                    "total_distance_m": shadow_solution.total_distance_m,
                    "total_duration_s": shadow_solution.total_duration_s,
                    "cost_breakdown": economic_cost,
                },
                "delta": {
                    "generalized_cost": economic_cost.get("total_generalized_cost", 0)
                    - legacy_cost.get("total_generalized_cost", 0),
                    "late_minutes": economic_cost.get("late_minutes", 0)
                    - legacy_cost.get("late_minutes", 0),
                    "unassigned_count": len(shadow_solution.unassigned_orders)
                    - len(solution.unassigned_orders),
                    "distance_m": shadow_solution.total_distance_m - solution.total_distance_m,
                    "duration_s": shadow_solution.total_duration_s - solution.total_duration_s,
                },
            }

        return _batch_result({
            "routes_created": persisted_solution["routes_created"],
            "unassigned_count": len(solution.unassigned_orders),
            "unassigned_order_ids": solution.unassigned_orders,
            "solver_status": solution.solver_status.value,
            "total_distance_m": solution.total_distance_m,
            "total_duration_s": solution.total_duration_s,
            "optimizer_mode": (
                "ECONOMIC" if primary_optimization_settings is not None else "LEGACY"
            ),
            "economic_optimizer_enabled": economic_optimizer_enabled,
            "shadow_mode_enabled": shadow_mode_enabled,
            "optimization_preset_key": optimization_settings.preset_key,
            "objective_breakdown": cost_breakdown,
            "cost_breakdown": cost_breakdown,
            "route_cost_breakdowns": route_cost_breakdowns,
            "optimization_run_id": str(run_id),
            "matrix_source": matrix.source,
            "traffic_profile_applied": adjusted_matrix.applied,
            "avg_traffic_multiplier": round(adjusted_matrix.avg_multiplier, 4),
            "traffic_profile_hits": adjusted_matrix.profile_hits,
            "traffic_profile_misses": adjusted_matrix.profile_misses,
            "osrm_metrics": osrm_client.get_osrm_metrics_snapshot(),
            "quality_metrics": quality_metrics,
            "validation": validation.summary(),
            "unassigned_diagnostics": unassigned_diagnostics,
            "shadow_comparison": shadow_comparison,
            "solver_metrics": solver_metrics,
            "global_feasibility_summary": solver_metrics.get("global_feasibility_summary"),
            "cluster_feasibility_summary": solver_metrics.get("cluster_feasibility_summary"),
            "seed_solution_summary": solver_metrics.get("seed_solution_summary"),
            "repair_summary": solver_metrics.get("repair_summary"),
            "optimization_trace": solver_metrics.get("optimization_trace"),
            "reload_summary": solver_metrics.get("reload_summary"),
            "unassigned_classification_summary": solver_metrics.get("unassigned_classification_summary"),
            "assignment_count_status": solver_metrics.get("assignment_count_status"),
        })


# ---------------------------------------------------------------------------
# 2. ON-DEMAND DISPATCH
# ---------------------------------------------------------------------------

async def dispatch_on_demand(order_id: str) -> dict:
    """Dispatch a single on-demand order via insertion heuristic.

    Flow:
      0. Pre-flight: verify OSRM is reachable
      1. Fetch the order (verify still PENDING)
      2. Fetch all ONLINE couriers + their active routes
      3. Run insertion heuristic
      4. If candidate found — update route / create new route
      5. Update order status → ASSIGNED
    """
    started = time.monotonic()

    def _on_demand_result(payload: dict) -> dict:
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        perf_metrics.observe_ms("dispatch_on_demand_latency_ms", latency_ms)
        payload.setdefault("dispatch_on_demand_latency_ms", latency_ms)
        return payload

    if not await osrm_client.check_osrm_health():
        logger.error("On-demand dispatch aborted for order %s: OSRM unreachable", order_id)
        return _on_demand_result({"result": "OSRM_UNAVAILABLE"})

    async with get_async_session_factory()() as db:
        # 1. Fetch order
        result = await db.execute(
            select(Order).where(Order.id == uuid.UUID(order_id))
        )
        order = result.scalar_one_or_none()
        if not order:
            return _on_demand_result({"result": "ORDER_NOT_FOUND"})
        if order.status != OrderStatus.PENDING:
            return _on_demand_result(
                {
                    "result": "ORDER_NOT_PENDING",
                    "current_status": order.status.value,
                }
            )

        # 2. Fetch eligible couriers with their routes
        result = await db.execute(
            select(Courier)
            .where(
                Courier.availability_status == AvailabilityStatus.ONLINE,
                tenant_filter(Courier, order.tenant_id),
            )
            .options(selectinload(Courier.routes))
        )
        couriers = list(result.scalars().all())

        if not couriers:
            return _on_demand_result({"result": "NO_ONLINE_COURIERS"})

        # Enforce zone-restriction hard constraint for auto dispatch.
        zoned_couriers = [
            courier
            for courier in couriers
            if (
                courier.zone_polygon is None
                or point_in_geojson_polygon(
                    order.pickup_lat,
                    order.pickup_lon,
                    courier.zone_polygon,
                )
            )
        ]
        if not zoned_couriers:
            return _on_demand_result({"result": "NO_ELIGIBLE_COURIERS_IN_ZONE"})

        # Build insertion inputs (load constraints for service time)
        from app.services.constraint_service import get_global_constraints as _get_gc
        _gc = await _get_gc(db, order.tenant_id)
        service_time_s = _gc["service_time_minutes"] * 60
        operations_policy = await operations_policy_service.get_operations_policy(db, order.tenant_id)

        is_ride = order.type == OrderType.RIDE
        pickup_earliest_s = (
            _datetime_to_day_seconds(order.earliest_pickup)
            if order.earliest_pickup
            else 0
        )
        pickup_latest_s = (
            _datetime_to_day_seconds(order.latest_pickup)
            if order.latest_pickup
            else _datetime_to_day_seconds(order.latest_delivery)
        )

        # Mirror dispatch_vrp_helpers Phase 1.4: when ride_pickup_buffer_enabled,
        # the driver must arrive at the pickup at least `buffer_minutes` before
        # the passenger ready time. Enforced by tightening pickup_latest_s.
        if (
            is_ride
            and operations_policy.feature_flags.ride_pickup_buffer_enabled
            and pickup_latest_s
        ):
            buffer_min = (
                order.pickup_buffer_minutes_override
                if order.pickup_buffer_minutes_override is not None
                else int(operations_policy.ride_settings.pickup_buffer_minutes)
            )
            pickup_latest_s = max(0, pickup_latest_s - buffer_min * 60)
            if pickup_earliest_s and pickup_earliest_s > pickup_latest_s:
                pickup_earliest_s = pickup_latest_s

        od_pkg_summary = await _load_order_package_summaries(db, [order.id], tenant_id=order.tenant_id)
        od_pkg = od_pkg_summary.get(order.id, {})

        new_order = NewOrder(
            order_id=str(order.id),
            pickup_lat=order.pickup_lat,
            pickup_lon=order.pickup_lon,
            delivery_lat=order.delivery_lat,
            delivery_lon=order.delivery_lon,
            weight_grams=round(order.weight_kg * 1000),
            volume_cm3=round(od_pkg.get("total_volume_cm3", 0)),
            package_count=od_pkg.get("count", 1),
            is_fragile=order.is_fragile,
            is_temp_sensitive=order.is_temp_sensitive,
            earliest_s=_datetime_to_day_seconds(order.earliest_delivery),
            latest_s=_datetime_to_day_seconds(order.latest_delivery),
            pickup_earliest_s=pickup_earliest_s,
            pickup_latest_s=pickup_latest_s,
            priority=order.priority,
            service_time_s=service_time_s,
            is_ride=is_ride,
            passenger_count=int(order.passenger_count or 0) if is_ride else 0,
        )

        eligible_couriers = await _build_eligible_couriers(db, zoned_couriers, tenant_id=order.tenant_id)
        strategy = operations_policy.dispatch.assignment_strategy
        if strategy == "LEAST_BUSY" and eligible_couriers:
            min_stop_count = min(len(courier.current_stops) for courier in eligible_couriers)
            eligible_couriers = [
                courier for courier in eligible_couriers
                if len(courier.current_stops) == min_stop_count
            ]
        elif strategy == "PREFERRED_COURIER":
            preferred_courier_id = order.preferred_courier_id
            if preferred_courier_id:
                preferred_candidates = [
                    courier for courier in eligible_couriers
                    if courier.courier_id == str(preferred_courier_id)
                ]
                if preferred_candidates:
                    eligible_couriers = preferred_candidates

        # 3. Run heuristic
        # Resolve a single OSRM profile for the heuristic matrix.
        # If all eligible couriers share the same profile (common case: all cars, or
        # all motorcycles), use that profile. If the fleet is mixed, fall back to
        # "driving" — the heuristic scores relative insertion cost, so a consistent
        # profile is more important than per-vehicle precision here.
        _od_profiles = {
            resolve_osrm_profile_from_values(
                c.vehicle_type,
                gross_weight_kg=round(c.max_weight_kg),
            )
            for c in eligible_couriers
        }
        _od_profile = _od_profiles.pop() if len(_od_profiles) == 1 else "driving"

        matrix_source_counts: dict[str, int] = {}
        traffic_multipliers: list[float] = []
        traffic_applied = False

        async def _matrix_provider(
            coords: list[tuple[float, float]],
        ) -> tuple[list[list[int]], str]:
            nonlocal traffic_applied
            matrix_result = await osrm_client.get_distance_matrix(coords, profile=_od_profile)
            adjusted = await traffic_profile_service.adjust_duration_matrix(
                db,
                coords=coords,
                durations=matrix_result.durations,
                at_time=datetime.now(UTC).replace(tzinfo=None),
            )
            source_key = matrix_result.source
            matrix_source_counts[source_key] = matrix_source_counts.get(source_key, 0) + 1
            if adjusted.applied:
                traffic_applied = True
                traffic_multipliers.append(adjusted.avg_multiplier)
                return adjusted.durations, f"{source_key}+traffic"
            return matrix_result.durations, source_key

        insertion_result = await find_best_insertion(
            new_order,
            eligible_couriers,
            matrix_provider=_matrix_provider,
        )

        if insertion_result.candidate is None:
            logger.info("Insertion heuristic: no candidate for order %s (%s)",
                        order_id, insertion_result.reason)
            return _on_demand_result({
                "result": insertion_result.reason,
                "rejection_summary": insertion_result.rejection_summary,
            })

        candidate = insertion_result.candidate

        # 4. Apply the insertion and update order status
        await _apply_on_demand_insertion(
            db, order, candidate, operations_policy=operations_policy
        )

        return _on_demand_result({
            "result": insertion_result.reason,
            "courier_id": candidate.courier_id,
            "route_id": str(order.route_id),
            "detour_cost_s": candidate.detour_cost_s,
            "matrix_source": (
                max(matrix_source_counts.items(), key=lambda item: item[1])[0]
                if matrix_source_counts
                else "none"
            ),
            "traffic_profile_applied": traffic_applied,
            "avg_traffic_multiplier": (
                round(sum(traffic_multipliers) / len(traffic_multipliers), 4)
                if traffic_multipliers
                else 1.0
            ),
            "osrm_metrics": osrm_client.get_osrm_metrics_snapshot(),
        })


# ---------------------------------------------------------------------------
# 3. RE-OPTIMIZATION (courier offline)
# ---------------------------------------------------------------------------

async def handle_courier_offline(
    courier_id: str,
    *,
    tenant_id: uuid.UUID | None = None,
) -> dict:
    """When a courier goes offline, abort their active route and move
    undelivered orders back to PENDING for re-dispatch.

    Flow:
      1. Find courier's ACTIVE or PLANNED routes
      2. For each route, set status → ABORTED
      3. For each undelivered stop, move the order back to PENDING
      4. Return count of re-queued orders
    """
    requeued = 0
    async with get_async_session_factory()() as db:
        result = await db.execute(
            select(Route)
            .where(
                Route.courier_id == uuid.UUID(courier_id),
                Route.status.in_(ACTIVE_ROUTE_STATUSES),
            )
            .options(selectinload(Route.stops))
        )
        if tenant_id is not None:
            result = await db.execute(
                select(Route)
                .where(
                    Route.courier_id == uuid.UUID(courier_id),
                    Route.status.in_(ACTIVE_ROUTE_STATUSES),
                    tenant_filter(Route, tenant_id),
                )
                .options(selectinload(Route.stops))
            )
        routes = list(result.scalars().unique().all())

        requeue_order_ids = {
            stop.order_id
            for route in routes
            for stop in route.stops
            if stop.status in (StopStatus.PENDING, StopStatus.ARRIVED) and stop.order_id is not None
        }
        order_by_id: dict[uuid.UUID, Order] = {}
        if requeue_order_ids:
            route_tenant_ids = {route.tenant_id for route in routes}
            order_result = await db.execute(
                select(Order).where(
                    Order.id.in_(requeue_order_ids),
                    Order.tenant_id.in_(route_tenant_ids),
                )
            )
            order_by_id = {
                order.id: order for order in order_result.scalars().all()
            }

        from app.models.enums import SLAAlertSeverity, SLAAlertStatus
        from app.models.sla_alert import SLAAlert

        now_ts = datetime.now(UTC).replace(tzinfo=None)
        alerts_to_create: list[SLAAlert] = []

        for route in routes:
            await apply_route_status_transition(
                db,
                route,
                RouteStatus.ABORTED,
                reason="Courier went offline — route aborted by reoptimization",
            )

            requeued_for_this_route = 0
            for stop in route.stops:
                if stop.status in (StopStatus.PENDING, StopStatus.ARRIVED):
                    await apply_stop_status_transition(
                        db,
                        stop,
                        StopStatus.FAILED,
                        reason="Courier went offline",
                    )
                    stop.notes = "Courier went offline"

                    # Move the associated order back to PENDING
                    order = order_by_id.get(stop.order_id)
                    if order and order.status == OrderStatus.ASSIGNED:
                        await apply_order_status_transition(
                            db,
                            order,
                            OrderStatus.PENDING,
                            reason="Courier offline re-queue",
                        )
                        order.assigned_courier_id = None
                        order.route_id = None
                        requeued += 1
                        requeued_for_this_route += 1

            # Surface this incident to dispatchers via SLAAlert so it lands in
            # LiveOps Exception Center, not just the activity log.
            alerts_to_create.append(
                SLAAlert(
                    tenant_id=route.tenant_id,
                    route_id=route.id,
                    stop_id=None,
                    order_id=None,
                    courier_id=route.courier_id,
                    severity=SLAAlertSeverity.CRITICAL,
                    status=SLAAlertStatus.ACTIVE,
                    alert_type="COURIER_OFFLINE",
                    message=(
                        f"Courier went offline mid-route — "
                        f"{requeued_for_this_route} order"
                        f"{'s' if requeued_for_this_route != 1 else ''} re-queued for dispatch"
                    ),
                    estimated_delay_seconds=0,
                    dedupe_key=f"courier_offline:{route.id}",
                    created_at=now_ts,
                    updated_at=now_ts,
                )
            )

        for alert in alerts_to_create:
            db.add(alert)

        await db.commit()

    logger.info(
        "Courier %s went offline: %d routes aborted, %d orders re-queued",
        courier_id, len(routes), requeued,
    )
    return {"routes_aborted": len(routes), "orders_requeued": requeued}


async def approve_optimization_run(run_id: uuid.UUID, principal) -> dict:
    """Transition DRAFT routes from a run to PLANNED and assign orders."""
    async with get_async_session_factory()() as db:
        result = await db.execute(
            select(Route).where(
                Route.optimization_run_id == run_id,
                Route.status == RouteStatus.DRAFT,
                tenant_filter(Route, principal),
            ).options(selectinload(Route.stops))
        )
        draft_routes = list(result.scalars().unique().all())
        if not draft_routes:
            raise HTTPException(status_code=404, detail="No draft routes found for this run")

        approved = 0
        for route in draft_routes:
            await apply_route_status_transition(
                db,
                route,
                RouteStatus.PLANNED,
                reason="Optimization run approved by dispatcher",
            )
            for stop in route.stops:
                order_result = await db.execute(
                    select(Order).where(
                        Order.id == stop.order_id,
                        tenant_filter(Order, route.tenant_id),
                    )
                )
                order = order_result.scalar_one_or_none()
                if order and order.status == OrderStatus.PENDING:
                    order.assigned_courier_id = route.courier_id
                    order.route_id = route.id
                    await apply_order_status_transition(
                        db, order, OrderStatus.ASSIGNED,
                        reason="Optimization run approved",
                    )
            approved += 1

        from app.services import optimization_run_service
        await optimization_run_service.update_run_status(
            db, run_id, OptimizationRunStatus.SUCCEEDED
        )
        await db.commit()
    return {"approved_routes": approved}


async def reject_optimization_run(run_id: uuid.UUID, principal) -> dict:
    """Delete DRAFT routes from a rejected run."""
    async with get_async_session_factory()() as db:
        result = await db.execute(
            select(Route).where(
                Route.optimization_run_id == run_id,
                Route.status == RouteStatus.DRAFT,
                tenant_filter(Route, principal),
            )
        )
        draft_routes = list(result.scalars().all())
        deleted = len(draft_routes)
        for route in draft_routes:
            await db.delete(route)

        from app.services import optimization_run_service
        await optimization_run_service.update_run_status(
            db, run_id, OptimizationRunStatus.FAILED, error_message="Rejected by dispatcher"
        )
        await db.commit()
    return {"deleted_routes": deleted}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _apply_on_demand_insertion(
    db: AsyncSession,
    order: Order,
    candidate: InsertionCandidate,
    *,
    operations_policy=None,
) -> None:
    """Apply the on-demand insertion candidate to the DB.

    ``operations_policy`` is threaded through to ``build_route_stops_for_order``
    so RIDE pickups can be stamped with ``wait_required`` / ``max_wait_minutes``
    when ``ride_no_show_handling_enabled`` is on.
    """
    if candidate.route_id is None:
        # Create a new route
        route = Route(
            tenant_id=order.tenant_id,
            courier_id=uuid.UUID(candidate.courier_id),
            status=RouteStatus.PLANNED,
            planned_distance_km=0,
            optimization_run_id=None,
        )
        db.add(route)
        await db.flush()
        await db.refresh(route)

        pickup_stop, delivery_stop = route_service.build_route_stops_for_order(
            route_id=route.id,
            order=order,
            pickup_sequence=0,
            delivery_sequence=1,
            operations_policy=operations_policy,
        )
        db.add_all([pickup_stop, delivery_stop])
        await db.flush()
        await route_service.retime_route_stops(db, route.id)
        order.route_id = route.id
    else:
        # Insert into existing route — update sequence numbers
        route_id = uuid.UUID(candidate.route_id)
        result = await db.execute(
            select(RouteStop)
            .where(
                RouteStop.route_id == route_id,
                tenant_filter(RouteStop, order.tenant_id),
            )
            .order_by(RouteStop.sequence_number)
        )
        existing_stops = list(result.scalars().all())

        # Shift sequence numbers to make room
        p_pos = candidate.pickup_position
        d_pos = candidate.delivery_position

        _shift_route_stop_sequences(
            existing_stops,
            pickup_position=p_pos,
            delivery_position=d_pos,
        )
        pickup_stop, delivery_stop = route_service.build_route_stops_for_order(
            route_id=route_id,
            order=order,
            pickup_sequence=p_pos,
            delivery_sequence=d_pos,
            operations_policy=operations_policy,
        )
        db.add_all([pickup_stop, delivery_stop])
        await db.flush()
        await route_service.retime_route_stops(db, route_id)
        order.route_id = route_id

    # Update order status
    order.assigned_courier_id = uuid.UUID(candidate.courier_id)
    await apply_order_status_transition(
        db,
        order,
        OrderStatus.ASSIGNED,
        reason="Auto-dispatch assignment",
    )
    await db.commit()


_DEPOT_PROXIMITY_RADIUS_M = 1000.0


def _is_near_depot_loc(lat: float, lon: float, depot_loc: tuple[float, float]) -> bool:
    """Return True if (lat, lon) is within ~1 km of the given depot coordinate.

    Uses a proper great-circle (Haversine) distance check so the threshold
    behaves identically at every latitude. The previous implementation used
    a degree-based rectangle calibrated for ~41 °N (Istanbul) and silently
    drifted at deployments near the equator or above 60 °N.
    """
    dlat, dlon = depot_loc
    return haversine_meters(lat, lon, dlat, dlon) <= _DEPOT_PROXIMITY_RADIUS_M


def _is_depot_loc(lat: float, lon: float, depot_locs: set[tuple[float, float]]) -> bool:
    """Return True if (lat, lon) is within ~1 km of any known depot coordinate."""
    return any(_is_near_depot_loc(lat, lon, depot_loc) for depot_loc in depot_locs)


def _should_hide_initial_depot_pickup(
    *,
    stop_type: str,
    stop_lat: float,
    stop_lon: float,
    order: Order | None,
    courier_depot_loc: tuple[float, float] | None,
    warehouse_locs: set[tuple[float, float]] | None,
    route_work_started: bool,
) -> bool:
    """Hide initial preload PICKUP stops at any warehouse where the courier loads up.

    Drivers physically pick up multiple packages from the same warehouse in one
    visit, so persisting one PICKUP stop per order at the warehouse before any
    deliveries happen is misleading (e.g. "visit warehouse 5 times in a row").
    Once any non-pickup stop or a pickup at a non-warehouse location occurs,
    `route_work_started` flips to True and subsequent stops are persisted as-is.
    """
    if (
        stop_type != "PICKUP"
        or order is None
        or route_work_started
        or order.type == OrderType.REVERSE
    ):
        return False
    if warehouse_locs and _is_depot_loc(stop_lat, stop_lon, warehouse_locs):
        return True
    if courier_depot_loc is not None:
        return _is_near_depot_loc(stop_lat, stop_lon, courier_depot_loc)
    return False


def _should_hide_reload_depot_pickup(
    *,
    stop_type: str,
    stop_lat: float,
    stop_lon: float,
    order: Order | None,
    courier_depot_loc: tuple[float, float] | None,
    warehouse_locs: set[tuple[float, float]] | None,
    in_depot_reload_block: bool,
) -> bool:
    if (
        stop_type != "PICKUP"
        or order is None
        or order.type == OrderType.REVERSE
        or not in_depot_reload_block
    ):
        return False
    if warehouse_locs and _is_depot_loc(stop_lat, stop_lon, warehouse_locs):
        return True
    return bool(courier_depot_loc and _is_near_depot_loc(stop_lat, stop_lon, courier_depot_loc))


async def _assign_order_to_planned_route(
    db: AsyncSession,
    *,
    order: Order,
    planned_route,
    route: Route,
    stop_arrival_time_s: int,
    draft: bool,
    service_anchor: datetime,
    pending_transitions: list[tuple[Order, OrderStatus]] | None = None,
) -> None:
    """Wire one order into a route's in-memory state.

    When `pending_transitions` is provided, the PENDING→ASSIGNED transition
    is queued onto that list rather than applied per-call — the caller is
    expected to flush them in bulk via `apply_order_status_transitions_bulk`.
    This avoids the per-order flush latency that dominated batch persistence
    for non-draft runs (~10ms × N orders).

    Backwards compatible: when `pending_transitions` is None, the old
    per-order transition behaviour is preserved (used by callers that only
    transition one order at a time).
    """
    if draft or order.status != OrderStatus.PENDING:
        return
    order.assigned_courier_id = uuid.UUID(planned_route.courier_id)
    order.route_id = route.id
    order.scheduled_for_date = _seconds_to_service_datetime(
        stop_arrival_time_s,
        service_anchor,
    ).date()
    if pending_transitions is not None:
        pending_transitions.append((order, OrderStatus.ASSIGNED))
    else:
        await apply_order_status_transition(
            db,
            order,
            OrderStatus.ASSIGNED,
            reason="Batch optimization assignment",
        )


async def _persist_solution(
    db: AsyncSession,
    solution: VRPSolution,
    orders: list[Order],
    optimization_run_id: uuid.UUID,
    *,
    draft: bool = False,
    service_anchor: datetime | None = None,
    courier_depot_locs: dict[str, tuple[float, float]] | None = None,
    warehouse_locs: set[tuple[float, float]] | None = None,
) -> dict:
    """Write VRP solution to database.

    Creates Route + RouteStop records and optionally updates Order status.
    When draft=True, routes are created as DRAFT and orders are NOT assigned.
    Returns persistence metadata including created route count and any
    route-level economic breakdowns keyed by route id.

    courier_depot_locs: legacy per-courier home depot coordinates keyed by
    courier id (used as a fallback for the hide-initial-pickup heuristic).
    warehouse_locs: set of every active warehouse coordinate for the tenant.
    Initial preload PICKUP stops at any of these warehouses are collapsed so
    a courier loading 5 orders at the same warehouse shows up as one trip,
    not five sequential warehouse visits. Later depot pickups remain visible.
    """
    order_map = {str(o.id): o for o in orders}
    routes_created = 0
    route_status = RouteStatus.DRAFT if draft else RouteStatus.PLANNED
    route_tenant_id = orders[0].tenant_id if orders else None
    service_anchor = service_anchor or _service_day_anchor(orders)

    # Loaded once for the whole batch; used to stamp wait_required /
    # max_wait_minutes on RIDE pickup stops when ride_no_show_handling_enabled.
    # Fail soft — a missing policy or a test fake should never break persist.
    operations_policy_for_batch = None
    if route_tenant_id is not None:
        try:
            operations_policy_for_batch = (
                await operations_policy_service.get_operations_policy(db, route_tenant_id)
            )
        except Exception:
            operations_policy_for_batch = None
    ride_wait_on_batch = bool(
        operations_policy_for_batch is not None
        and operations_policy_for_batch.feature_flags.ride_no_show_handling_enabled
    )
    ride_max_wait_minutes_batch = (
        int(operations_policy_for_batch.ride_settings.passenger_no_show_wait_minutes)
        if ride_wait_on_batch
        else 0
    )
    route_cost_breakdowns: dict[str, dict] = {}

    # Preload ChargingStation rows referenced by any synthetic
    # __CHARGER_<station>_<courier>__ stop so persistence can write the
    # real station name + address into the RouteStop row instead of the
    # placeholder "Charging station" label. Skipping tenant scope so
    # platform-shared stations (tenant_id NULL) are resolvable too.
    charging_station_by_id: dict = {}
    referenced_station_ids: set = set()
    for planned_route in solution.routes:
        for stop in planned_route.stops:
            if stop.order_id and stop.order_id.startswith("__CHARGER_"):
                parts = stop.order_id.split("_")
                # Format: __CHARGER_<station_uuid>_<courier_uuid>__
                if len(parts) >= 5:
                    try:
                        referenced_station_ids.add(uuid.UUID(parts[2]))
                    except ValueError:
                        pass
    if referenced_station_ids:
        from app.models.charging_station import ChargingStation
        rows = (await db.execute(
            select(ChargingStation).where(
                ChargingStation.id.in_(referenced_station_ids),
            ),
            execution_options={"skip_tenant_scope": True},
        )).scalars().all()
        charging_station_by_id = {row.id: row for row in rows}

    # In non-draft mode each delivered order goes PENDING → ASSIGNED. Done
    # per-order this stacks ~2 DB round-trips × N orders (~10ms × 600 = ~6s
    # of pure DB latency for a typical batch). Collect transitions during the
    # stop loop and flush them in one bulk call after persistence finishes.
    # `_assign_order_to_planned_route` queues onto this list when given.
    pending_transitions: list[tuple[Order, OrderStatus]] = []

    # Build all Route ORM objects up front, then bulk-add and single-flush.
    # The previous code did `db.add(route); await db.flush(); await
    # db.refresh(route)` per route — N routes → 2N round-trips just to allocate
    # IDs. With `add_all + flush` we get all IDs in one round-trip and skip the
    # refresh entirely (the inserted Route already has its server-generated id
    # populated on the in-memory object after flush).
    pending_routes: list[tuple[PlannedRoute, Route]] = []
    for planned_route in solution.routes:
        if not planned_route.stops:
            continue
        route = Route(
            tenant_id=route_tenant_id,
            courier_id=uuid.UUID(planned_route.courier_id),
            status=route_status,
            planned_distance_km=round(
                planned_route.total_distance_m / 1000.0, 2
            ),
            optimization_run_id=optimization_run_id,
        )
        pending_routes.append((planned_route, route))
    if pending_routes:
        db.add_all([r for _, r in pending_routes])
        await db.flush()

    for planned_route, route in pending_routes:
        if planned_route.cost_breakdown is not None:
            route_cost_breakdowns[str(route.id)] = _serialize_cost_breakdown(
                planned_route.cost_breakdown
            ) or {}

        seq = 0
        route_work_started = False
        in_depot_reload_block = False
        courier_depot_loc = (
            courier_depot_locs or {}
        ).get(planned_route.courier_id)
        for stop in planned_route.stops:
            is_depot_stop = stop.stop_type in ("DEPOT_RETURN", "DEPOT_RELOAD")

            if is_depot_stop:
                route_stop = RouteStop(
                    tenant_id=route_tenant_id,
                    route_id=route.id,
                    order_id=None,
                    sequence_number=seq,
                    stop_type=StopType(stop.stop_type),
                    lat=stop.lat,
                    lon=stop.lon,
                    address="Depot",
                    planned_arrival=_seconds_to_service_datetime(stop.arrival_time_s, service_anchor),
                    planned_departure=_seconds_to_service_datetime(stop.departure_time_s, service_anchor),
                    status=StopStatus.PENDING,
                )
                db.add(route_stop)
                seq += 1
                route_work_started = True
                in_depot_reload_block = stop.stop_type == "DEPOT_RELOAD"
                continue

            # Phase 2.3 — synthetic charging-stop detection. The dispatch
            # helper injected these as OrderData rows with the order_id
            # prefix '__CHARGER_'. They round-trip through the solver as
            # PICKUP/DELIVERY pairs at the same node. Persist ONE
            # RouteStop with stop_type=CHARGING (the pickup, which carries
            # the charge time as service_time) and skip the delivery half.
            if stop.order_id and stop.order_id.startswith("__CHARGER_"):
                if stop.stop_type != "PICKUP":
                    # Skip the delivery half; only the pickup gets a row.
                    continue
                parts = stop.order_id.split("_")
                station = None
                if len(parts) >= 5:
                    try:
                        station = charging_station_by_id.get(uuid.UUID(parts[2]))
                    except ValueError:
                        station = None
                # Render the operator + name when we have it ("Voltrun —
                # Levent"), falling back to the station's free-form address
                # or, last-resort, the placeholder. The driver and
                # dispatcher need to know which station this is.
                if station is not None:
                    address = " — ".join(
                        bit for bit in [station.operator, station.name] if bit
                    ) or station.address or "Charging station"
                    if station.address and address != station.address:
                        # Keep the street address as part of the label when
                        # both an operator/name and a real address are known.
                        address = f"{address} ({station.address})"
                else:
                    address = "Charging station"
                route_stop = RouteStop(
                    tenant_id=route_tenant_id,
                    route_id=route.id,
                    order_id=None,
                    sequence_number=seq,
                    stop_type=StopType.CHARGING,
                    lat=stop.lat,
                    lon=stop.lon,
                    address=address[:500],  # column cap
                    planned_arrival=_seconds_to_service_datetime(stop.arrival_time_s, service_anchor),
                    # Crucial: ``departure_time_s - arrival_time_s`` is the
                    # solver-allocated charge duration. Without persisting
                    # this, the driver/dispatcher loses the "plug in for
                    # ~45 min" signal entirely.
                    planned_departure=_seconds_to_service_datetime(stop.departure_time_s, service_anchor),
                    status=StopStatus.PENDING,
                )
                db.add(route_stop)
                seq += 1
                continue

            # Only hide initial preload pickups at this courier's own depot.
            # Mid-route depot reloads, other warehouses, and customer/merchant
            # pickups must stay visible to the courier.
            order_for_check = order_map.get(stop.order_id)
            if _should_hide_initial_depot_pickup(
                stop_type=stop.stop_type,
                stop_lat=stop.lat,
                stop_lon=stop.lon,
                order=order_for_check,
                courier_depot_loc=courier_depot_loc,
                warehouse_locs=warehouse_locs,
                route_work_started=route_work_started,
            ) or _should_hide_reload_depot_pickup(
                stop_type=stop.stop_type,
                stop_lat=stop.lat,
                stop_lon=stop.lon,
                order=order_for_check,
                courier_depot_loc=courier_depot_loc,
                warehouse_locs=warehouse_locs,
                in_depot_reload_block=in_depot_reload_block,
            ):
                # Still mark the order as assigned even though pickup stop is omitted.
                if order_for_check:
                    await _assign_order_to_planned_route(
                        db,
                        order=order_for_check,
                        planned_route=planned_route,
                        route=route,
                        stop_arrival_time_s=stop.arrival_time_s,
                        draft=draft,
                        service_anchor=service_anchor,
                        pending_transitions=pending_transitions,
                    )
                continue

            order = order_map.get(stop.order_id)
            if not order:
                continue
            in_depot_reload_block = False

            is_pickup = stop.stop_type == "PICKUP"
            is_ride_pickup = is_pickup and order.type == OrderType.RIDE
            route_stop = RouteStop(
                tenant_id=order.tenant_id,
                route_id=route.id,
                order_id=order.id,
                sequence_number=seq,
                stop_type=(StopType.PICKUP if is_pickup else StopType.DELIVERY),
                lat=stop.lat,
                lon=stop.lon,
                address=(order.pickup_address if is_pickup else order.delivery_address),
                planned_arrival=_seconds_to_service_datetime(stop.arrival_time_s, service_anchor),
                planned_departure=_seconds_to_service_datetime(stop.departure_time_s, service_anchor),
                status=StopStatus.PENDING,
                wait_required=(ride_wait_on_batch and is_ride_pickup),
                max_wait_minutes=(
                    ride_max_wait_minutes_batch if (ride_wait_on_batch and is_ride_pickup) else 0
                ),
            )
            db.add(route_stop)
            seq += 1
            route_work_started = True

            await _assign_order_to_planned_route(
                db,
                order=order,
                planned_route=planned_route,
                route=route,
                stop_arrival_time_s=stop.arrival_time_s,
                draft=draft,
                service_anchor=service_anchor,
                pending_transitions=pending_transitions,
            )

        routes_created += 1

    # Single bulk flush for all PENDING → ASSIGNED transitions collected
    # during the per-route loop. For a 600-order non-draft batch this turns
    # ~1200 round-trips (one flush + one history insert per order, both
    # awaited per call) into a single flush + add_all of all history rows.
    if pending_transitions:
        await apply_order_status_transitions_bulk(
            db,
            pending_transitions,
            reason="Batch optimization assignment",
        )

    # Capture dispatch decisions for the forensics dashboard. Failure
    # is non-fatal: if the decision-log write fails, the route persist
    # itself still committed.
    try:
        await _record_dispatch_decisions_from_solution(
            db,
            solution=solution,
            orders=orders,
            optimization_run_id=optimization_run_id,
            kind=DispatchDecisionKind.BATCH_DISPATCH,
        )
    except Exception:
        logger.exception(
            "Failed to record dispatch decision log for run %s",
            optimization_run_id,
        )

    return {
        "routes_created": routes_created,
        "route_cost_breakdowns": route_cost_breakdowns,
    }


async def _record_dispatch_decisions_from_solution(
    db: AsyncSession,
    *,
    solution: VRPSolution,
    orders: list[Order],
    optimization_run_id: uuid.UUID,
    kind: DispatchDecisionKind,
) -> None:
    """Extract per-order assignments from `solution` and persist them as
    `DispatchDecisionLog` rows. Each chosen assignment's score is the
    route's total distance — a simple proxy until the solver exposes
    per-order objective contributions."""
    if not orders:
        return
    order_map = {str(o.id): o for o in orders}
    tenant_id = orders[0].tenant_id

    assignments: list[tuple[uuid.UUID, uuid.UUID, float | None]] = []
    for planned_route in solution.routes:
        try:
            physical_id, _, _ = planned_route.courier_id.partition("__day")
            chosen_courier_id = uuid.UUID(physical_id)
        except (ValueError, AttributeError):
            continue
        route_score = float(planned_route.total_distance_m) if planned_route.total_distance_m else None
        for stop in planned_route.stops:
            if not stop.order_id or stop.stop_type != "DELIVERY":
                continue
            order = order_map.get(stop.order_id)
            if order is None:
                continue
            try:
                assignments.append((order.id, chosen_courier_id, route_score))
            except Exception:
                continue

    unassigned: list[tuple[uuid.UUID, str | None]] = []
    for raw_id in solution.unassigned_orders:
        order = order_map.get(str(raw_id))
        if order is not None:
            unassigned.append((order.id, None))

    await record_decisions_for_solution(
        db,
        tenant_id=tenant_id,
        optimization_run_id=optimization_run_id,
        kind=kind,
        assignments=assignments,
        unassigned=unassigned,
    )


async def _build_eligible_couriers(
    db: AsyncSession,
    couriers: list[Courier],
    *,
    tenant_id: uuid.UUID | None = None,
) -> list[EligibleCourier]:
    """Convert Courier ORM objects into EligibleCourier data for the heuristic.

    For each courier, loads their active route stops (if any) and computes
    current load.
    """
    from app.services.constraint_service import get_global_constraints, resolve_courier_constraints
    if not couriers:
        return []
    resolved_tenant_id = tenant_id or couriers[0].tenant_id
    global_c = await get_global_constraints(db, resolved_tenant_id)
    service_time_s = global_c["service_time_minutes"] * 60

    courier_ids = [courier.id for courier in couriers]
    route_result = await db.execute(
        select(Route)
        .where(
            Route.courier_id.in_(courier_ids),
            Route.status.in_(ACTIVE_ROUTE_STATUSES),
            tenant_filter(Route, resolved_tenant_id),
        )
        .options(selectinload(Route.stops))
        .order_by(Route.courier_id, Route.created_at.desc())
    )
    route_by_courier_id: dict[uuid.UUID, Route] = {}
    for route in route_result.scalars().unique().all():
        route_by_courier_id.setdefault(route.courier_id, route)

    relevant_order_ids = {
        stop.order_id
        for route in route_by_courier_id.values()
        for stop in route.stops
        if stop.status not in TERMINAL_STOP_STATUSES and stop.order_id is not None
    }
    orders_by_id: dict[uuid.UUID, Order] = {}
    if relevant_order_ids:
        order_result = await db.execute(
            select(Order).where(
                Order.id.in_(relevant_order_ids),
                tenant_filter(Order, resolved_tenant_id),
            )
        )
        orders_by_id = {
            order.id: order
            for order in order_result.scalars().all()
        }
    order_weight_grams = {
        order_id: round(order.weight_kg * 1000)
        for order_id, order in orders_by_id.items()
    }
    order_volume_cm3 = {
        order_id: int(getattr(order, "volume_cm3", 0) or 0)
        for order_id, order in orders_by_id.items()
    }
    order_package_count = {
        order_id: int(getattr(order, "package_count", 1) or 1)
        for order_id, order in orders_by_id.items()
    }
    order_passenger_count = {
        order_id: int(order.passenger_count or 0) if order.type == OrderType.RIDE else 0
        for order_id, order in orders_by_id.items()
    }

    eligible: list[EligibleCourier] = []

    for courier in couriers:
        current_stops: list[ExistingStop] = []
        active_route = route_by_courier_id.get(courier.id)
        route_id = str(active_route.id) if active_route else None
        starting_load = 0
        starting_volume = 0
        starting_packages = 0
        starting_passengers = 0

        if active_route:
            actionable_stops = [
                stop
                for stop in active_route.stops
                if stop.status not in TERMINAL_STOP_STATUSES
            ]
            starting_load = _estimate_route_starting_load_grams(
                actionable_stops,
                order_weight_grams,
            )
            starting_volume = _estimate_route_starting_load_volume_cm3(
                actionable_stops,
                order_volume_cm3,
            )
            starting_packages = _estimate_route_starting_load_packages(
                actionable_stops,
                order_package_count,
            )
            starting_passengers = _estimate_route_starting_load_passengers(
                actionable_stops,
                order_passenger_count,
            )
            for stop in sorted(active_route.stops,
                               key=lambda s: s.sequence_number):
                if stop.status in TERMINAL_STOP_STATUSES:
                    continue
                if stop.order_id is None:
                    continue

                weight_grams = order_weight_grams.get(stop.order_id, 0)
                volume_cm3 = order_volume_cm3.get(stop.order_id, 0)
                package_count = order_package_count.get(stop.order_id, 0)
                passenger_count = order_passenger_count.get(stop.order_id, 0)
                sign = 1 if stop.stop_type == StopType.PICKUP else -1

                planned_arrival_s = _datetime_to_day_seconds(stop.planned_arrival)
                order = orders_by_id.get(stop.order_id)
                if order and stop.stop_type == StopType.PICKUP:
                    earliest_s, latest_s = _resolve_pickup_window_seconds(order)
                elif order:
                    earliest_s = _datetime_to_day_seconds(order.earliest_delivery)
                    latest_s = _datetime_to_day_seconds(order.latest_delivery)
                else:
                    earliest_s = planned_arrival_s
                    latest_s = planned_arrival_s

                current_stops.append(ExistingStop(
                    lat=stop.lat,
                    lon=stop.lon,
                    earliest_s=earliest_s,
                    latest_s=latest_s,
                    arrival_s=planned_arrival_s,
                    service_s=service_time_s,
                    weight_delta_g=sign * weight_grams,
                    volume_delta_cm3=sign * volume_cm3,
                    package_delta=sign * package_count,
                    passenger_delta=sign * passenger_count,
                ))

        rc = resolve_courier_constraints(global_c, courier)
        max_vol = rc.get("max_volume_cm3")
        cap_vol = round(max_vol) if max_vol else 0
        max_pkg = rc.get("max_packages") or 0
        max_pax = int(courier.max_passengers or 0)

        eligible.append(EligibleCourier(
            courier_id=str(courier.id),
            vehicle_type=courier.vehicle_type,
            capacity_grams=round(rc["max_weight_kg"] * 1000),
            max_weight_kg=float(rc["max_weight_kg"]),
            capacity_volume_cm3=cap_vol,
            max_packages=max_pkg,
            current_load_volume_cm3=starting_volume,
            current_load_packages=starting_packages,
            depot_lat=courier.depot_lat,
            depot_lon=courier.depot_lon,
            start_time_s=_time_to_seconds(courier.working_hours_start),
            end_time_s=_time_to_seconds(courier.working_hours_end),
            route_id=route_id,
            current_stops=current_stops,
            current_load_grams=starting_load,
            allow_fragile=rc.get("allow_fragile", True),
            allow_temp_sensitive=rc.get("allow_temp_sensitive", True),
            max_passengers=max_pax,
            current_load_passengers=starting_passengers,
        ))

    return eligible
def _shift_route_stop_sequences(
    stops: list[RouteStop],
    *,
    pickup_position: int,
    delivery_position: int,
) -> None:
    """Shift stop sequence numbers to make room for a new pickup+delivery pair.

    pickup_position and delivery_position are final-route indexes produced by
    the insertion heuristic. delivery_position is therefore evaluated after
    pickup insertion.
    """
    second_shift_threshold = delivery_position - 1
    for stop in stops:
        if stop.sequence_number >= second_shift_threshold:
            stop.sequence_number += 2
        elif stop.sequence_number >= pickup_position:
            stop.sequence_number += 1
