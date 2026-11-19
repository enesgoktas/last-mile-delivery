"""Compatibility-first repair: most-restricted requests inserted first."""
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
    """Insert requests sorted by compatibility scarcity (most restricted first).

    Requests with the fewest compatible vehicles are inserted first to
    reduce the chance they end up unserved due to later requests filling slots.
    """
    def _scarcity(req: Request) -> int:
        """Number of vehicles that can serve this request (lower = scarcer)."""
        from app.algorithms.rich_vrp.feasibility.kernel import is_compatible
        return sum(
            1 for route in state.solution.routes
            if is_compatible(req, route.vehicle)
        )

    # Sort by scarcity ascending (fewest compatible vehicles first)
    ordered = sorted(removed, key=_scarcity)

    for req in ordered:
        best_cost: int | None = None
        best_route_idx: int | None = None
        best_pickup: int | None = None
        best_delivery: int | None = None

        candidates = _candidate_vehicles(req, state.solution.routes, problem, k=24)
        for route_idx in candidates:
            route = state.solution.routes[route_idx]
            for pickup_pos, delivery_pos in feasible_insertion_positions(route, req, problem):
                trial = copy.deepcopy(route)
                _apply_insertion(trial, req, pickup_pos, delivery_pos, problem)
                cost = trial.cost.total_generalized_cost
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best_route_idx = route_idx
                    best_pickup = pickup_pos
                    best_delivery = delivery_pos
                break

        if best_route_idx is not None:
            _apply_insertion(state.solution.routes[best_route_idx], req, best_pickup, best_delivery, problem)
            state.solution.unserved.discard(req.id)

    state.recompute_objective()
    return state
