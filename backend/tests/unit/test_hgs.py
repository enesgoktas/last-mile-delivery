"""Unit tests for M4 HGS hybridisation — diversity, population, crossover, penalty."""
from __future__ import annotations

import copy
import random

import pytest

from app.algorithms.rich_vrp.alns.engine import ALNSConfig, RichALNS
from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import VisitKind
from app.algorithms.rich_vrp.hgs.crossover import _delivery_order, srex
from app.algorithms.rich_vrp.hgs.diversity import (
    adjacency_set,
    broken_pairs_distance,
    diversity_contribution,
)
from app.algorithms.rich_vrp.hgs.penalty import AdaptivePenalty
from app.algorithms.rich_vrp.hgs.population import ElitePool, HGSPopulation, Individual
from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution


@pytest.fixture(scope="module")
def problem_and_solution():
    vrp = load_synthetic("synthetic_30")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=12)
    return problem, solution


# ── diversity ────────────────────────────────────────────────────────────────

def test_adjacency_set_returns_frozenset(problem_and_solution):
    _, solution = problem_and_solution
    adj = adjacency_set(solution)
    assert isinstance(adj, frozenset)


def test_broken_pairs_distance_self_zero(problem_and_solution):
    _, solution = problem_and_solution
    dist = broken_pairs_distance(solution, solution)
    assert dist == pytest.approx(0.0)


def test_broken_pairs_distance_range(problem_and_solution):
    problem, solution = problem_and_solution
    other = seed_greedy(problem, candidate_size=6)
    dist = broken_pairs_distance(solution, other)
    assert 0.0 <= dist <= 1.0


def test_diversity_contribution_empty_pool(problem_and_solution):
    _, solution = problem_and_solution
    dc = diversity_contribution(solution, [])
    assert dc == pytest.approx(1.0)


def test_diversity_contribution_multiple(problem_and_solution):
    problem, solution = problem_and_solution
    others = [seed_greedy(problem, candidate_size=6) for _ in range(3)]
    dc = diversity_contribution(solution, others)
    assert 0.0 <= dc <= 1.0


# ── population ───────────────────────────────────────────────────────────────

def _make_individual(problem, solution, feasible=True):
    sol = copy.deepcopy(solution)
    obj = objective_for_solution(sol.routes, sol.unserved, problem)
    return Individual(solution=sol, objective=obj, is_feasible=feasible)


def test_elite_pool_add_and_len(problem_and_solution):
    problem, solution = problem_and_solution
    pool = ElitePool(max_size=5)
    for _ in range(3):
        pool.add(_make_individual(problem, solution))
    assert len(pool) == 3


def test_elite_pool_prunes_to_max(problem_and_solution):
    problem, solution = problem_and_solution
    pool = ElitePool(max_size=3)
    for _ in range(6):
        pool.add(_make_individual(problem, solution))
    assert len(pool) <= 3


def test_elite_pool_best_not_none(problem_and_solution):
    problem, solution = problem_and_solution
    pool = ElitePool(max_size=5)
    pool.add(_make_individual(problem, solution))
    assert pool.best() is not None


def test_hgs_population_feas_infeas(problem_and_solution):
    problem, solution = problem_and_solution
    pop = HGSPopulation(pool_size=5)
    pop.add(_make_individual(problem, solution, feasible=True))
    pop.add(_make_individual(problem, solution, feasible=False))
    assert len(pop.feas) == 1
    assert len(pop.infeas) == 1


def test_hgs_population_best_prefers_feas(problem_and_solution):
    problem, solution = problem_and_solution
    pop = HGSPopulation(pool_size=5)
    pop.add(_make_individual(problem, solution, feasible=True))
    pop.add(_make_individual(problem, solution, feasible=False))
    assert pop.best() is not None
    assert pop.best().is_feasible


def test_hgs_population_sample_parents(problem_and_solution):
    problem, solution = problem_and_solution
    pop = HGSPopulation(pool_size=10)
    for _ in range(4):
        pop.add(_make_individual(problem, solution, feasible=True))
    rng = random.Random(0)
    p1, p2 = pop.sample_parents(rng)
    assert p1 is not None
    assert p2 is not None


