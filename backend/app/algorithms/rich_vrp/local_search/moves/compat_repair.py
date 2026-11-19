"""Compatibility ejection-chain repair.

When a high-priority request is unserved due to compatibility scarcity,
eject a lower-priority compatible request to free capacity, insert the
high-priority request, then re-insert the ejected request elsewhere.
"""
from __future__ import annotations

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.constructive.greedy_seed import _apply_insertion
from app.algorithms.rich_vrp.domain.models import OrderPriority, Problem, VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import (
    evaluate_route,
    feasible_insertion_positions,
    is_compatible,
)
from app.algorithms.rich_vrp.local_search.candidate_lists import CandidateLists
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator

_PRIORITY_RANK = {
    OrderPriority.URGENT: 4,
    OrderPriority.HIGH: 3,
    OrderPriority.NORMAL: 2,
    OrderPriority.LOW: 1,
}


def search(
    state: ALNSState,
    problem: Problem,
    candidates: CandidateLists,
    evaluator: DeltaEvaluator,
    max_moves: int = 1,
) -> int:
    """Ejection-chain: try to eject a low-priority request to make room for a high-priority unserved one."""
    applied = 0
    if not state.solution.unserved or applied >= max_moves:
        return 0

    # Sort unserved by priority descending
    unserved_sorted = sorted(
        state.solution.unserved,
        key=lambda rid: _PRIORITY_RANK.get(_safe_priority(rid, problem), 0),
        reverse=True,
    )

    routes = state.solution.routes

    for target_rid in unserved_sorted:
        if applied >= max_moves:
            break
        try:
            target_req = problem.request(target_rid)
        except KeyError:
            continue

        target_rank = _PRIORITY_RANK.get(target_req.priority, 0)

        # Find compatible routes
        for route in routes:
            if not is_compatible(target_req, route.vehicle):
                continue

            # Find a lower-priority request we could eject
            candidates_to_eject = [
                v.request_id for v in route.visits
                if v.kind == VisitKind.DELIVERY and v.request_id
                and _PRIORITY_RANK.get(_safe_priority(v.request_id, problem), 0) < target_rank
            ]

            for eject_rid in candidates_to_eject:
                try:
                    eject_req = problem.request(eject_rid)
                except KeyError:
                    continue

                # Remove ejected request
                visits_without = [v for v in route.visits if v.request_id != eject_rid]
                trial_route_without = type(route)(vehicle=route.vehicle, visits=visits_without, total_distance_m=0, total_duration_s=0)
                evaluate_route(trial_route_without, problem)

                # Try inserting target into the freed route
                for pu, de in feasible_insertion_positions(trial_route_without, target_req, problem):
                    import copy
                    trial_with = copy.deepcopy(trial_route_without)
                    _apply_insertion(trial_with, target_req, pu, de, problem)

                    # Try re-inserting ejected request into any route
                    best_eject_route_idx: int | None = None
                    best_eject_pu: int | None = None
                    best_eject_de: int | None = None

                    for other_idx, other_route in enumerate(routes):
                        if other_route is route:
                            candidate_route = trial_with  # the modified route
                        else:
                            candidate_route = other_route
                        for e_pu, e_de in feasible_insertion_positions(candidate_route, eject_req, problem):
                            if best_eject_route_idx is None:
                                best_eject_route_idx = other_idx
                                best_eject_pu = e_pu
                                best_eject_de = e_de
                            break

                    if best_eject_route_idx is not None:
                        # Commit: update source route, insert target, insert ejected
                        route.visits = visits_without
                        _apply_insertion(route, target_req, pu, de, problem)
                        state.solution.unserved.discard(target_rid)

                        eject_dst = routes[best_eject_route_idx]
                        _apply_insertion(eject_dst, eject_req, best_eject_pu, best_eject_de, problem)
                        state.solution.unserved.discard(eject_rid)
                        applied += 1
                        break
                    break  # only try first feasible insertion of target

                if applied >= max_moves:
                    break

            if applied >= max_moves:
                break

    return applied


def _safe_priority(request_id: str, problem: Problem) -> OrderPriority | None:
    try:
        return problem.request(request_id).priority
    except KeyError:
        return None
