"""Greedy paired-insertion seeder for the rich VRP engine.

Constructs an initial solution by inserting requests one at a time
(URGENT first) into their cheapest feasible (pickup_pos, delivery_pos).
"""
from __future__ import annotations

from app.algorithms.rich_vrp.domain.models import (
    Problem,
    Request,
    Route,
    Solution,
    Vehicle,
    Visit,
    VisitKind,
)
from app.algorithms.rich_vrp.feasibility.kernel import (
    Feasibility,
    evaluate_route,
    feasible_insertion_positions,
    is_compatible,
)
from app.algorithms.rich_vrp.objective.cost import compute_route_cost
from app.models.enums import OrderPriority

_PRIORITY_SORT_KEY = {
    OrderPriority.URGENT: 0,
    OrderPriority.HIGH: 1,
    OrderPriority.NORMAL: 2,
    OrderPriority.LOW: 3,
}


def seed_greedy(problem: Problem, *, candidate_size: int = 24) -> Solution:
    """Construct an initial solution via greedy paired insertion.

    Requests are processed in priority order (URGENT first).  For each request
    the cheapest feasible (pickup_pos, delivery_pos) across all compatible vehicles
    is chosen.  Requests with no feasible insertion go to unserved.

    Args:
        problem: The solver-internal problem description.
        candidate_size: Maximum number of vehicles to evaluate per request
                        (k-nearest by depot distance).  Acts as a perf knob;
                        does not affect correctness when k >= n_vehicles.
    """
    # Initialise one empty route per vehicle
    routes: list[Route] = [_empty_route(v) for v in problem.vehicles]

    # Sort requests: priority first, then hard deadline, then latest delivery window
    sorted_requests = sorted(
        problem.requests,
        key=lambda r: (
            _PRIORITY_SORT_KEY.get(r.priority, 99),
            r.hard_delivery_deadline or r.delivery_window[1],
            r.delivery_window[1],
        ),
    )

    unserved: set[str] = set()

    for req in sorted_requests:
        best_cost: int | None = None
        best_route_idx: int | None = None
        best_pickup_pos: int | None = None
        best_delivery_pos: int | None = None

        # Limit candidates to compatible vehicles ordered by depot proximity
        candidate_indices = _candidate_vehicles(req, routes, problem, candidate_size)

        for route_idx in candidate_indices:
            route = routes[route_idx]
            for pickup_pos, delivery_pos in feasible_insertion_positions(route, req, problem):
                # Tentative insertion: compute incremental cost
                cost = _insertion_cost(route, req, pickup_pos, delivery_pos, problem)
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best_route_idx = route_idx
                    best_pickup_pos = pickup_pos
                    best_delivery_pos = delivery_pos
                break  # take first feasible position per route (greedy)

        if best_route_idx is not None:
            # Snapshot the route's visits so we can revert if the post-insertion
            # evaluation comes back HARD_INFEASIBLE. Without this the seed can
            # commit insertions that the production validator later rejects
            # (e.g. when the upstream feasibility check missed a constraint).
            route = routes[best_route_idx]
            visits_before = list(route.visits)
            _apply_insertion(route, req, best_pickup_pos, best_delivery_pos, problem)
            if not all(r.feasible for r in [route]):
                # Roll back and mark unserved.
                route.visits = visits_before
                evaluate_route(route, problem)
                unserved.add(req.id)
        else:
            unserved.add(req.id)

    return Solution(routes=routes, unserved=unserved)


def _empty_route(vehicle: Vehicle) -> Route:
    depot_start = Visit(
        request_id=None,
        kind=VisitKind.DEPOT_START,
        node=vehicle.start_node,
        arrival_s=vehicle.shift_window[0],
        departure_s=vehicle.shift_window[0],
    )
    depot_end = Visit(
        request_id=None,
        kind=VisitKind.DEPOT_END,
        node=vehicle.end_node,
        arrival_s=vehicle.shift_window[0],
        departure_s=vehicle.shift_window[0],
    )
    return Route(vehicle=vehicle, visits=[depot_start, depot_end])


def _candidate_vehicles(
    req: Request,
    routes: list[Route],
    problem: Problem,
    k: int,
) -> list[int]:
    """Return up to k route indices for compatible vehicles, nearest-depot first."""
    compatible = [
        i for i, r in enumerate(routes)
        if is_compatible(req, r.vehicle)
    ]
    if len(compatible) <= k:
        return compatible
    # Sort by distance from pickup node to vehicle start node
    matrix = problem.matrix
    compatible.sort(
        key=lambda i: matrix.distance(routes[i].vehicle.start_node, req.pickup_node, routes[i].vehicle.profile)
    )
    return compatible[:k]


def _insertion_cost(
    route: Route,
    req: Request,
    pickup_pos: int,
    delivery_pos: int,
    problem: Problem,
) -> int:
    """Estimate the marginal cost of inserting req at (pickup_pos, delivery_pos)."""
    import copy
    trial = copy.deepcopy(route)
    _apply_insertion(trial, req, pickup_pos, delivery_pos, problem)
    cost = compute_route_cost(trial, problem)
    return cost.total_generalized_cost


def _apply_insertion(
    route: Route,
    req: Request,
    pickup_pos: int,
    delivery_pos: int,
    problem: Problem,
) -> None:
    """Insert req's pickup and delivery visits into the route and re-evaluate."""
    pickup_visit = Visit(
        request_id=req.id,
        kind=VisitKind.PICKUP,
        node=req.pickup_node,
        arrival_s=0,
        departure_s=0,
    )
    delivery_visit = Visit(
        request_id=req.id,
        kind=VisitKind.DELIVERY,
        node=req.delivery_node,
        arrival_s=0,
        departure_s=0,
    )
    route.visits.insert(pickup_pos, pickup_visit)
    route.visits.insert(delivery_pos, delivery_visit)
    evaluate_route(route, problem)
