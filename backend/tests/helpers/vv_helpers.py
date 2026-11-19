"""Shared helpers for V&V tests (determinism, boundary, feasibility-gate).

Two responsibilities:
  - validate_vrp_solution(solution, vrp): independent feasibility check using
    the production validator with metadata extracted from a VRPInput.
  - make_minimal_vrp(...): build trivially-small or degenerate VRP instances
    for boundary-condition testing.
"""
from __future__ import annotations

import math

from app.algorithms.solution_validator import (
    RouteConstraints,
    ValidationResult,
    validate_solution,
)
from app.algorithms.vrp_solver import (
    OrderData,
    VehicleData,
    VRPInput,
    VRPSolution,
)
from app.models.enums import OrderPriority, VehicleType


def validate_vrp_solution(solution: VRPSolution, vrp: VRPInput) -> ValidationResult:
    """Run the production validator with args extracted from a VRPInput.

    Separate from app.services.dispatch_vrp_helpers.validate_batch_solution() —
    that one consumes ORM Order/Vehicle entities and is async-DB coupled. This
    helper takes the solver-input VRPInput so V&V tests can validate any
    solver output without touching the database.
    """
    order_weights = {o.order_id: o.weight_grams for o in vrp.orders}
    order_volumes = {o.order_id: o.volume_cm3 for o in vrp.orders}
    order_packages = {o.order_id: o.package_count for o in vrp.orders}
    order_tws = {
        o.order_id: (o.earliest_time_s, o.hard_latest_time_s or o.latest_time_s)
        for o in vrp.orders
    }
    order_pickup_tws = {
        o.order_id: (
            o.pickup_earliest_time_s or 0,
            o.pickup_latest_time_s or o.latest_time_s,
        )
        for o in vrp.orders
    }
    depot_pickups = {o.order_id for o in vrp.orders if o.pickup_is_depot}

    constraint_map = {
        v.courier_id: RouteConstraints(
            capacity_grams=v.capacity_grams,
            capacity_volume_cm3=v.capacity_volume_cm3,
            max_packages=v.max_packages,
            start_time_s=v.start_time_s,
            end_time_s=v.hard_end_time_s or v.end_time_s,
            max_stops=v.max_stops,
            max_distance_m=v.max_distance_m,
            depot_index=v.depot_index,
        )
        for v in vrp.vehicles
    }

    return validate_solution(
        solution,
        order_weights=order_weights,
        order_volumes=order_volumes,
        order_package_counts=order_packages,
        route_constraints=constraint_map,
        order_time_windows=order_tws,
        order_pickup_windows=order_pickup_tws,
        depot_pickup_order_ids=depot_pickups,
    )


# ---------------------------------------------------------------------------
# Minimal VRP builders for boundary-condition tests
# ---------------------------------------------------------------------------

_DAY_START = 8 * 3600
_DAY_END = 18 * 3600
_DEPOT_LAT = 41.015
_DEPOT_LON = 28.979


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> int:
    R = 6_371_000.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return int(2 * R * math.asin(math.sqrt(h)))


