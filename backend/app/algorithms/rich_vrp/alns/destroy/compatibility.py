"""Compatibility destroy: removes requests whose vehicle match is barely adequate."""
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
    """Remove requests assigned to vehicles with tight compatibility (low tag slack).

    'Tight' means the request's required_tags are a large fraction of the
    vehicle's tags — leaving little room for other requests that might need
    different tags.  Removing these lets ALNS try to reassign them to better-
    matched vehicles.
    """
    scored: list[tuple[str, float]] = []
    for route in state.solution.routes:
        veh = route.vehicle
        for visit in route.visits:
            if visit.kind != VisitKind.DELIVERY or not visit.request_id:
                continue
            try:
                req = problem.request(visit.request_id)
            except KeyError:
                continue
            if not req.required_tags:
                score = 0.0
            else:
                # Tightness = (required tags) / (vehicle tags) — higher = tighter fit
                score = len(req.required_tags) / max(len(veh.tags), 1)
            scored.append((visit.request_id, score))

    if not scored:
        return state, []

    scored.sort(key=lambda x: x[1], reverse=True)
    chosen_ids = [rid for rid, _ in scored[:min(q, len(scored))]]

    removed: list[Request] = []
    for rid in chosen_ids:
        req = state.remove_request(rid)
        if req is not None:
            state.add_unserved(rid)
            removed.append(req)

    state.recompute_objective()
    return state, removed
