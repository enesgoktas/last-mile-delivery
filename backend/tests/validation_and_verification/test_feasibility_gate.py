"""V&V — always-on feasibility CI gate (activity B11 in the V&V plan).

The independent feasibility validator already runs in production solver
metrics. This integration test wires it into CI so any solver change that
produces an infeasible plan fails the build immediately.

What the gate asserts, on a fixed matrix of (adapter, instance):
  * Solver call does not raise.
  * `solution_validator.validate_solution()` reports `error_count == 0`.

Coverage matrix:
  * adapters    = {rich_alns, ortools_cvrptw, human_dispatcher}
  * instances   = {synthetic_30, synthetic_60, synthetic_120}
  * seed        = 42 (one fixed seed; the determinism suite covers seed
                  reproducibility separately).

Why synthetic instances rather than historical days: they're always
available, don't depend on a populated database, and run identically on a
fresh checkout. The replay regression suite (tests/replay/) covers the
"vs. real production data" angle separately when snapshots are present.
"""
from __future__ import annotations

import pytest

from app.algorithms.rich_vrp.bench.datasets import load_synthetic
from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.solver_registry import get_solver
from tests.helpers.vv_helpers import validate_vrp_solution

# Per-instance time budgets — large enough that every solver finds a
# feasible solution, small enough to keep CI under a couple of minutes
# total across the full matrix.
_BUDGETS_S = {
    "synthetic_30": 5.0,
    "synthetic_60": 10.0,
    "synthetic_120": 20.0,
}

_INSTANCES = tuple(_BUDGETS_S.keys())
_ADAPTERS = ("rich_alns", "ortools_cvrptw", "human_dispatcher")
_GATE_SEED = 42


@pytest.mark.parametrize("adapter_kind", _ADAPTERS)
@pytest.mark.parametrize("instance", _INSTANCES)
def test_solver_produces_feasible_solution(adapter_kind: str, instance: str) -> None:
    """Every (adapter, instance) pair must produce a feasibility-valid plan.

    A failure here means a recent solver change started shipping an
    infeasible plan — capacity overflow, time-window violation,
    pickup-after-delivery, etc. The independent validator catches it
    regardless of which solver wrote the broken plan.
    """
    adapter = get_solver(adapter_kind)
    vrp = load_synthetic(instance)
    budget = _BUDGETS_S[instance]

    result = adapter.solve(vrp, SolveOptions(time_budget_s=budget, seed=_GATE_SEED))
    validation = validate_vrp_solution(result.solution, vrp)

    if validation.error_count > 0:
        # Render every violation so the CI log makes the failure mode obvious.
        details = "\n".join(
            f"  - {v.violation_type.value} on courier={v.route_courier_id} "
            f"order={v.order_id}: {v.message}"
            for v in validation.violations
            if v.severity == "error"
        )
        pytest.fail(
            f"{adapter_kind} produced {validation.error_count} infeasibility "
            f"violation(s) on {instance}:\n{details}"
        )


@pytest.mark.parametrize("adapter_kind", _ADAPTERS)
@pytest.mark.parametrize("instance", _INSTANCES)
def test_no_duplicate_assignments(adapter_kind: str, instance: str) -> None:
    """No order should appear in more than one route, and not in both
    routes and unassigned_orders.

    This is a structural integrity check that complements the feasibility
    validator (which checks per-route constraints). A duplicate assignment
    can produce a plan that looks feasible per-route but ships the same
    order twice in aggregate.
    """
    adapter = get_solver(adapter_kind)
    vrp = load_synthetic(instance)
    budget = _BUDGETS_S[instance]

    result = adapter.solve(vrp, SolveOptions(time_budget_s=budget, seed=_GATE_SEED))
    sol = result.solution

    served_ids: list[str] = []
    for route in sol.routes:
        for stop in route.stops:
            if stop.stop_type == "DELIVERY" and stop.order_id:
                served_ids.append(stop.order_id)

    assert len(served_ids) == len(set(served_ids)), (
        f"{adapter_kind} produced duplicate DELIVERY assignments on {instance}: "
        f"{[oid for oid in served_ids if served_ids.count(oid) > 1]}"
    )

    served_set = set(served_ids)
    unassigned_set = set(sol.unassigned_orders)
    overlap = served_set & unassigned_set
    assert overlap == set(), (
        f"{adapter_kind} on {instance}: orders appear in BOTH served and "
        f"unassigned: {sorted(overlap)}"
    )

    expected_total = {o.order_id for o in vrp.orders}
    actual_total = served_set | unassigned_set
    missing = expected_total - actual_total
    extra = actual_total - expected_total
    assert not missing, f"{adapter_kind} on {instance}: orders lost (neither served nor unassigned): {sorted(missing)}"
    assert not extra, f"{adapter_kind} on {instance}: phantom orders not in input: {sorted(extra)}"
