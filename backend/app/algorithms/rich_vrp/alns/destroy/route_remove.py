"""Route-level destroy operator: empties the most expensive route(s)."""
from __future__ import annotations

import random

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import Problem, Request, VisitKind


def destroy(
    state: ALNSState,
    problem: Problem,
    q: int,
    rng: random.Random,
) -> tuple[ALNSState, list[Request]]:
    """Remove all requests from the most expensive non-empty route.

    q is used as a budget cap — we stop when we've removed at least q requests
    (or emptied all non-empty routes if fewer than q exist).
    """
    # Find non-empty routes ordered by cost-per-served-order descending
    non_empty = [
        r for r in state.solution.routes
        if any(v.kind == VisitKind.DELIVERY for v in r.visits)
    ]
    if not non_empty:
        return state, []

    def _cost_per_order(route) -> float:
        served = sum(1 for v in route.visits if v.kind == VisitKind.DELIVERY)
        cost = route.cost.total_generalized_cost
        return cost / max(served, 1)

    non_empty.sort(key=_cost_per_order, reverse=True)

    removed: list[Request] = []
    for route in non_empty:
        if len(removed) >= q:
            break
        # Collect request IDs served on this route
        request_ids = [
            v.request_id
            for v in route.visits
            if v.kind == VisitKind.DELIVERY and v.request_id
        ]
        for rid in request_ids:
            req = state.remove_request(rid)
            if req is not None:
                state.add_unserved(rid)
                removed.append(req)

    state.recompute_objective()
    return state, removed
