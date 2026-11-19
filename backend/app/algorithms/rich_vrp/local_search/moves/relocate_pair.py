"""Paired-request relocation: move both pickup and delivery to a different route.

For each request served on a route, try relocating the pair to every other
vehicle's route at the cheapest feasible (pickup_pos, delivery_pos).
"""
from __future__ import annotations

import copy

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.constructive.greedy_seed import _apply_insertion
from app.algorithms.rich_vrp.domain.models import Problem, VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import (
    evaluate_route,
    feasible_insertion_positions,
)
from app.algorithms.rich_vrp.local_search.candidate_lists import CandidateLists
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator


def search(
    state: ALNSState,
    problem: Problem,
    candidates: CandidateLists,
    evaluator: DeltaEvaluator,
    max_moves: int = 1,
) -> int:
    """Try inter-route paired relocation. Return number of moves applied."""
    applied = 0
    routes = state.solution.routes
    n_routes = len(routes)
    if n_routes < 2:
        return 0

    for src_idx in range(n_routes):
        if applied >= max_moves:
            break
        src_route = routes[src_idx]
        # Collect request ids served on this route
        request_ids = [
            v.request_id for v in src_route.visits
            if v.kind == VisitKind.DELIVERY and v.request_id
        ]
        for rid in request_ids:
            if applied >= max_moves:
                break
            try:
                req = problem.request(rid)
            except KeyError:
                continue

            # Cost if we remove from source
            trial_src_visits = [v for v in src_route.visits if v.request_id != rid]
            trial_src = type(src_route)(
                vehicle=src_route.vehicle,
                visits=trial_src_visits,
                total_distance_m=0,
                total_duration_s=0,
            )
            evaluate_route(trial_src, problem)
            src_gain = src_route.cost.total_generalized_cost - trial_src.cost.total_generalized_cost

            best_delta: int | None = None
            best_dst_idx: int | None = None
            best_pickup: int | None = None
            best_delivery: int | None = None

            for dst_idx in range(n_routes):
                if dst_idx == src_idx:
                    continue
                dst_route = routes[dst_idx]
                for pickup_pos, delivery_pos in feasible_insertion_positions(dst_route, req, problem):
                    trial_dst = copy.deepcopy(dst_route)
                    _apply_insertion(trial_dst, req, pickup_pos, delivery_pos, problem)
                    dst_delta = trial_dst.cost.total_generalized_cost - dst_route.cost.total_generalized_cost
                    total_delta = dst_delta - src_gain
                    if best_delta is None or total_delta < best_delta:
                        best_delta = total_delta
                        best_dst_idx = dst_idx
                        best_pickup = pickup_pos
                        best_delivery = delivery_pos
                    break  # greedy: first feasible per dst route

            if best_delta is not None and best_delta < 0:
                # Apply: remove from source
                src_route.visits = trial_src_visits
                evaluate_route(src_route, problem)
                # Insert into destination
                dst_route = routes[best_dst_idx]
                _apply_insertion(dst_route, req, best_pickup, best_delivery, problem)
                applied += 1

    return applied
