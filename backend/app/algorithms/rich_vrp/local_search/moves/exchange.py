"""Inter-route (1,1) exchange: swap one request from route A with one from route B.

Tries all pairs of request assignments across all route pairs; applies the
best improving swap.
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
    """Try (1,1) inter-route swap. Return number of moves applied."""
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
            rids_a = [v.request_id for v in ra.visits if v.kind == VisitKind.DELIVERY and v.request_id]
            rids_b = [v.request_id for v in rb.visits if v.kind == VisitKind.DELIVERY and v.request_id]

            best_gain: int = 0
            best_ra_id: str | None = None
            best_rb_id: str | None = None
            best_a_pu: int | None = None
            best_a_de: int | None = None
            best_b_pu: int | None = None
            best_b_de: int | None = None

            cost_a = ra.cost.total_generalized_cost
            cost_b = rb.cost.total_generalized_cost

            for rid_a in rids_a:
                req_a = problem.request(rid_a)
                ra_without = [v for v in ra.visits if v.request_id != rid_a]
                trial_ra = type(ra)(vehicle=ra.vehicle, visits=ra_without, total_distance_m=0, total_duration_s=0)
                evaluate_route(trial_ra, problem)

                for rid_b in rids_b:
                    req_b = problem.request(rid_b)
                    rb_without = [v for v in rb.visits if v.request_id != rid_b]
                    trial_rb = type(rb)(vehicle=rb.vehicle, visits=rb_without, total_distance_m=0, total_duration_s=0)
                    evaluate_route(trial_rb, problem)

                    # Try inserting b into ra_without, and a into rb_without
                    for pu_b, de_b in feasible_insertion_positions(trial_ra, req_b, problem):
                        trial_ra2 = copy.deepcopy(trial_ra)
                        _apply_insertion(trial_ra2, req_b, pu_b, de_b, problem)
                        cost_ra2 = trial_ra2.cost.total_generalized_cost
                        break
                    else:
                        continue

                    for pu_a, de_a in feasible_insertion_positions(trial_rb, req_a, problem):
                        trial_rb2 = copy.deepcopy(trial_rb)
                        _apply_insertion(trial_rb2, req_a, pu_a, de_a, problem)
                        cost_rb2 = trial_rb2.cost.total_generalized_cost
                        break
                    else:
                        continue

                    gain = (cost_a + cost_b) - (cost_ra2 + cost_rb2)
                    if gain > best_gain:
                        best_gain = gain
                        best_ra_id = rid_a
                        best_rb_id = rid_b
                        best_a_pu, best_a_de = pu_a, de_a
                        best_b_pu, best_b_de = pu_b, de_b

            if best_gain > 0 and best_ra_id and best_rb_id:
                req_a = problem.request(best_ra_id)
                req_b = problem.request(best_rb_id)
                # Remove from ra and rb
                ra.visits = [v for v in ra.visits if v.request_id != best_ra_id]
                rb.visits = [v for v in rb.visits if v.request_id != best_rb_id]
                # Insert b into ra, a into rb
                _apply_insertion(ra, req_b, best_b_pu, best_b_de, problem)
                _apply_insertion(rb, req_a, best_a_pu, best_a_de, problem)
                applied += 1

    return applied
