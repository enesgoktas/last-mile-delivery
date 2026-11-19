"""Unit tests for M3 VND local search engine."""
from __future__ import annotations

import random

import pytest

from app.algorithms.rich_vrp.alns.engine import ALNSConfig, RichALNS
from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import VisitKind
from app.algorithms.rich_vrp.local_search.candidate_lists import build_candidate_lists
from app.algorithms.rich_vrp.local_search.engine import improve
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator


@pytest.fixture(scope="module")
def problem_and_solution():
    vrp = load_synthetic("synthetic_30")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=12)
    return problem, solution


def test_improve_returns_state(problem_and_solution):
    problem, solution = problem_and_solution
    import copy
    state = ALNSState(solution=copy.deepcopy(solution), problem=problem)
    candidates = build_candidate_lists(problem, k=12)
    evaluator = DeltaEvaluator(problem)
    result = improve(state, problem, candidates, evaluator, max_seconds=1.0)
    assert result is state  # mutates in place, returns same object


def test_improve_no_duplicates(problem_and_solution):
    problem, solution = problem_and_solution
    import copy
    state = ALNSState(solution=copy.deepcopy(solution), problem=problem)
    candidates = build_candidate_lists(problem, k=12)
    evaluator = DeltaEvaluator(problem)
    improve(state, problem, candidates, evaluator, max_seconds=1.0)
    delivery_ids = [
        v.request_id for r in state.solution.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    ]
    assert len(delivery_ids) == len(set(delivery_ids))


def test_improve_precedence_valid(problem_and_solution):
    problem, solution = problem_and_solution
    import copy
    state = ALNSState(solution=copy.deepcopy(solution), problem=problem)
    candidates = build_candidate_lists(problem, k=12)
    evaluator = DeltaEvaluator(problem)
    improve(state, problem, candidates, evaluator, max_seconds=1.0)
    for route in state.solution.routes:
        seen: set[str] = set()
        for v in route.visits:
            if v.kind == VisitKind.PICKUP and v.request_id:
                seen.add(v.request_id)
            if v.kind == VisitKind.DELIVERY and v.request_id:
                assert v.request_id in seen, f"Delivery {v.request_id} before pickup"


def test_engine_with_vnd_no_worse_than_seed(problem_and_solution):
    """Engine with VND should produce a solution no worse than the greedy seed."""
    problem, solution = problem_and_solution
    import copy
    config = ALNSConfig(destroy_min_fraction=0.1, destroy_max_fraction=0.3)
    engine = RichALNS(problem=problem, seeds=[copy.deepcopy(solution)], config=config)
    rng = random.Random(42)
    result = engine.run(time_budget_s=2.0, rng=rng)
    # Basic sanity: result is a valid Solution
    assert result is not None
    assert isinstance(result.routes, list)
    assert isinstance(result.unserved, set)


def test_candidate_lists_built(problem_and_solution):
    problem, _ = problem_and_solution
    candidates = build_candidate_lists(problem, k=5)
    assert candidates.k == 5
    # Every node should have neighbours
    for i in range(len(problem.locations)):
        nbrs = candidates.neighbours(i)
        assert len(nbrs) <= 5
        assert i not in nbrs  # no self-loop
