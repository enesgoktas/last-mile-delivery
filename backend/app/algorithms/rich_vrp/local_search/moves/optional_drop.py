"""Optional-drop move: remove optional requests when dropping improves the objective.

An optional request has `request.optional = True`.  If its unserved_penalty is
less than the marginal routing cost of serving it, dropping it improves the
lexicographic objective.
"""
from __future__ import annotations

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import Problem, VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import evaluate_route
from app.algorithms.rich_vrp.local_search.candidate_lists import CandidateLists
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator


def search(
    state: ALNSState,
    problem: Problem,
    candidates: CandidateLists,
    evaluator: DeltaEvaluator,
    max_moves: int = 1,
) -> int:
    """Drop optional requests whose removal improves generalized cost. Return moves applied."""
    applied = 0
    for route in state.solution.routes:
        if applied >= max_moves:
            break
        optional_rids = [
            v.request_id for v in route.visits
            if v.kind == VisitKind.DELIVERY and v.request_id
            and _is_optional(v.request_id, problem)
        ]
        for rid in optional_rids:
            if applied >= max_moves:
                break
            try:
                req = problem.request(rid)
            except KeyError:
                continue

            route_cost_before = route.cost.total_generalized_cost
            trial_visits = [v for v in route.visits if v.request_id != rid]
            trial = type(route)(vehicle=route.vehicle, visits=trial_visits, total_distance_m=0, total_duration_s=0)
            evaluate_route(trial, problem)
            route_saving = route_cost_before - trial.cost.total_generalized_cost

            if route_saving > req.unserved_penalty:
                route.visits = trial_visits
                evaluate_route(route, problem)
                state.solution.unserved.add(rid)
                applied += 1

    return applied


def _is_optional(request_id: str, problem: Problem) -> bool:
    try:
        return problem.request(request_id).optional
    except KeyError:
        return False
