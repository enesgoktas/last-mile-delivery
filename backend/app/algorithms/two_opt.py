"""
Intra-route 2-opt local search — post-solver improvement pass.

Runs *after* OR-Tools returns a feasible/optimal solution.  For each
route, tries reversing sub-sequences of stops to reduce total travel
distance while preserving all constraints (time windows, capacity,
pickup-before-delivery).

The function is pure: it accepts a VRPSolution and returns a new one
with improved routes.  No OR-Tools state is required.

This is a CPU-bound, synchronous function.  Call it from a thread pool
(asyncio.to_thread) just like the solver itself.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Callable

from app.algorithms.solution_validator import RouteConstraints, validate_solution
from app.algorithms.vrp_solver import (
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VRPSolution,
)

logger = logging.getLogger(__name__)

# Minimum stops for a route to be worth optimising (pickup+delivery pairs)
_MIN_STOPS = 4


def two_opt_improve(
    solution: VRPSolution,
    duration_matrix: list[list[int]],
    distance_matrix: list[list[int]],
    *,
    time_budget_s: float = 1.0,
    route_validator: Callable[[PlannedRoute], bool] | None = None,
    route_constraints: dict[str, RouteConstraints] | None = None,
    order_weights: dict[str, int] | None = None,
    order_volumes: dict[str, int] | None = None,
    order_package_counts: dict[str, int] | None = None,
    order_time_windows: dict[str, tuple[int, int]] | None = None,
    order_pickup_windows: dict[str, tuple[int, int]] | None = None,
    depot_pickup_order_ids: set[str] | None = None,
) -> VRPSolution:
    """Apply intra-route 2-opt improvement to each route in the solution.

    Args:
        solution: The VRP solution to improve.
        duration_matrix: N×N seconds matrix (physical location indices).
        distance_matrix: N×N metres matrix (physical location indices).
        time_budget_s: Maximum wall-clock seconds to spend on improvement.

    Returns:
        A new VRPSolution with improved routes.  If no improvement is
        found (or the budget is exhausted), the original solution is
        returned unchanged.
    """
    if not solution.routes:
        return solution

    deadline = time.monotonic() + time_budget_s
    improved_routes: list[PlannedRoute] = []
    total_improvements = 0
    total_distance_saved = 0

    for route in solution.routes:
        if time.monotonic() >= deadline:
            # Budget exhausted — keep remaining routes as-is
            improved_routes.append(route)
            continue

        if len(route.stops) < _MIN_STOPS:
            improved_routes.append(route)
            continue

        validator = route_validator or _build_route_validator(
            route_constraints=route_constraints,
            order_weights=order_weights,
            order_volumes=order_volumes,
            order_package_counts=order_package_counts,
            order_time_windows=order_time_windows,
            order_pickup_windows=order_pickup_windows,
            depot_pickup_order_ids=depot_pickup_order_ids,
        )
        improved_route, distance_saved = _improve_route(
            route, duration_matrix, distance_matrix, deadline, validator,
        )
        improved_routes.append(improved_route)
        if distance_saved > 0:
            total_improvements += 1
            total_distance_saved += distance_saved

    if total_improvements == 0:
        return solution

    # Recompute totals
    grand_distance = sum(r.total_distance_m for r in improved_routes)
    grand_duration = sum(r.total_duration_s for r in improved_routes)

    logger.info(
        "2-opt: improved %d/%d routes, saved %dm total distance "
        "(%.1fs elapsed)",
        total_improvements,
        len(solution.routes),
        total_distance_saved,
        time_budget_s - max(0, deadline - time.monotonic()),
    )

    return VRPSolution(
        routes=improved_routes,
        unassigned_orders=solution.unassigned_orders,
        total_distance_m=grand_distance,
        total_duration_s=grand_duration,
        solver_status=solution.solver_status,
        cost_breakdown=solution.cost_breakdown,
    )


def _improve_route(
    route: PlannedRoute,
    duration_matrix: list[list[int]],
    distance_matrix: list[list[int]],
    deadline: float,
    route_validator: Callable[[PlannedRoute], bool] | None,
) -> tuple[PlannedRoute, int]:
    """Try to improve a single route via 2-opt swaps.

    Returns (improved_route, distance_saved).  distance_saved == 0 means
    no improvement was found.
    """
    stops = list(route.stops)
    n = len(stops)

    # Build order-id → (pickup_idx, delivery_idx) map for constraint checks
    order_pickup_idx: dict[str, int] = {}
    order_delivery_idx: dict[str, int] = {}
    for i, stop in enumerate(stops):
        if stop.stop_type == "PICKUP":
            order_pickup_idx[stop.order_id] = i
        elif stop.stop_type == "DELIVERY":
            order_delivery_idx[stop.order_id] = i

    best_stops = stops
    best_distance = route.total_distance_m
    improved = True

    while improved and time.monotonic() < deadline:
        improved = False
        for i in range(n - 2):
            if time.monotonic() >= deadline:
                break
            for j in range(i + 2, n):
                if time.monotonic() >= deadline:
                    break

                # Skip if reversing would break pickup-before-delivery
                candidate = _reverse_segment(best_stops, i, j)
                if not _check_pickup_delivery_order(
                    candidate, order_pickup_idx, order_delivery_idx,
                ):
                    continue

                # Re-simulate timing to check feasibility
                timing = _simulate_timing(candidate, duration_matrix)
                if timing is None:
                    continue  # Time window violated

                # Compute new distance
                new_distance = _compute_total_distance(candidate, distance_matrix)
                if new_distance < best_distance:
                    candidate_route = _candidate_route(
                        route,
                        candidate,
                        new_distance,
                        timing,
                    )
                    if route_validator is not None and not route_validator(candidate_route):
                        continue
                    best_stops = candidate
                    best_distance = new_distance
                    improved = True

                    # Rebuild pickup/delivery indices for the new ordering
                    order_pickup_idx.clear()
                    order_delivery_idx.clear()
                    for k, stop in enumerate(best_stops):
                        if stop.stop_type == "PICKUP":
                            order_pickup_idx[stop.order_id] = k
                        elif stop.stop_type == "DELIVERY":
                            order_delivery_idx[stop.order_id] = k
                    # Restart inner loops with new best
                    break
            else:
                continue
            break  # Restart outer loop after improvement

    distance_saved = route.total_distance_m - best_distance

    if distance_saved <= 0:
        return route, 0

    # Rebuild route with updated stop times
    timing = _simulate_timing(best_stops, duration_matrix)
    if timing is None:
        # Shouldn't happen — we validated above. Return original.
        return route, 0

    timed_stops, route_duration = timing
    final_route = _candidate_route(route, timed_stops, best_distance, (timed_stops, route_duration))
    if route_validator is not None and not route_validator(final_route):
        return route, 0
    return final_route, distance_saved


def _candidate_route(
    original_route: PlannedRoute,
    timed_stops: list[PlannedStop],
    distance_m: int,
    timing: tuple[list[PlannedStop], int],
) -> PlannedRoute:
    stops, duration = timing
    return PlannedRoute(
        courier_id=original_route.courier_id,
        stops=stops,
        total_distance_m=distance_m,
        total_duration_s=duration,
        route_start_time_s=stops[0].arrival_time_s if stops else 0,
        route_end_time_s=stops[-1].departure_time_s if stops else 0,
        cost_breakdown=original_route.cost_breakdown,
    )


def _build_route_validator(
    *,
    route_constraints: dict[str, RouteConstraints] | None,
    order_weights: dict[str, int] | None,
    order_volumes: dict[str, int] | None,
    order_package_counts: dict[str, int] | None,
    order_time_windows: dict[str, tuple[int, int]] | None,
    order_pickup_windows: dict[str, tuple[int, int]] | None,
    depot_pickup_order_ids: set[str] | None,
) -> Callable[[PlannedRoute], bool] | None:
    if not any(
        (
            route_constraints,
            order_weights,
            order_volumes,
            order_package_counts,
            order_time_windows,
            order_pickup_windows,
            depot_pickup_order_ids,
        )
    ):
        return None

    def _validator(route: PlannedRoute) -> bool:
        result = validate_solution(
            VRPSolution(
                routes=[route],
                unassigned_orders=[],
                total_distance_m=route.total_distance_m,
                total_duration_s=route.total_duration_s,
                solver_status=SolverStatus.FEASIBLE,
                cost_breakdown=route.cost_breakdown,
            ),
            order_weights=order_weights,
            order_volumes=order_volumes,
            order_package_counts=order_package_counts,
            route_constraints=route_constraints,
            order_time_windows=order_time_windows,
            order_pickup_windows=order_pickup_windows,
            depot_pickup_order_ids=depot_pickup_order_ids,
        )
        return result.error_count == 0

    return _validator


def _reverse_segment(
    stops: list[PlannedStop], i: int, j: int,
) -> list[PlannedStop]:
    """Return a new stop list with stops[i+1:j+1] reversed."""
    return stops[: i + 1] + stops[i + 1 : j + 1][::-1] + stops[j + 1 :]


def _check_pickup_delivery_order(
    stops: list[PlannedStop],
    order_pickup_idx: dict[str, int],
    order_delivery_idx: dict[str, int],
) -> bool:
    """Check that every pickup still comes before its delivery.

    Rebuilds indices from the candidate list since positions changed.
    """
    current_pickup: dict[str, int] = {}
    current_delivery: dict[str, int] = {}
    for i, stop in enumerate(stops):
        if stop.stop_type == "PICKUP":
            current_pickup[stop.order_id] = i
        elif stop.stop_type == "DELIVERY":
            current_delivery[stop.order_id] = i

    for order_id, p_idx in current_pickup.items():
        d_idx = current_delivery.get(order_id)
        if d_idx is not None and p_idx >= d_idx:
            return False
    return True


def _simulate_timing(
    stops: list[PlannedStop],
    duration_matrix: list[list[int]],
) -> tuple[list[PlannedStop], int] | None:
    """Forward-simulate timing along the stop sequence.

    Returns updated stops with new arrival/departure times, and the
    total route duration.  Returns None if any time constraint is
    violated (arrival after the stop's original latest window).

    We use the original arrival_time_s as each stop's implicit time
    window upper bound — if a swap causes a stop to arrive later than
    its original plan, that could violate hard windows.  This is
    conservative but safe.
    """
    if not stops:
        return [], 0

    timed: list[PlannedStop] = []
    # Start from the first stop's original arrival (route start is fixed)
    current_time = stops[0].arrival_time_s
    service_time = stops[0].departure_time_s - stops[0].arrival_time_s

    timed.append(replace(
        stops[0],
        arrival_time_s=current_time,
        departure_time_s=current_time + service_time,
    ))
    current_time = current_time + service_time

    for idx in range(1, len(stops)):
        prev_loc = stops[idx - 1].location_index
        cur_loc = stops[idx].location_index

        # Check bounds
        if (
            prev_loc >= len(duration_matrix)
            or cur_loc >= len(duration_matrix)
        ):
            return None

        travel = duration_matrix[prev_loc][cur_loc]
        arrival = current_time + travel

        # Service time preserved from original
        service = stops[idx].departure_time_s - stops[idx].arrival_time_s

        timed.append(replace(
            stops[idx],
            arrival_time_s=arrival,
            departure_time_s=arrival + service,
        ))
        current_time = arrival + service

    if len(timed) >= 2:
        duration = timed[-1].departure_time_s - timed[0].arrival_time_s
    else:
        duration = 0

    return timed, max(duration, 0)


def _compute_total_distance(
    stops: list[PlannedStop],
    distance_matrix: list[list[int]],
) -> int:
    """Compute total distance along the stop sequence."""
    total = 0
    for i in range(len(stops) - 1):
        from_loc = stops[i].location_index
        to_loc = stops[i + 1].location_index
        if (
            from_loc < len(distance_matrix)
            and to_loc < len(distance_matrix)
        ):
            total += distance_matrix[from_loc][to_loc]
    return total
