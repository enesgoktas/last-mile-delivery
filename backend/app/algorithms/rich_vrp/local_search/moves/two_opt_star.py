"""Inter-route 2-opt* (SwapTails) move.

Picks two arcs from two different routes and swaps their tails.
Maintains pickup-delivery precedence — if swapping would separate a pickup
from its delivery, the move is rejected.
"""
from __future__ import annotations

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import Problem, VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import Feasibility, evaluate_route
from app.algorithms.rich_vrp.local_search.candidate_lists import CandidateLists
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator


def search(
    state: ALNSState,
    problem: Problem,
    candidates: CandidateLists,
    evaluator: DeltaEvaluator,
    max_moves: int = 1,
) -> int:
    """Try inter-route SwapTails (2-opt*). Return number of moves applied."""
    applied = 0
    routes = state.solution.routes
    n = len(routes)
    if n < 2:
        return 0

    for i in range(n):
        if applied >= max_moves:
            break
        for j in range(i + 1, n):
            if applied >= max_moves:
                break
            ra, rb = routes[i], routes[j]
            inner_a = [v for v in ra.visits if v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)]
            inner_b = [v for v in rb.visits if v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)]
            if not inner_a or not inner_b:
                continue

            depot_start_a = [v for v in ra.visits if v.kind == VisitKind.DEPOT_START]
            depot_end_a = [v for v in ra.visits if v.kind == VisitKind.DEPOT_END]
            depot_start_b = [v for v in rb.visits if v.kind == VisitKind.DEPOT_START]
            depot_end_b = [v for v in rb.visits if v.kind == VisitKind.DEPOT_END]

            cost_ab = ra.cost.total_generalized_cost + rb.cost.total_generalized_cost

            for cut_a in range(len(inner_a)):
                for cut_b in range(len(inner_b)):
                    # new_a = inner_a[:cut_a+1] + inner_b[cut_b+1:]
                    # new_b = inner_b[:cut_b+1] + inner_a[cut_a+1:]
                    new_inner_a = inner_a[:cut_a + 1] + inner_b[cut_b + 1:]
                    new_inner_b = inner_b[:cut_b + 1] + inner_a[cut_a + 1:]

                    if not _precedence_ok(new_inner_a) or not _precedence_ok(new_inner_b):
                        continue

                    trial_a_visits = depot_start_a + new_inner_a + depot_end_a
                    trial_b_visits = depot_start_b + new_inner_b + depot_end_b

                    trial_a = type(ra)(vehicle=ra.vehicle, visits=trial_a_visits, total_distance_m=0, total_duration_s=0)
                    trial_b = type(rb)(vehicle=rb.vehicle, visits=trial_b_visits, total_distance_m=0, total_duration_s=0)

                    ev_a = evaluate_route(trial_a, problem)
                    ev_b = evaluate_route(trial_b, problem)

                    if ev_a.feasibility == Feasibility.HARD_INFEASIBLE or ev_b.feasibility == Feasibility.HARD_INFEASIBLE:
                        continue

                    new_cost = trial_a.cost.total_generalized_cost + trial_b.cost.total_generalized_cost
                    if new_cost < cost_ab:
                        ra.visits = trial_a_visits
                        evaluate_route(ra, problem)
                        rb.visits = trial_b_visits
                        evaluate_route(rb, problem)
                        applied += 1
                        cost_ab = ra.cost.total_generalized_cost + rb.cost.total_generalized_cost
                        inner_a = [v for v in ra.visits if v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)]
                        inner_b = [v for v in rb.visits if v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)]
                        break
                else:
                    continue
                break

    return applied


def _precedence_ok(visits: list) -> bool:
    seen: set[str] = set()
    for v in visits:
        if v.kind == VisitKind.PICKUP and v.request_id:
            seen.add(v.request_id)
        if v.kind == VisitKind.DELIVERY and v.request_id and v.request_id not in seen:
            return False
    return True
