"""Intra-route 2-opt move.

Applies 2-opt within each route, respecting pickup-delivery precedence.
Exposed through the standard search() interface.
"""
from __future__ import annotations

import time

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import Problem, Route, VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import evaluate_route
from app.algorithms.rich_vrp.local_search.candidate_lists import CandidateLists
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator

# Cap inner improvement passes per route. Without this guard, route_two_opt's
# `while improved` can churn for many seconds on long routes when many small
# local improvements are available. Capping at 3 passes keeps the algorithm
# semantically equivalent (still 2-opt, still first-improving) while bounding
# worst-case per-route runtime.
_MAX_PASSES_PER_ROUTE = 3


def search(
    state: ALNSState,
    problem: Problem,
    candidates: CandidateLists,
    evaluator: DeltaEvaluator,
    max_moves: int = 1,
    deadline_s: float | None = None,
) -> int:
    """Apply intra-route 2-opt to every route. Return total improving moves found."""
    applied = 0
    for route in state.solution.routes:
        if deadline_s is not None and time.perf_counter() >= deadline_s:
            break
        before = route.cost.total_generalized_cost
        route_two_opt(route, problem, deadline_s=deadline_s)
        if route.cost.total_generalized_cost < before:
            applied += 1
    return applied


def route_two_opt(route: Route, problem: Problem, *, deadline_s: float | None = None) -> None:
    """Apply intra-route 2-opt, respecting pickup-delivery precedence."""
    inner = [
        v for v in route.visits
        if v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)
    ]
    n = len(inner)
    if n < 4:
        return

    matrix = problem.matrix
    veh = route.vehicle

    improved = True
    passes = 0
    while improved and passes < _MAX_PASSES_PER_ROUTE:
        if deadline_s is not None and time.perf_counter() >= deadline_s:
            break
        improved = False
        passes += 1
        for i in range(n - 1):
            if deadline_s is not None and time.perf_counter() >= deadline_s:
                break
            for j in range(i + 2, n):
                new_inner = inner[:i + 1] + list(reversed(inner[i + 1:j + 1])) + inner[j + 1:]
                if not _respects_precedence(new_inner):
                    continue
                old_cost = _segment_travel(inner, matrix, veh, i, j)
                new_cost = _segment_travel(new_inner, matrix, veh, i, j)
                if new_cost < old_cost:
                    inner = new_inner
                    improved = True
            if improved:
                break

    depot_start = [v for v in route.visits if v.kind == VisitKind.DEPOT_START]
    depot_end = [v for v in route.visits if v.kind == VisitKind.DEPOT_END]
    route.visits = depot_start + inner + depot_end
    evaluate_route(route, problem)


def _respects_precedence(visits: list) -> bool:
    seen: set[str] = set()
    for v in visits:
        if v.kind == VisitKind.PICKUP and v.request_id:
            seen.add(v.request_id)
        if v.kind == VisitKind.DELIVERY and v.request_id and v.request_id not in seen:
            return False
    return True


def _segment_travel(visits: list, matrix, veh, i: int, j: int) -> int:
    cost = 0
    if i + 1 < len(visits):
        cost += matrix.duration(visits[i].node, visits[i + 1].node, veh.profile)
    if j + 1 < len(visits):
        cost += matrix.duration(visits[j].node, visits[j + 1].node, veh.profile)
    return cost