def make_minimal_vrp(
    *,
    n_orders: int,
    n_vehicles: int,
    same_location: bool = False,
    capacity_grams: int = 50_000,
    max_packages: int = 50,
    weight_per_order_grams: int = 1_000,
    earliest_time_s: int = _DAY_START,
    latest_time_s: int = _DAY_END,
    hard_latest_time_s: int | None = None,
    location_offset_deg: float = 0.02,
) -> VRPInput:
    """Build a trivially-small VRPInput for boundary-condition testing.

    Customer locations are spaced symmetrically around a fixed Istanbul depot,
    one customer per order. Travel times use a 36 km/h average. All vehicles
    share the same depot at index 0.
    """
    locations: list[tuple[float, float]] = [(_DEPOT_LAT, _DEPOT_LON)]

    if n_orders == 0:
        # Single-node matrix (just the depot).
        dist_matrix = [[0]]
        dur_matrix = [[0]]
        orders: list[OrderData] = []
    else:
        for k in range(n_orders):
            if same_location:
                locations.append((_DEPOT_LAT + location_offset_deg, _DEPOT_LON + location_offset_deg))
            else:
                # Spread customers in a small ring around the depot.
                angle = 2 * math.pi * k / max(n_orders, 1)
                locations.append((
                    _DEPOT_LAT + location_offset_deg * math.cos(angle),
                    _DEPOT_LON + location_offset_deg * math.sin(angle),
                ))

        n_locs = len(locations)
        dist_matrix = [[0] * n_locs for _ in range(n_locs)]
        dur_matrix = [[0] * n_locs for _ in range(n_locs)]
        for i in range(n_locs):
            for j in range(n_locs):
                if i == j:
                    continue
                d = _haversine_m(locations[i], locations[j])
                dist_matrix[i][j] = d
                dur_matrix[i][j] = d // 10  # ~36 km/h

        orders = [
            OrderData(
                order_id=f"ord-{k:03d}",
                pickup_index=0,  # depot pickup keeps the matrix small (1 + n_orders nodes)
                delivery_index=k + 1,
                pickup_is_depot=True,
                weight_grams=weight_per_order_grams,
                volume_cm3=1_000,
                package_count=1,
                earliest_time_s=earliest_time_s,
                latest_time_s=latest_time_s,
                hard_latest_time_s=hard_latest_time_s,
                service_time_s=120,
                priority=OrderPriority.NORMAL,
                unserved_penalty=10_000,
                lateness_penalty_per_minute=10,
                priority_penalty_multiplier=1.0,
            )
            for k in range(n_orders)
        ]

    vehicles = [
        VehicleData(
            courier_id=f"veh-{v:02d}",
            vehicle_type=VehicleType.CAR_VAN,
            physical_courier_id=f"veh-{v:02d}",
            depot_index=0,
            start_time_s=_DAY_START,
            end_time_s=_DAY_END,
            hard_end_time_s=_DAY_END + 3600,
            capacity_grams=capacity_grams,
            capacity_volume_cm3=500_000,
            max_packages=max_packages,
            distance_cost_per_km=10,
            labor_cost_per_minute=2,
            fuel_cost_per_km=5,
            overtime_penalty_per_minute=5,
            trip_index=0,
        )
        for v in range(n_vehicles)
    ]

    return VRPInput(
        orders=orders,
        vehicles=vehicles,
        locations=locations,
        duration_matrix=dur_matrix,
        distance_matrix=dist_matrix,
    )


def served_order_ids(solution: VRPSolution) -> list[str]:
    """Return the sorted list of order_ids appearing as DELIVERY stops."""
    return sorted(
        s.order_id
        for r in solution.routes
        for s in r.stops
        if s.stop_type == "DELIVERY" and s.order_id
    )


def solution_signature(solution: VRPSolution) -> tuple:
    """Stable hashable summary of a solution — used for determinism comparison.

    rich_alns and ortools_cvrptw terminate on a wall-clock budget, so two
    runs with the same seed may run for slightly different numbers of
    iterations and produce sub-optimally-different per-stop arrival times
    or cost (typically ±0.5%). What MUST be reproducible across runs at the
    same seed is the *combinatorial* output: the set of orders served and
    the set left unassigned. That's what we compare here.

    For tighter strictness use `solution_signature_with_cost` — but be aware
    it's only meaningful when the engine is iteration-bounded.
    """
    return (
        tuple(served_order_ids(solution)),
        tuple(sorted(solution.unassigned_orders)),
    )


def solution_signature_with_cost(solution: VRPSolution) -> tuple:
    """Strict signature including cost and distance.

    Useful when comparing two solutions you expect to be byte-identical
    (e.g. one solver run with two different seeds that should produce the
    same answer because the input has only one feasible plan).
    """
    cb = solution.cost_breakdown
    return (
        cb.total_generalized_cost if cb else None,
        solution.total_distance_m,
        tuple(served_order_ids(solution)),
        tuple(sorted(solution.unassigned_orders)),
    )
