"""Tests for ALNS repair operators (M2.3)."""
from __future__ import annotations

import random

import pytest

from app.algorithms.rich_vrp.alns.destroy import random_remove
from app.algorithms.rich_vrp.alns.repair import (
    compatibility_first,
    cost_normalized,
    greedy_paired,
    regret_k,
)
from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import Feasibility, evaluate_route

_REPAIR_OPS = [
    greedy_paired.repair,
    regret_k.repair,
    cost_normalized.repair,
    compatibility_first.repair,
]


def _make_state(n_orders: int = 30, seed: int = 0):
    vrp = load_synthetic(f"synthetic_{n_orders}")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=12)
    return ALNSState(solution=solution, problem=problem), problem


@pytest.mark.parametrize("repair_fn", _REPAIR_OPS)
def test_repair_no_duplicates(repair_fn):
    rng = random.Random(42)
    state, problem = _make_state(30)
    q = 5
    state, removed = random_remove.destroy(state, problem, q, rng)
    state = repair_fn(state, problem, removed, rng)

    delivery_ids: list[str] = []
    for route in state.solution.routes:
        for v in route.visits:
            if v.kind == VisitKind.DELIVERY and v.request_id:
                delivery_ids.append(v.request_id)
    assert len(delivery_ids) == len(set(delivery_ids)), "Duplicate deliveries after repair"


@pytest.mark.parametrize("repair_fn", _REPAIR_OPS)
def test_repair_pickup_before_delivery(repair_fn):
    rng = random.Random(7)
    state, problem = _make_state(30)
    q = 5
    state, removed = random_remove.destroy(state, problem, q, rng)
    state = repair_fn(state, problem, removed, rng)

    for route in state.solution.routes:
        seen: set[str] = set()
        for v in route.visits:
            if v.kind == VisitKind.PICKUP and v.request_id:
                seen.add(v.request_id)
            if v.kind == VisitKind.DELIVERY and v.request_id:
                assert v.request_id in seen, f"Delivery {v.request_id} before pickup"


@pytest.mark.parametrize("repair_fn", _REPAIR_OPS)
def test_repair_no_hard_infeasible_routes(repair_fn):
    rng = random.Random(13)
    state, problem = _make_state(30)
    q = 5
    state, removed = random_remove.destroy(state, problem, q, rng)
    state = repair_fn(state, problem, removed, rng)

    for route in state.solution.routes:
        has_visits = any(
            v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)
            for v in route.visits
        )
        if not has_visits:
            continue
        result = evaluate_route(route, problem)
        assert result.feasibility != Feasibility.HARD_INFEASIBLE, (
            f"Hard infeasible route after repair: {result.hard_violations}"
        )


@pytest.mark.parametrize("repair_fn", _REPAIR_OPS)
def test_repair_total_requests_preserved(repair_fn):
    rng = random.Random(17)
    state, problem = _make_state(30)
    total_before = len(problem.requests)
    q = 4
    state, removed = random_remove.destroy(state, problem, q, rng)
    state = repair_fn(state, problem, removed, rng)

    served = {
        v.request_id
        for route in state.solution.routes
        for v in route.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    total_after = len(served) + len(state.solution.unserved)
    assert total_after == total_before, (
        f"Requests disappeared: before={total_before}, after={total_after}"
    )
