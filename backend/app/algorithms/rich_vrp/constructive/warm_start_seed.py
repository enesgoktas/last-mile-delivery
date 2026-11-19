"""Warm-start seeder: converts CachedAssignment into an internal Solution.

Reads the previous solve's order-to-courier mapping and reconstructs routes
in the internal domain model.  Any request that no longer has a matching
vehicle, or that fails feasibility on re-evaluation, is dropped and returned
in the to_repair list for re-insertion.
"""
from __future__ import annotations

from app.algorithms.rich_vrp.constructive.greedy_seed import _apply_insertion, _empty_route
from app.algorithms.rich_vrp.domain.models import (
    Problem,
    Route,
    Solution,
    VisitKind,
)
from app.algorithms.rich_vrp.feasibility.kernel import (
    evaluate_route,
    is_compatible,
)
from app.algorithms.warm_start_cache import CachedAssignment


def seed_from_warm_start(
    cached: CachedAssignment,
    problem: Problem,
) -> tuple[Solution, list[str]]:
    """Convert a CachedAssignment into an internal Solution.

    Returns:
        (solution, to_repair) — the seeded solution and the list of order IDs
        that could not be mapped (vehicle gone, compatibility failed, or
        feasibility violated).  The caller should re-insert to_repair via
        the greedy seeder or leave them unserved.
    """
    vehicle_by_id = {v.id: v for v in problem.vehicles}
    request_by_id = {r.id: r for r in problem.requests}

    # Build one route per vehicle in the problem
    routes_by_vehicle_id: dict[str, Route] = {
        v.id: _empty_route(v) for v in problem.vehicles
    }

    to_repair: list[str] = []

    for courier_id, order_ids in cached.courier_routes.items():
        if courier_id not in routes_by_vehicle_id:
            to_repair.extend(order_ids)
            continue

        route = routes_by_vehicle_id[courier_id]
        vehicle = vehicle_by_id[courier_id]

        for order_id in order_ids:
            req = request_by_id.get(order_id)
            if req is None:
                # Order no longer in the problem (cancelled or rescheduled)
                continue
            if not is_compatible(req, vehicle):
                to_repair.append(order_id)
                continue
            # Append pickup then delivery at the end of the current route
            # (before DEPOT_END), then re-evaluate
            end_idx = len(route.visits) - 1  # index of DEPOT_END
            _apply_insertion(route, req, end_idx, end_idx + 1, problem)

    # Re-evaluate all routes and collect feasibility failures
    surviving_routes: list[Route] = []
    final_unserved: set[str] = set()

    for route in routes_by_vehicle_id.values():
        if len(route.visits) <= 2:  # only DEPOT_START + DEPOT_END
            surviving_routes.append(route)
            continue

        from app.algorithms.rich_vrp.feasibility.kernel import Feasibility
        evaluation = evaluate_route(route, problem)

        if evaluation.feasibility == Feasibility.HARD_INFEASIBLE:
            # Eject all requests from this route into to_repair
            ejected = {
                v.request_id
                for v in route.visits
                if v.kind == VisitKind.DELIVERY and v.request_id
            }
            to_repair.extend(ejected)
            # Reset route to empty
            route.visits = [route.visits[0], route.visits[-1]]
        surviving_routes.append(route)

    final_unserved.update(to_repair)

    return Solution(routes=surviving_routes, unserved=final_unserved), to_repair
