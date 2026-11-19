"""Cost-normalized repair: greedy insertion with priority-weighted cost."""
from __future__ import annotations

import copy
import random

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.constructive.greedy_seed import _apply_insertion, _candidate_vehicles
from app.algorithms.rich_vrp.domain.models import Problem, Request
from app.algorithms.rich_vrp.feasibility.kernel import feasible_insertion_positions


def repair(
    state: ALNSState,
    problem: Problem,
    removed: list[Request],
    rng: random.Random,
) -> ALNSState:
    """Insert requests in order of priority_multiplier (most important first).

    Cost scoring divides the raw insertion cost by the request's
    priority_multiplier so high-priority requests are preferred in
    contested insertion slots.
    """
    # Sort by priority multiplier descending (high priority → insert first)
    ordered = sorted(removed, key=lambda r: r.priority_multiplier, reverse=True)

    for req in ordered:
        best_score: float | None = None
        best_route_idx: int | None = None
        best_pickup: int | None = None
        best_delivery: int | None = None

        candidates = _candidate_vehicles(req, state.solution.routes, problem, k=24)
        for route_idx in candidates:
            route = state.solution.routes[route_idx]
            for pickup_pos, delivery_pos in feasible_insertion_positions(route, req, problem):
                trial = copy.deepcopy(route)
                _apply_insertion(trial, req, pickup_pos, delivery_pos, problem)
                raw_cost = trial.cost.total_generalized_cost
                score = raw_cost / max(req.priority_multiplier, 0.01)
                if best_score is None or score < best_score:
                    best_score = score
                    best_route_idx = route_idx
                    best_pickup = pickup_pos
                    best_delivery = delivery_pos
                break

        if best_route_idx is not None:
            _apply_insertion(state.solution.routes[best_route_idx], req, best_pickup, best_delivery, problem)
            state.solution.unserved.discard(req.id)

    state.recompute_objective()
    return state
