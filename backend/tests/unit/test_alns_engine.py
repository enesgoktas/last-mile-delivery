"""End-to-end tests for the ALNS engine (M2.6)."""
from __future__ import annotations

import random

from app.algorithms.rich_vrp.alns.engine import ALNSConfig, RichALNS
from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import VisitKind


def _run_engine(n_orders: int, time_budget: float = 5.0, seed: int = 0):
    vrp = load_synthetic(f"synthetic_{n_orders}")
    problem = build_problem(vrp)
    greedy = seed_greedy(problem, candidate_size=12)
    config = ALNSConfig(destroy_min_fraction=0.1, destroy_max_fraction=0.3)
    engine = RichALNS(problem=problem, seeds=[greedy], config=config)
    rng = random.Random(seed)
    solution = engine.run(time_budget_s=time_budget, rng=rng)
    return solution, engine


def test_engine_returns_valid_solution():
    solution, engine = _run_engine(30, time_budget=3.0)
    assert solution is not None
    assert isinstance(solution.unserved, set)
    assert isinstance(solution.routes, list)


def test_engine_deterministic_with_same_seed():
    sol1, _ = _run_engine(30, time_budget=2.0, seed=42)
    sol2, _ = _run_engine(30, time_budget=2.0, seed=42)
    served1 = sorted(
        v.request_id
        for r in sol1.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    )
    served2 = sorted(
        v.request_id
        for r in sol2.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    )
    assert served1 == served2, "Engine not deterministic with same seed"


def test_engine_no_duplicates():
    solution, _ = _run_engine(30, time_budget=2.0)
    delivery_ids: list[str] = []
    for route in solution.routes:
        for v in route.visits:
            if v.kind == VisitKind.DELIVERY and v.request_id:
                delivery_ids.append(v.request_id)
    assert len(delivery_ids) == len(set(delivery_ids))


def test_engine_total_requests_conserved():
    vrp = load_synthetic("synthetic_30")
    problem = build_problem(vrp)
    total_requests = len(problem.requests)

    greedy = seed_greedy(problem, candidate_size=12)
    engine = RichALNS(problem=problem, seeds=[greedy], config=ALNSConfig())
    solution = engine.run(time_budget_s=2.0, rng=random.Random(0))

    served = {
        v.request_id
        for r in solution.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    assert len(served) + len(solution.unserved) == total_requests


def test_engine_metrics_populated():
    _, engine = _run_engine(30, time_budget=2.0)
    assert "total_iterations" in engine.metrics
    assert engine.metrics["total_iterations"] > 0
    assert "final_lex_tuple" in engine.metrics


def test_engine_via_adapter_real():
    """Integration test: the real engine always runs (Phase C — no feature flag)."""
    from app.algorithms.adapters.rich_alns import RichALNSAdapter
    from app.algorithms.solver_adapter import SolveOptions

    vrp = load_synthetic("synthetic_30")
    adapter = RichALNSAdapter()
    result = adapter.solve(vrp, SolveOptions(time_budget_s=5.0, seed=0))
    assert result.solver_kind == "rich_alns"
    assert result.metrics.get("total_iterations", 0) > 0
