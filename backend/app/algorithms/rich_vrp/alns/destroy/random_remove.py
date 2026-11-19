"""Random destroy operator: removes q randomly chosen requests."""
from __future__ import annotations

import random

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import Problem, Request


def destroy(
    state: ALNSState,
    problem: Problem,
    q: int,
    rng: random.Random,
) -> tuple[ALNSState, list[Request]]:
    """Remove q randomly selected served requests."""
    active = list(state.active_request_ids())
    if not active:
        return state, []

    chosen_ids = rng.sample(active, min(q, len(active)))
    removed: list[Request] = []
    for rid in chosen_ids:
        req = state.remove_request(rid)
        if req is not None:
            state.add_unserved(rid)
            removed.append(req)

    state.recompute_objective()
    return state, removed
