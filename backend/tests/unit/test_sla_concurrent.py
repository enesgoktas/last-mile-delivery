"""M8 SLA test: concurrent solve calls must complete within budget × 1.5.

Simulates 5 concurrent solver invocations (as would happen if multiple
scenario-comparison tabs are open simultaneously) and asserts that each
finishes within wall-clock_budget × 1.5 seconds.  The solver used here is
the OR-Tools adapter (always available, deterministic) because the real-engine
path requires a running ALNS engine which is slow in unit tests.

The test is intentionally lightweight (20-order synthetic instance, 10s budget)
so it runs in CI without special hardware.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.algorithms.adapters.ortools import ORToolsCVRPTWAdapter
from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.solver_adapter import SolveOptions

BUDGET_S = 10.0
CONCURRENCY = 5
WALL_CLOCK_TOLERANCE = 1.5  # each call must finish within BUDGET_S × this


@pytest.mark.asyncio
async def test_concurrent_solves_within_budget():
    """5 concurrent OR-Tools solves must each complete within budget × 1.5."""
    adapter = ORToolsCVRPTWAdapter()
    vrp = load_synthetic("synthetic_30")
    options = SolveOptions(time_budget_s=BUDGET_S, seed=0)

    async def _one_solve() -> float:
        t0 = time.perf_counter()
        await asyncio.to_thread(adapter.solve, vrp, options)
        return time.perf_counter() - t0

    t_start = time.perf_counter()
    results = await asyncio.gather(*[_one_solve() for _ in range(CONCURRENCY)])
    total_wall = time.perf_counter() - t_start

    for i, elapsed in enumerate(results):
        assert elapsed <= BUDGET_S * WALL_CLOCK_TOLERANCE, (
            f"solve #{i} took {elapsed:.2f}s > {BUDGET_S * WALL_CLOCK_TOLERANCE:.1f}s limit"
        )

    # Total wall-clock (with CONCURRENCY workers in threads) should be close
    # to a single solve, not CONCURRENCY × single solve.
    assert total_wall <= BUDGET_S * WALL_CLOCK_TOLERANCE * 1.5, (
        f"total concurrent wall clock {total_wall:.2f}s too high "
        f"(limit={BUDGET_S * WALL_CLOCK_TOLERANCE * 1.5:.1f}s)"
    )
