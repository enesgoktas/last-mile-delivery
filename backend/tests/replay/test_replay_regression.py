"""Replay regression tests: compare current solver against baseline snapshots.

Each snapshot encodes the VRPInput and baseline metrics recorded from a
previous production run.  These tests assert that the current solver does
not regress on served_count, cost, or URGENT coverage.

Run after populating tests/replay/snapshots/:
    pytest tests/replay/ -v --no-cov

Without snapshots the suite collects zero tests and passes trivially.
"""
from __future__ import annotations

import pytest

from app.algorithms.adapters.rich_alns import RichALNSAdapter
from app.algorithms.solver_adapter import SolveOptions

pytestmark = pytest.mark.replay  # skip by default with -m "not replay"


@pytest.fixture
def adapter():
    return RichALNSAdapter()


def test_served_count_within_tolerance(replay_snapshot, adapter):
    """Served count must not drop by more than 2% vs baseline."""
    from app.algorithms.vrp_solver import VRPInput  # noqa: PLC0415
    vrp_input = VRPInput(**replay_snapshot["vrp_input"])
    baseline = replay_snapshot["baseline"]

    result = adapter.solve(vrp_input, SolveOptions(time_budget_s=60.0))
    served = len(vrp_input.orders) - len(result.solution.unassigned_orders)
    tolerance = max(1, int(baseline["served_count"] * 0.02))

    assert served >= baseline["served_count"] - tolerance, (
        f"served {served} < baseline {baseline['served_count']} - {tolerance}"
    )


def test_cost_within_tolerance(replay_snapshot, adapter):
    """Generalized cost must not exceed baseline by more than 5%."""
    from app.algorithms.vrp_solver import VRPInput  # noqa: PLC0415
    vrp_input = VRPInput(**replay_snapshot["vrp_input"])
    baseline = replay_snapshot["baseline"]

    result = adapter.solve(vrp_input, SolveOptions(time_budget_s=60.0))
    cost = getattr(result.solution.cost_breakdown, "total_generalized_cost", 0) or 0
    baseline_cost = baseline.get("total_generalized_cost", 0)

    if baseline_cost <= 0:
        pytest.skip("no baseline cost recorded")

    assert cost <= baseline_cost * 1.05, (
        f"cost {cost} exceeds baseline {baseline_cost} × 1.05 = {baseline_cost * 1.05}"
    )


def test_no_urgent_regression(replay_snapshot, adapter):
    """No URGENT request that was served in baseline may be unserved now."""
    from app.algorithms.vrp_solver import VRPInput  # noqa: PLC0415
    vrp_input = VRPInput(**replay_snapshot["vrp_input"])
    baseline_served_urgent = set(replay_snapshot["baseline"].get("served_urgent_ids", []))

    if not baseline_served_urgent:
        pytest.skip("no URGENT orders in baseline")

    result = adapter.solve(vrp_input, SolveOptions(time_budget_s=60.0))
    now_unserved = set(result.solution.unassigned_orders)

    regressions = baseline_served_urgent & now_unserved
    assert not regressions, (
        f"URGENT orders now unserved that were served in baseline: {regressions}"
    )
