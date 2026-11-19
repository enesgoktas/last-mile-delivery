"""Time-window destroy: removes the q requests with the most lateness."""
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
    """Remove q requests with the highest delivery lateness."""
    # Score each served request by its lateness in seconds
    lateness: list[tuple[str, int]] = []
    for route in state.solution.routes:
        for visit in route.visits:
            if visit.kind != VisitKind.DELIVERY or not visit.request_id:
                continue
            try:
                req = problem.request(visit.request_id)
            except KeyError:
                continue
            soft_deadline = req.delivery_window[1]
            late_s = max(visit.arrival_s - soft_deadline, 0)
            lateness.append((visit.request_id, late_s))

    if not lateness:
        return state, []

    lateness.sort(key=lambda x: x[1], reverse=True)
    chosen_ids = [rid for rid, _ in lateness[:min(q, len(lateness))]]

    removed: list[Request] = []
    for rid in chosen_ids:
        req = state.remove_request(rid)
        if req is not None:
            state.add_unserved(rid)
            removed.append(req)

    state.recompute_objective()
    return state, removed
