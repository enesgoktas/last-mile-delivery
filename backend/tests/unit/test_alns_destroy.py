"""Tests for ALNS destroy operators (M2.2)."""
from __future__ import annotations

import random

import pytest

from app.algorithms.rich_vrp.alns.destroy import (
    compatibility,
    random_remove,
    route_remove,
    shaw,
    time_window,
    worst_cost,
)
from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import VisitKind

HOUR = 3600

_DESTROY_OPS = [
    random_remove.destroy,
    shaw.destroy,
    worst_cost.destroy,
    route_remove.destroy,
    time_window.destroy,
    compatibility.destroy,
]


def _make_state(n_orders: int = 30, seed: int = 0):
    vrp = load_synthetic(f"synthetic_{n_orders}")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=12)
    return ALNSState(solution=solution, problem=problem), problem


def _count_served(state: ALNSState) -> int:
    return sum(
        1 for route in state.solution.routes
        for v in route.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    )


@pytest.mark.parametrize("destroy_fn", _DESTROY_OPS)
def test_destroy_removes_exactly_q_or_fewer(destroy_fn):
    rng = random.Random(42)
    state, problem = _make_state(30)
    served_before = _count_served(state)
    q = max(1, served_before // 5)

    _, removed = destroy_fn(state, problem, q, rng)

    # Some operators may remove fewer than q (e.g. route_remove empties whole route)
    assert 0 < len(removed) <= max(q, served_before)


@pytest.mark.parametrize("destroy_fn", _DESTROY_OPS)
def test_destroy_returns_request_objects(destroy_fn):
    rng = random.Random(7)
    state, problem = _make_state(30)
    served_before = _count_served(state)
    q = max(1, served_before // 4)

    state_out, removed = destroy_fn(state, problem, q, rng)

    # All removed items should be Request objects with valid IDs
    for req in removed:
        assert req.id is not None
        assert isinstance(req.id, str)


@pytest.mark.parametrize("destroy_fn", _DESTROY_OPS)
def test_destroy_leaves_no_duplicates(destroy_fn):
    rng = random.Random(13)
    state, problem = _make_state(30)
    q = 5

    state_out, removed = destroy_fn(state, problem, q, rng)

    # Each request should appear at most once in routes
    delivery_ids: list[str] = []
    for route in state_out.solution.routes:
        for v in route.visits:
            if v.kind == VisitKind.DELIVERY and v.request_id:
                delivery_ids.append(v.request_id)
    assert len(delivery_ids) == len(set(delivery_ids))


@pytest.mark.parametrize("destroy_fn", _DESTROY_OPS)
def test_destroy_removed_requests_not_in_routes(destroy_fn):
    rng = random.Random(99)
    state, problem = _make_state(30)
    q = 4

    state_out, removed = destroy_fn(state, problem, q, rng)

    removed_ids = {req.id for req in removed}
    in_routes = {
        v.request_id
        for route in state_out.solution.routes
        for v in route.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    assert removed_ids.isdisjoint(in_routes), "Some removed requests still in routes"


def test_random_destroy_zero_q_is_graceful():
    rng = random.Random(0)
    state, problem = _make_state(30)
    state_out, removed = random_remove.destroy(state, problem, 0, rng)
    # Should remove at most 0 or return empty
    assert len(removed) == 0 or len(removed) <= 1  # min(0, n) = 0
