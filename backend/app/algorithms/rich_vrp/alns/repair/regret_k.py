"""Regret-k repair: prioritise requests with large insertion cost differences."""
from __future__ import annotations

import copy
import random

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.constructive.greedy_seed import _apply_insertion, _candidate_vehicles
from app.algorithms.rich_vrp.domain.models import Problem, Request
from app.algorithms.rich_vrp.feasibility.kernel import feasible_insertion_positions

_INF = 10 ** 12


def repair(
    state: ALNSState,
    problem: Problem,
    removed: list[Request],
    rng: random.Random,
    k: int = 2,
) -> ALNSState:
    """Regret-k repair: repeatedly insert the request with the highest regret.

    Regret = difference between best and k-th best insertion cost across all vehicles.
    Requests with high regret are more urgently inserted first.
    """
    pending = list(removed)

    while pending:
        best_regret = -1
        best_req: Request | None = None
        best_route_idx: int | None = None
        best_pickup: int | None = None
        best_delivery: int | None = None

        for req in pending:
            costs: list[tuple[int, int, int]] = []  # (cost, route_idx, pickup, delivery)
            candidates = _candidate_vehicles(req, state.solution.routes, problem, k=24)
            for route_idx in candidates:
                route = state.solution.routes[route_idx]
                for pickup_pos, delivery_pos in feasible_insertion_positions(route, req, problem):
                    trial = copy.deepcopy(route)
                    _apply_insertion(trial, req, pickup_pos, delivery_pos, problem)
                    cost = trial.cost.total_generalized_cost
                    costs.append((cost, route_idx, pickup_pos, delivery_pos))
                    break

            if not costs:
                # No feasible insertion — regret is high but we track it separately
                continue

            costs.sort(key=lambda x: x[0])
            best_cost = costs[0][0]
            kth_cost = costs[min(k - 1, len(costs) - 1)][0]
            regret = kth_cost - best_cost

            if regret > best_regret:
                best_regret = regret
                best_req = req
                best_route_idx = costs[0][1]
                best_pickup = costs[0][2]
                best_delivery = costs[0][3]

        if best_req is None:
            # No feasible insertions remain — mark all remaining as unserved
            break

        # Insert the highest-regret request
        _apply_insertion(state.solution.routes[best_route_idx], best_req, best_pickup, best_delivery, problem)
        state.solution.unserved.discard(best_req.id)
        pending.remove(best_req)

    state.recompute_objective()
    return state
