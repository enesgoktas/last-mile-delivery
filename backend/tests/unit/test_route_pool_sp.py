"""Unit tests for M5 route-pool SP recombination and CP-SAT micro-repair."""
from __future__ import annotations

import copy
import random

import pytest

from app.algorithms.rich_vrp.alns.engine import ALNSConfig, RichALNS
from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import VisitKind
from app.algorithms.rich_vrp.recombination.cp_sat_repair import cp_sat_repair
from app.algorithms.rich_vrp.recombination.route_pool import PooledRoute, RoutePool
from app.algorithms.rich_vrp.recombination.sp_repair import sp_repair


@pytest.fixture(scope="module")
def problem_and_solution():
    vrp = load_synthetic("synthetic_30")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=12)
    return problem, solution


# ── RoutePool ────────────────────────────────────────────────────────────────

def test_route_pool_add_nonempty(problem_and_solution):
    _, solution = problem_and_solution
    pool = RoutePool(cap=100)
    for route in solution.routes:
        pool.add(route)
    assert len(pool) > 0


def test_route_pool_deduplication(problem_and_solution):
    _, solution = problem_and_solution
    pool = RoutePool(cap=100)
    for route in solution.routes:
        pool.add(route)
    size_after_first = len(pool)
    # Adding same routes again should not grow the pool
    for route in solution.routes:
        pool.add(route)
    assert len(pool) == size_after_first


def test_route_pool_cap_respected(problem_and_solution):
    problem, solution = problem_and_solution
    pool = RoutePool(cap=3)
    for route in solution.routes:
        pool.add(route)
    assert len(pool) <= 3


def test_route_pool_cheaper_replaces(problem_and_solution):
    _, solution = problem_and_solution
    pool = RoutePool(cap=100)
    for route in solution.routes:
        pool.add(route)
    initial_size = len(pool)
    # Modify a route's cost to be cheaper and re-add
    route = solution.routes[0]
    route_copy = copy.deepcopy(route)
    route_copy.cost.total_generalized_cost = 0
    pool.add(route_copy)
    # Pool size should not grow (same key, just cheaper)
    assert len(pool) == initial_size


def test_route_pool_sample_for_sp(problem_and_solution):
    problem, solution = problem_and_solution
    pool = RoutePool(cap=1000)
    for route in solution.routes:
        pool.add(route)
    sample = pool.sample_for_sp(max_routes=2)
    assert len(sample) <= 2
    assert all(isinstance(r, PooledRoute) for r in sample)


def test_route_pool_sample_all_when_small(problem_and_solution):
    _, solution = problem_and_solution
    pool = RoutePool(cap=100)
    for route in solution.routes:
        pool.add(route)
    sample = pool.sample_for_sp(max_routes=10000)
    assert len(sample) == len(pool)


# ── SP repair ─────────────────────────────────────────────────────────────────

def test_sp_repair_returns_solution_or_none(problem_and_solution):
    problem, solution = problem_and_solution
    pool = RoutePool(cap=500)
    for route in solution.routes:
        pool.add(route)
    result = sp_repair(pool, problem, time_limit_s=5.0, max_routes=50)
    # Result is either None (if MIP infeasible within budget) or a valid Solution
    if result is not None:
        assert isinstance(result.routes, list)
        assert isinstance(result.unserved, set)


def test_sp_repair_no_duplicate_requests(problem_and_solution):
    problem, solution = problem_and_solution
    pool = RoutePool(cap=500)
    for route in solution.routes:
        pool.add(route)
    result = sp_repair(pool, problem, time_limit_s=5.0, max_routes=50)
    if result is None:
        pytest.skip("SP found no feasible solution within budget")
    delivery_ids = [
        v.request_id
        for r in result.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    ]
    assert len(delivery_ids) == len(set(delivery_ids))


def test_sp_repair_covers_all_requests(problem_and_solution):
    problem, solution = problem_and_solution
    pool = RoutePool(cap=500)
    for route in solution.routes:
        pool.add(route)
    result = sp_repair(pool, problem, time_limit_s=5.0, max_routes=50)
    if result is None:
        pytest.skip("SP found no feasible solution within budget")
    served = {
        v.request_id
        for r in result.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    assert len(served) + len(result.unserved) == len(problem.requests)


def test_sp_repair_empty_pool_returns_none(problem_and_solution):
    problem, _ = problem_and_solution
    pool = RoutePool(cap=100)
    result = sp_repair(pool, problem, time_limit_s=5.0)
    assert result is None


# ── CP-SAT micro-repair ───────────────────────────────────────────────────────

def test_cp_sat_repair_no_unserved_passthrough(problem_and_solution):
    problem, solution = problem_and_solution
    # Ensure no unserved for this test
    import copy as _copy
    sol = _copy.deepcopy(solution)
    sol.unserved = set()
    result = cp_sat_repair(sol, problem, time_limit_s=5.0)
    assert result is sol  # unchanged


def test_cp_sat_repair_reduces_or_preserves_unserved(problem_and_solution):
    problem, solution = problem_and_solution
    # Seed with some unserved by using a fresh solution
    other = seed_greedy(problem, candidate_size=3)
    if not other.unserved:
        pytest.skip("greedy produced no unserved requests")
    before = len(other.unserved)
    result = cp_sat_repair(other, problem, time_limit_s=5.0, max_requests=20)
    assert len(result.unserved) <= before


def test_cp_sat_repair_no_duplicates(problem_and_solution):
    problem, solution = problem_and_solution
    other = seed_greedy(problem, candidate_size=3)
    result = cp_sat_repair(other, problem, time_limit_s=5.0, max_requests=20)
    delivery_ids = [
        v.request_id
        for r in result.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    ]
    assert len(delivery_ids) == len(set(delivery_ids))


# ── Engine integration ────────────────────────────────────────────────────────

def test_engine_m5_metrics_present(problem_and_solution):
    problem, solution = problem_and_solution
    config = ALNSConfig(
        destroy_min_fraction=0.1,
        destroy_max_fraction=0.3,
        crossover_interval=50,
        sp_interval=200,
        stagnation_threshold=100,
        sp_time_limit_s=2.0,
        cp_sat_time_limit_s=2.0,
    )
    engine = RichALNS(problem=problem, seeds=[copy.deepcopy(solution)], config=config)
    engine.run(time_budget_s=3.0, rng=random.Random(0))
    assert "sp_count" in engine.metrics
    assert "cp_sat_count" in engine.metrics
    assert "route_pool_size" in engine.metrics
    assert engine.metrics["route_pool_size"] >= 0


def test_engine_m5_valid_solution(problem_and_solution):
    problem, solution = problem_and_solution
    config = ALNSConfig(
        destroy_min_fraction=0.1,
        destroy_max_fraction=0.3,
        crossover_interval=100,
        sp_interval=300,
        stagnation_threshold=200,
    )
    engine = RichALNS(problem=problem, seeds=[copy.deepcopy(solution)], config=config)
    result = engine.run(time_budget_s=3.0, rng=random.Random(5))
    assert isinstance(result.routes, list)
    assert isinstance(result.unserved, set)
    delivery_ids = [
        v.request_id
        for r in result.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    ]
    assert len(delivery_ids) == len(set(delivery_ids))
