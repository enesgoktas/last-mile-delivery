"""Worst-cost destroy operator: removes the q requests with highest removal gain."""
from __future__ import annotations

import random

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import Problem, Request
from app.algorithms.rich_vrp.feasibility.kernel import evaluate_route


def destroy(
    state: ALNSState,
    problem: Problem,
    q: int,
    rng: random.Random,
) -> tuple[ALNSState, list[Request]]:
    """Remove q requests whose removal reduces cost the most."""
    active_ids = list(state.active_request_ids())
    if not active_ids:
        return state, []

    # Score each active request by how much removing it saves
    gains: list[tuple[str, int]] = []
    for rid in active_ids:
        route = state.route_for_request(rid)
        if route is None:
            continue
        cost_before = route.cost.total_generalized_cost
        # Temporarily build a version of the route without this request
        trial_visits = [v for v in route.visits if v.request_id != rid]
        import copy

        from app.algorithms.rich_vrp.domain.models import Route
        trial_route = Route(
            vehicle=route.vehicle,
            visits=copy.deepcopy(trial_visits),
        )
        evaluate_route(trial_route, problem)
        cost_after = trial_route.cost.total_generalized_cost
        gains.append((rid, cost_before - cost_after))

    # Sort by gain descending, pick top q
    gains.sort(key=lambda x: x[1], reverse=True)
    chosen_ids = [rid for rid, _ in gains[:min(q, len(gains))]]

    removed: list[Request] = []
    for rid in chosen_ids:
        req = state.remove_request(rid)
        if req is not None:
            state.add_unserved(rid)
            removed.append(req)

    state.recompute_objective()
    return state, removed
