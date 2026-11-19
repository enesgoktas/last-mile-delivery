"""SREX-lite crossover for PDPTW.

Selective Route Exchange (SREX) hybridised for paired pickup-delivery:

1. Randomly select a subset of routes from parent1 to keep intact.
2. Collect all requests not covered by those routes.
3. Sort unassigned requests by their delivery order in parent2 (to inherit
   parent2's sequencing insight).
4. Greedy-insert all unassigned requests into the offspring.
"""
from __future__ import annotations

import copy
import random

from app.algorithms.rich_vrp.constructive.greedy_seed import _apply_insertion
from app.algorithms.rich_vrp.domain.models import Problem, Route, Solution, VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import evaluate_route, feasible_insertion_positions
from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution


def srex(
    parent1: Solution,
    parent2: Solution,
    problem: Problem,
    rng: random.Random,
) -> Solution:
    """Selective Route Exchange crossover. Returns a new offspring Solution."""
    routes1 = parent1.routes
    if not routes1:
        return copy.deepcopy(parent2)

    # Choose what fraction of parent1's routes to preserve (30–60%)
    frac = rng.uniform(0.3, 0.6)
    n_keep = max(1, round(len(routes1) * frac))
    kept_indices = set(rng.sample(range(len(routes1)), min(n_keep, len(routes1))))

    # Build offspring: deep-copy all routes; strip non-kept routes to depots only
    offspring_routes: list[Route] = []
    served_rids: set[str] = set()

    for idx, route in enumerate(routes1):
        new_route = copy.deepcopy(route)
        if idx in kept_indices:
            for v in route.visits:
                if v.kind == VisitKind.DELIVERY and v.request_id:
                    served_rids.add(v.request_id)
        else:
            new_route.visits = [
                v for v in new_route.visits
                if v.kind in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)
            ]
            evaluate_route(new_route, problem)
        offspring_routes.append(new_route)

    # Collect unassigned requests; order by their position in parent2
    p2_order = _delivery_order(parent2)
    all_rids = {req.id for req in problem.requests}
    unassigned = [rid for rid in p2_order if rid not in served_rids]
    not_in_p2 = [rid for rid in all_rids if rid not in served_rids and rid not in set(p2_order)]
    unassigned += not_in_p2

    # Greedy insertion of unassigned requests
    for rid in unassigned:
        try:
            req = problem.request(rid)
        except KeyError:
            continue

        best_cost: int | None = None
        best_idx: int | None = None
        best_pu: int | None = None
        best_de: int | None = None

        for r_idx, route in enumerate(offspring_routes):
            for pu, de in feasible_insertion_positions(route, req, problem):
                trial = copy.deepcopy(route)
                _apply_insertion(trial, req, pu, de, problem)
                cost = trial.cost.total_generalized_cost
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best_idx = r_idx
                    best_pu = pu
                    best_de = de
                break  # greedy: first feasible per route

        if best_idx is not None:
            _apply_insertion(offspring_routes[best_idx], req, best_pu, best_de, problem)
            served_rids.add(rid)

    unserved = all_rids - served_rids
    obj = objective_for_solution(offspring_routes, unserved, problem)
    return Solution(routes=offspring_routes, unserved=unserved, objective=obj)


def _delivery_order(solution: Solution) -> list[str]:
    """Return request IDs in order of first delivery appearance across all routes."""
    order: list[str] = []
    seen: set[str] = set()
    for route in solution.routes:
        for v in route.visits:
            if v.kind == VisitKind.DELIVERY and v.request_id and v.request_id not in seen:
                order.append(v.request_id)
                seen.add(v.request_id)
    return order