# ── crossover ────────────────────────────────────────────────────────────────

def test_srex_returns_valid_solution(problem_and_solution):
    problem, solution = problem_and_solution
    rng = random.Random(42)
    offspring = srex(solution, solution, problem, rng)
    assert offspring is not None
    assert isinstance(offspring.routes, list)
    assert isinstance(offspring.unserved, set)


def test_srex_no_duplicates(problem_and_solution):
    problem, solution = problem_and_solution
    rng = random.Random(42)
    offspring = srex(solution, solution, problem, rng)
    delivery_ids = [
        v.request_id
        for r in offspring.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    ]
    assert len(delivery_ids) == len(set(delivery_ids))


def test_srex_conserves_requests(problem_and_solution):
    problem, solution = problem_and_solution
    rng = random.Random(7)
    total_requests = len(problem.requests)
    offspring = srex(solution, solution, problem, rng)
    served = {
        v.request_id
        for r in offspring.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    assert len(served) + len(offspring.unserved) == total_requests


def test_srex_pickup_before_delivery(problem_and_solution):
    problem, solution = problem_and_solution
    rng = random.Random(99)
    offspring = srex(solution, solution, problem, rng)
    for route in offspring.routes:
        seen: set[str] = set()
        for v in route.visits:
            if v.kind == VisitKind.PICKUP and v.request_id:
                seen.add(v.request_id)
            if v.kind == VisitKind.DELIVERY and v.request_id:
                assert v.request_id in seen, f"{v.request_id} delivered before pickup"


def test_delivery_order_unique(problem_and_solution):
    _, solution = problem_and_solution
    order = _delivery_order(solution)
    assert len(order) == len(set(order))


# ── penalty ──────────────────────────────────────────────────────────────────

def test_penalty_initial_multiplier():
    pen = AdaptivePenalty(window=10)
    assert pen.multiplier == pytest.approx(1.0)


def test_penalty_adjust_up_when_too_feasible():
    pen = AdaptivePenalty(window=10)
    for _ in range(10):
        pen.record(True)  # 100% feasible — too high
    mult_before = pen.multiplier
    pen.adjust()
    assert pen.multiplier > mult_before


def test_penalty_adjust_down_when_too_infeasible():
    pen = AdaptivePenalty(window=10)
    for _ in range(10):
        pen.record(False)  # 0% feasible — too low
    mult_before = pen.multiplier
    pen.adjust()
    assert pen.multiplier < mult_before


def test_penalty_no_adjust_at_target():
    pen = AdaptivePenalty(window=20)
    # Record ~65% feasible
    for i in range(20):
        pen.record(i < 13)
    mult_before = pen.multiplier
    pen.adjust()
    assert pen.multiplier == pytest.approx(mult_before)


# ── engine integration ────────────────────────────────────────────────────────

def test_engine_m4_has_hgs_metrics(problem_and_solution):
    problem, solution = problem_and_solution
    config = ALNSConfig(
        destroy_min_fraction=0.1,
        destroy_max_fraction=0.3,
        crossover_interval=50,
        hgs_pool_size=5,
    )
    engine = RichALNS(problem=problem, seeds=[copy.deepcopy(solution)], config=config)
    engine.run(time_budget_s=2.0, rng=random.Random(0))
    assert "hgs_feas_pool_size" in engine.metrics
    assert "hgs_infeas_pool_size" in engine.metrics
    assert "crossover_count" in engine.metrics
    assert "feasibility_ratio" in engine.metrics


def test_engine_m4_valid_solution(problem_and_solution):
    problem, solution = problem_and_solution
    config = ALNSConfig(
        destroy_min_fraction=0.1,
        destroy_max_fraction=0.3,
        crossover_interval=100,
    )
    engine = RichALNS(problem=problem, seeds=[copy.deepcopy(solution)], config=config)
    result = engine.run(time_budget_s=2.0, rng=random.Random(1))
    assert isinstance(result.routes, list)
    assert isinstance(result.unserved, set)
    delivery_ids = [
        v.request_id
        for r in result.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    ]
    assert len(delivery_ids) == len(set(delivery_ids))
