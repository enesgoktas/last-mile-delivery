"""Route-level economic cost computation for the rich VRP engine."""
from __future__ import annotations

from app.algorithms.rich_vrp.domain.models import (
    CostBreakdown,
    Problem,
    Route,
    VisitKind,
)


def _seconds_to_billed_minutes(seconds: int) -> int:
    if seconds <= 0:
        return 0
    return (seconds + 59) // 60


def compute_route_cost(route: Route, problem: Problem) -> CostBreakdown:
    """Compute economic cost for a route whose visits already have arrival/departure times set."""
    vehicle = route.vehicle
    matrix = problem.matrix

    # Distance: sum of arcs between consecutive visits (including depot→first and last→depot)
    distance_m = 0
    prev_node = vehicle.start_node
    for visit in route.visits:
        if visit.kind == VisitKind.DEPOT_START:
            prev_node = visit.node
            continue
        distance_m += matrix.distance(prev_node, visit.node, vehicle.profile)
        prev_node = visit.node

    # Duration: from first departure to last arrival
    actual_visits = [v for v in route.visits if v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)]
    if not actual_visits:
        return CostBreakdown()

    route_end_time = actual_visits[-1].departure_s

    route_distance_km = distance_m / 1000.0
    distance_cost = int(round(route_distance_km * vehicle.cost.distance_per_km))
    fuel_cost = int(round(route_distance_km * vehicle.cost.fuel_per_km))

    estimated_fuel_liters: float | None = None
    if vehicle.fuel_efficiency_km_per_liter and vehicle.fuel_efficiency_km_per_liter > 0:
        estimated_fuel_liters = round(route_distance_km / vehicle.fuel_efficiency_km_per_liter, 3)
    estimated_co2_g: float | None = None
    if vehicle.co2_g_per_km and vehicle.co2_g_per_km > 0:
        estimated_co2_g = round(route_distance_km * vehicle.co2_g_per_km, 1)

    # Labour: shift length from start_time to end of last stop
    shift_start = vehicle.shift_window[0]
    duration_s = max(route_end_time - shift_start, 0)
    labor_cost = int(round((duration_s * vehicle.cost.labor_per_min) / 60.0))

    # Lateness penalty
    lateness_penalty = 0
    late_minutes = 0
    for visit in route.visits:
        if visit.kind != VisitKind.DELIVERY or visit.request_id is None:
            continue
        try:
            req = problem.request(visit.request_id)
        except KeyError:
            continue
        soft_deadline = req.delivery_window[1]
        late_s = max(visit.arrival_s - soft_deadline, 0)
        if late_s > 0:
            late_minutes += _seconds_to_billed_minutes(late_s)
            lateness_penalty += int(
                round((late_s * req.lateness_penalty_per_min * req.priority_multiplier) / 60.0)
            )

    # Overtime
    soft_end = vehicle.shift_window[1]
    overtime_s = max(route_end_time - soft_end, 0)
    overtime_minutes = _seconds_to_billed_minutes(overtime_s)
    overtime_penalty = int(round((overtime_s * vehicle.cost.overtime_per_min) / 60.0))

    fixed_route_cost = vehicle.cost.fixed_route if actual_visits else 0

    total = (
        distance_cost
        + fuel_cost
        + labor_cost
        + fixed_route_cost
        + lateness_penalty
        + overtime_penalty
    )

    return CostBreakdown(
        total_generalized_cost=total,
        distance_cost=distance_cost,
        labor_cost=labor_cost,
        fuel_cost=fuel_cost,
        fixed_route_cost=fixed_route_cost,
        lateness_penalty=lateness_penalty,
        overtime_penalty=overtime_penalty,
        late_minutes=late_minutes,
        overtime_minutes=overtime_minutes,
        estimated_fuel_liters=estimated_fuel_liters,
        estimated_co2_g=estimated_co2_g,
    )
