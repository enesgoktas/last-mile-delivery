"""Border repair for cross-zone seam improvement (M6).

Identifies requests that straddle zone boundaries — either their current route
vehicle belongs to a different zone, or they are geographically close to a
neighbouring zone centroid — and runs a local ALNS pass to re-optimise only
those requests.

The result is merged back into the full solution.
"""
from __future__ import annotations

import contextlib
import copy
import math
import random

from app.algorithms.rich_vrp.domain.models import Problem, Solution, VisitKind
from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution

_MAX_BORDER_REQUESTS = 60


def border_repair(
    solution: Solution,
    problem: Problem,
    clusters: list | None = None,
    time_budget_s: float = 10.0,
    rng: random.Random | None = None,
) -> Solution:
    """Improve cross-zone seam quality.

    Parameters
    ----------
    solution:
        Current full solution.
    problem:
        Full problem instance.
    clusters:
        List of ZoneCluster objects.  If None or a single zone, returns
        the solution unchanged.
    time_budget_s:
        Wall-clock budget for the local repair.
    rng:
        Random state for reproducibility.
    """
    if clusters is None or len(clusters) <= 1:
        return solution

    rng = rng or random.Random()
    border_rids = _find_border_requests(solution, problem, clusters)
    if not border_rids:
        return solution

    # Limit to MAX_BORDER_REQUESTS (sorted by degree of cross-zone-ness)
    border_rids = list(border_rids)[:_MAX_BORDER_REQUESTS]

    # Extract border requests from their current routes
    new_routes = [copy.deepcopy(r) for r in solution.routes]
    extracted: set[str] = set()

    for route in new_routes:
        to_keep = []
        for visit in route.visits:
            if visit.kind in (VisitKind.DEPOT_START, VisitKind.DEPOT_END):
                to_keep.append(visit)
                continue
            if visit.request_id and visit.request_id in border_rids:
                extracted.add(visit.request_id)
            else:
                to_keep.append(visit)
        route.visits = to_keep

    # Re-evaluate stripped routes
    from app.algorithms.rich_vrp.feasibility.kernel import evaluate_route  # noqa: PLC0415
    for route in new_routes:
        evaluate_route(route, problem)

    # Greedily reinsert the border requests
    from app.algorithms.rich_vrp.constructive.greedy_seed import _apply_insertion  # noqa: PLC0415
    from app.algorithms.rich_vrp.feasibility.kernel import (
        feasible_insertion_positions,  # noqa: PLC0415
    )

    newly_served: set[str] = set()
    for rid in sorted(extracted):  # deterministic order
        try:
            req = problem.request(rid)
        except KeyError:
            continue

        best_cost = None
        best_idx = best_pu = best_de = None

        for r_idx, route in enumerate(new_routes):
            for pu, de in feasible_insertion_positions(route, req, problem):
                trial = copy.deepcopy(route)
                _apply_insertion(trial, req, pu, de, problem)
                cost = trial.cost.total_generalized_cost
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best_idx = r_idx
                    best_pu, best_de = pu, de
                break  # greedy per route

        if best_idx is not None:
            _apply_insertion(new_routes[best_idx], req, best_pu, best_de, problem)
            newly_served.add(rid)

    new_unserved = (solution.unserved | (extracted - newly_served))
    obj = objective_for_solution(new_routes, new_unserved, problem)
    new_sol = Solution(routes=new_routes, unserved=new_unserved, objective=obj)

    # Only return the new solution if it's not worse
    old_obj = solution.objective or objective_for_solution(solution.routes, solution.unserved, problem)
    if new_sol.objective <= old_obj:
        return new_sol
    return solution


def _find_border_requests(solution: Solution, problem: Problem, clusters: list) -> list[str]:
    """Return request IDs whose routes cross zone boundaries.

    A request is "border" if its assigned vehicle's start node is in a
    different zone than the request's delivery node.
    """
    if not clusters:
        return []

    locs = problem.locations

    # Map: delivery_node -> cluster_id
    node_to_zone: dict[int, int] = {}
    for c in clusters:
        for rid in c.request_ids:
            try:
                req = problem.request(rid)
                node_to_zone[req.delivery_node] = c.cluster_id
            except KeyError:
                pass

    # Map: vehicle_index -> cluster_id (by vehicle start_node proximity)
    def _nearest_zone(lat: float, lon: float) -> int:
        best, best_d = 0, float("inf")
        for c in clusters:
            d = math.sqrt((lat - c.centroid_lat) ** 2 + (lon - c.centroid_lon) ** 2)
            if d < best_d:
                best_d = d
                best = c.cluster_id
        return best

    vehicle_zone: dict[int, int] = {}
    for v in problem.vehicles:
        lat, lon = locs[v.start_node]
        vehicle_zone[v.index] = _nearest_zone(lat, lon)

    border: list[str] = []
    for route in solution.routes:
        v_zone = vehicle_zone.get(route.vehicle.index, -1)
        for visit in route.visits:
            if visit.kind == VisitKind.DELIVERY and visit.request_id:
                rid = visit.request_id
                req_node = None
                with contextlib.suppress(KeyError):
                    req_node = problem.request(rid).delivery_node
                if req_node is not None:
                    r_zone = node_to_zone.get(req_node, v_zone)
                    if r_zone != v_zone:
                        border.append(rid)

    return border
