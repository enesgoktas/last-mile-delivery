"""Cross-exchange: swap contiguous segments between two routes.

Each segment is a contiguous block of inner visits (excluding depots).
Segments of length 1-2 are tried. Pickup-delivery precedence is enforced.
"""
from __future__ import annotations

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import Problem, VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import Feasibility, evaluate_route
from app.algorithms.rich_vrp.local_search.candidate_lists import CandidateLists
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator

_MAX_SEG = 2  # max segment length to try


def search(
    state: ALNSState,
    problem: Problem,
    candidates: CandidateLists,
    evaluator: DeltaEvaluator,
    max_moves: int = 1,
) -> int:
    """Try cross-segment exchange between route pairs. Return moves applied."""
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

            depot_sa = [v for v in ra.visits if v.kind == VisitKind.DEPOT_START]
            depot_ea = [v for v in ra.visits if v.kind == VisitKind.DEPOT_END]
            depot_sb = [v for v in rb.visits if v.kind == VisitKind.DEPOT_START]
            depot_eb = [v for v in rb.visits if v.kind == VisitKind.DEPOT_END]

            cost_ab = ra.cost.total_generalized_cost + rb.cost.total_generalized_cost
            found = False

            for sa in range(1, _MAX_SEG + 1):
                if found:
                    break
                for ia in range(len(inner_a) - sa + 1):
                    if found:
                        break
                    seg_a = inner_a[ia:ia + sa]
                    for sb in range(1, _MAX_SEG + 1):
                        if found:
                            break
                        for ib in range(len(inner_b) - sb + 1):
                            seg_b = inner_b[ib:ib + sb]
                            new_inner_a = inner_a[:ia] + seg_b + inner_a[ia + sa:]
                            new_inner_b = inner_b[:ib] + seg_a + inner_b[ib + sb:]

                            if not _prec_ok(new_inner_a) or not _prec_ok(new_inner_b):
                                continue

                            va = depot_sa + new_inner_a + depot_ea
                            vb = depot_sb + new_inner_b + depot_eb

                            ta = type(ra)(vehicle=ra.vehicle, visits=va, total_distance_m=0, total_duration_s=0)
                            tb = type(rb)(vehicle=rb.vehicle, visits=vb, total_distance_m=0, total_duration_s=0)
                            eva = evaluate_route(ta, problem)
                            evb = evaluate_route(tb, problem)

                            if eva.feasibility == Feasibility.HARD_INFEASIBLE or evb.feasibility == Feasibility.HARD_INFEASIBLE:
                                continue

                            new_cost = ta.cost.total_generalized_cost + tb.cost.total_generalized_cost
                            if new_cost < cost_ab:
                                ra.visits = va
                                evaluate_route(ra, problem)
                                rb.visits = vb
                                evaluate_route(rb, problem)
                                applied += 1
                                found = True
                                break

    return applied


def _prec_ok(visits: list) -> bool:
    seen: set[str] = set()
    for v in visits:
        if v.kind == VisitKind.PICKUP and v.request_id:
            seen.add(v.request_id)
        if v.kind == VisitKind.DELIVERY and v.request_id and v.request_id not in seen:
            return False
    return True
