"""Tests for the greedy paired-insertion seeder (M1.5)."""
from __future__ import annotations

from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import Feasibility, evaluate_route


def test_greedy_seed_small_instance():
    vrp = load_synthetic("synthetic_30")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=24)

    total_requests = len(problem.requests)
    served = sum(
        1
        for route in solution.routes
        for v in route.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    )
    unserved = len(solution.unserved)

    # Greedy should serve at least 80% of orders
    assert served + unserved == total_requests
    assert served >= int(0.80 * total_requests), (
        f"Greedy served only {served}/{total_requests} ({100*served/total_requests:.0f}%)"
    )


def test_greedy_seed_no_duplicates():
    vrp = load_synthetic("synthetic_30")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=24)

    served_ids: list[str] = []
    for route in solution.routes:
        for visit in route.visits:
            if visit.kind == VisitKind.DELIVERY and visit.request_id:
                served_ids.append(visit.request_id)

    assert len(served_ids) == len(set(served_ids)), "Duplicate deliveries found"


def test_greedy_seed_pickup_before_delivery():
    vrp = load_synthetic("synthetic_30")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=24)

    for route in solution.routes:
        seen_pickups: set[str] = set()
        for visit in route.visits:
            if visit.kind == VisitKind.PICKUP and visit.request_id:
                seen_pickups.add(visit.request_id)
            if visit.kind == VisitKind.DELIVERY and visit.request_id:
                assert visit.request_id in seen_pickups, (
                    f"Delivery {visit.request_id} before its pickup"
                )


def test_greedy_seed_served_routes_are_not_hard_infeasible():
    vrp = load_synthetic("synthetic_30")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=24)

    hard_infeasible_count = 0
    for route in solution.routes:
        has_visits = any(
            v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)
            for v in route.visits
        )
        if not has_visits:
            continue
        eval_result = evaluate_route(route, problem)
        if eval_result.feasibility == Feasibility.HARD_INFEASIBLE:
            hard_infeasible_count += 1

    assert hard_infeasible_count == 0, f"{hard_infeasible_count} routes are hard infeasible"


def test_greedy_seed_unserved_plus_served_equals_total():
    vrp = load_synthetic("synthetic_60")
    problem = build_problem(vrp)
    solution = seed_greedy(problem)

    served = {
        v.request_id
        for route in solution.routes
        for v in route.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    total = {req.id for req in problem.requests}
    assert served | solution.unserved == total
    assert served & solution.unserved == set()
