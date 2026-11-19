"""Shaw related-removal destroy operator.

Removes q requests that are similar to each other, measured by a weighted
combination of spatial, temporal, load, and compatibility distance.
"""
from __future__ import annotations

import random

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import Problem, Request

_ALPHA = 0.5   # spatial weight
_BETA = 0.3    # temporal weight
_GAMMA = 0.1   # load similarity
_DELTA = 0.1   # compatibility overlap


def destroy(
    state: ALNSState,
    problem: Problem,
    q: int,
    rng: random.Random,
) -> tuple[ALNSState, list[Request]]:
    """Shaw related-removal: remove q mutually similar requests."""
    active_ids = list(state.active_request_ids())
    if not active_ids:
        return state, []

    # Seed the removal with a random request
    seed_id = rng.choice(active_ids)
    chosen_ids: list[str] = [seed_id]

    while len(chosen_ids) < min(q, len(active_ids)):
        # Pick a random already-removed request as the reference
        ref_id = rng.choice(chosen_ids)
        try:
            ref_req = problem.request(ref_id)
        except KeyError:
            break

        # Score remaining active requests by relatedness to ref_req
        candidates = [rid for rid in active_ids if rid not in chosen_ids]
        if not candidates:
            break

        scored = [
            (rid, _relatedness(ref_req, problem.request(rid) if _safe_request(problem, rid) else None, problem))
            for rid in candidates
        ]
        scored = [(rid, s) for rid, s in scored if s is not None]
        if not scored:
            break

        # Shaw's method: sort by relatedness, pick from the most related with randomisation
        scored.sort(key=lambda x: x[1])
        # Use random power to introduce some randomness (standard Shaw trick)
        idx = int(rng.random() ** 5 * len(scored))
        chosen_ids.append(scored[idx][0])

    removed: list[Request] = []
    for rid in chosen_ids:
        req = state.remove_request(rid)
        if req is not None:
            state.add_unserved(rid)
            removed.append(req)

    state.recompute_objective()
    return state, removed


def _safe_request(problem: Problem, request_id: str) -> bool:
    try:
        problem.request(request_id)
        return True
    except KeyError:
        return False


def _relatedness(ref: Request, other: Request | None, problem: Problem) -> float | None:
    if other is None:
        return None
    matrix = problem.matrix

    # Spatial: distance between delivery nodes (normalised by a rough max)
    spatial = matrix.distance(ref.delivery_node, other.delivery_node) / 100_000.0

    # Temporal: overlap between delivery windows
    r_start, r_end = ref.delivery_window
    o_start, o_end = other.delivery_window
    temporal_diff = abs(r_start - o_start) / max(r_end - r_start, 1)

    # Load similarity: difference in total weight load
    ref_load = ref.load.get("weight_g", 0)
    oth_load = other.load.get("weight_g", 0)
    max_load = max(ref_load, oth_load, 1)
    load_diff = abs(ref_load - oth_load) / max_load

    # Compatibility: number of required tags in common (inverted so 0 = very similar)
    ref_tags = ref.required_tags
    oth_tags = other.required_tags
    union = len(ref_tags | oth_tags)
    compat_diff = 1.0 - (len(ref_tags & oth_tags) / union) if union else 0.0

    return _ALPHA * spatial + _BETA * temporal_diff + _GAMMA * load_diff + _DELTA * compat_diff
