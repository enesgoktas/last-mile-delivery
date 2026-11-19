"""V&V — determinism audit (activity A7 in the V&V plan).

Asserts that every solver adapter is *seed-deterministic*: given the same
problem and the same SolveOptions.seed, two runs return the same solution
signature (total cost, total distance, served order set).

Without this guarantee, you can't:
  - reproduce a customer-reported routing decision later
  - meaningfully diff two solver versions on the same input
  - run paired statistical tests (each "pair" needs to be reproducible)

Coverage:
  * rich_alns      — seed-driven metaheuristic; must be reproducible per seed
  * ortools_cvrptw — has its own internal seed; we pass it through SolveOptions
  * human_dispatcher — sweep heuristic, deterministic by construction
                       (independent of seed)
"""
from __future__ import annotations

import pytest

from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.solver_registry import get_solver
from tests.helpers.vv_helpers import solution_signature

# Keep budgets short so the suite stays under ~60s. With 30-order synthetic
# instances every solver finds a stable solution well within 3 seconds.
_TIME_BUDGET_S = 3.0
_DATASET = "synthetic_30"
_SEEDS = (0, 7, 42)


@pytest.mark.parametrize("seed", _SEEDS)
def test_rich_alns_deterministic_per_seed(seed: int) -> None:
    """rich_alns: same seed → identical solution signature."""
    adapter = get_solver("rich_alns")
    vrp = load_synthetic(_DATASET)

    sig1 = solution_signature(
        adapter.solve(vrp, SolveOptions(time_budget_s=_TIME_BUDGET_S, seed=seed)).solution
    )
    sig2 = solution_signature(
        adapter.solve(vrp, SolveOptions(time_budget_s=_TIME_BUDGET_S, seed=seed)).solution
    )
    assert sig1 == sig2, f"rich_alns not deterministic at seed={seed}"


@pytest.mark.parametrize("seed", _SEEDS)
def test_ortools_deterministic_per_seed(seed: int) -> None:
    """ortools_cvrptw: same seed → identical solution signature."""
    adapter = get_solver("ortools_cvrptw")
    vrp = load_synthetic(_DATASET)

    sig1 = solution_signature(
        adapter.solve(vrp, SolveOptions(time_budget_s=_TIME_BUDGET_S, seed=seed)).solution
    )
    sig2 = solution_signature(
        adapter.solve(vrp, SolveOptions(time_budget_s=_TIME_BUDGET_S, seed=seed)).solution
    )
    assert sig1 == sig2, f"ortools_cvrptw not deterministic at seed={seed}"


def test_human_dispatcher_seed_independent() -> None:
    """human_dispatcher is a sweep heuristic — output must be identical across seeds."""
    adapter = get_solver("human_dispatcher")
    vrp = load_synthetic(_DATASET)

    sigs = [
        solution_signature(
            adapter.solve(vrp, SolveOptions(time_budget_s=_TIME_BUDGET_S, seed=seed)).solution
        )
        for seed in _SEEDS
    ]
    assert len(set(sigs)) == 1, (
        "human_dispatcher signatures differ across seeds; "
        "the sweep heuristic should be independent of the random seed."
    )
