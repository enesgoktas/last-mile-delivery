"""Unit tests for M3 local search move modules."""
from __future__ import annotations

import pytest

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import VisitKind
from app.algorithms.rich_vrp.local_search.candidate_lists import build_candidate_lists
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator
from app.algorithms.rich_vrp.local_search.moves import (
    compat_repair,
    cross_exchange,
    exchange,
    optional_drop,
    relocate,
    relocate_pair,
    two_opt,
    two_opt_star,
)


@pytest.fixture(scope="module")
def setup():
    vrp = load_synthetic("synthetic_30")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=12)
    candidates = build_candidate_lists(problem, k=12)
    evaluator = DeltaEvaluator(problem)
    return problem, solution, candidates, evaluator


def _make_state(problem, solution):
    import copy
    sol_copy = copy.deepcopy(solution)
    return ALNSState(solution=sol_copy, problem=problem)


def _no_duplicates(solution) -> bool:
    delivery_ids = [
        v.request_id for r in solution.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    ]
    return len(delivery_ids) == len(set(delivery_ids))


def _precedence_valid(solution) -> bool:
    for route in solution.routes:
        seen = set()
        for v in route.visits:
            if v.kind == VisitKind.PICKUP and v.request_id:
                seen.add(v.request_id)
            if v.kind == VisitKind.DELIVERY and v.request_id and v.request_id not in seen:
                return False
    return True


# --- relocate ---

def test_relocate_no_duplicates(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    relocate.search(state, problem, candidates, evaluator, max_moves=5)
    assert _no_duplicates(state.solution)


def test_relocate_precedence(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    relocate.search(state, problem, candidates, evaluator, max_moves=5)
    assert _precedence_valid(state.solution)


def test_relocate_returns_int(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    n = relocate.search(state, problem, candidates, evaluator, max_moves=3)
    assert isinstance(n, int) and n >= 0


# --- two_opt ---

def test_two_opt_no_duplicates(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    two_opt.search(state, problem, candidates, evaluator)
    assert _no_duplicates(state.solution)


def test_two_opt_precedence(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    two_opt.search(state, problem, candidates, evaluator)
    assert _precedence_valid(state.solution)


# --- relocate_pair ---

def test_relocate_pair_no_duplicates(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    relocate_pair.search(state, problem, candidates, evaluator, max_moves=3)
    assert _no_duplicates(state.solution)


def test_relocate_pair_precedence(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    relocate_pair.search(state, problem, candidates, evaluator, max_moves=3)
    assert _precedence_valid(state.solution)


# --- two_opt_star ---

def test_two_opt_star_no_duplicates(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    two_opt_star.search(state, problem, candidates, evaluator, max_moves=3)
    assert _no_duplicates(state.solution)


def test_two_opt_star_precedence(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    two_opt_star.search(state, problem, candidates, evaluator, max_moves=3)
    assert _precedence_valid(state.solution)


# --- exchange ---

def test_exchange_no_duplicates(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    exchange.search(state, problem, candidates, evaluator, max_moves=2)
    assert _no_duplicates(state.solution)


def test_exchange_precedence(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    exchange.search(state, problem, candidates, evaluator, max_moves=2)
    assert _precedence_valid(state.solution)


# --- cross_exchange ---

def test_cross_exchange_no_duplicates(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    cross_exchange.search(state, problem, candidates, evaluator, max_moves=2)
    assert _no_duplicates(state.solution)


def test_cross_exchange_precedence(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    cross_exchange.search(state, problem, candidates, evaluator, max_moves=2)
    assert _precedence_valid(state.solution)


# --- optional_drop ---

def test_optional_drop_returns_int(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    n = optional_drop.search(state, problem, candidates, evaluator)
    assert isinstance(n, int) and n >= 0


def test_optional_drop_no_duplicates(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    optional_drop.search(state, problem, candidates, evaluator, max_moves=5)
    assert _no_duplicates(state.solution)


# --- compat_repair ---

def test_compat_repair_returns_int(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    n = compat_repair.search(state, problem, candidates, evaluator)
    assert isinstance(n, int) and n >= 0


def test_compat_repair_no_duplicates(setup):
    problem, solution, candidates, evaluator = setup
    state = _make_state(problem, solution)
    compat_repair.search(state, problem, candidates, evaluator, max_moves=3)
    assert _no_duplicates(state.solution)
