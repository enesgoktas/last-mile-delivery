"""Intra-route single-stop relocation move.

Tries to move each non-depot visit to a cheaper position within the same route.
Only moves that reduce generalized cost are applied (first-improving).
"""
from __future__ import annotations

import copy

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
    """Try intra-route single-stop relocation. Return number of moves applied."""
    applied = 0
    for route in state.solution.routes:
        inner = [
            (i, v) for i, v in enumerate(route.visits)
            if v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)
        ]
        if len(inner) < 2:
            continue

        current_cost = route.cost.total_generalized_cost
        improved = True
        while improved and applied < max_moves:
            improved = False
            for pos, visit in inner:
                for new_pos in range(1, len(route.visits)):
                    if new_pos == pos or new_pos == pos + 1:
                        continue
                    trial_visits = list(route.visits)
                    trial_visits.pop(pos)
                    insert_pos = new_pos if new_pos < pos else new_pos - 1
                    trial_visits.insert(insert_pos, copy.copy(visit))

                    trial = type(route)(
                        vehicle=route.vehicle,
                        visits=trial_visits,
                        total_distance_m=0,
                        total_duration_s=0,
                    )
                    ev = evaluate_route(trial, problem)
                    if ev.feasibility == Feasibility.HARD_INFEASIBLE:
                        continue
                    new_cost = trial.cost.total_generalized_cost
                    if new_cost < current_cost:
                        route.visits = trial_visits
                        evaluate_route(route, problem)
                        current_cost = new_cost
                        inner = [
                            (i, v) for i, v in enumerate(route.visits)
                            if v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)
                        ]
                        improved = True
                        applied += 1
                        break
                if improved:
                    break
    return applied
