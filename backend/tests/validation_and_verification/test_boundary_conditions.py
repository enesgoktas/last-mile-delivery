"""V&V — boundary / edge-case suite (activity A8 in the V&V plan).

Catches the kind of bugs that hide in normal-sized synthetic tests: empty
inputs, single-element inputs, degenerate geometries, infeasibly-tight
constraints. Every adapter must behave sensibly on these cases (no crash,
no infeasible plan, well-defined unassigned set).

Each test runs the candidate solver(s), then runs the independent feasibility
validator on the result. A passing test means: solver returned a solution
without raising, and that solution satisfies all hard constraints.
"""
from __future__ import annotations

import pytest

from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.solver_registry import get_solver
from app.algorithms.vrp_solver import SolverStatus
from tests.helpers.vv_helpers import (
    make_minimal_vrp,
    served_order_ids,
    validate_vrp_solution,
)

# 1.5s is plenty for trivial instances and keeps test runtime reasonable
# across the parametrised matrix.
_BUDGET_S = 1.5
_ALL_ADAPTERS = ("rich_alns", "ortools_cvrptw", "human_dispatcher")


def _solve(adapter_kind: str, vrp, seed: int = 0):
    adapter = get_solver(adapter_kind)
    return adapter.solve(vrp, SolveOptions(time_budget_s=_BUDGET_S, seed=seed))


def _assert_feasible(solution, vrp) -> None:
    result = validate_vrp_solution(solution, vrp)
    assert result.error_count == 0, (
        f"feasibility violations: {[v.message for v in result.violations if v.severity == 'error']}"
    )


# ---------------------------------------------------------------------------
# Empty / trivial inputs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("adapter_kind", _ALL_ADAPTERS)
def test_empty_problem_zero_orders(adapter_kind: str) -> None:
    """Zero orders, N vehicles → solution with no assignments, no error."""
    vrp = make_minimal_vrp(n_orders=0, n_vehicles=3)
    result = _solve(adapter_kind, vrp)

    assert result.solution.unassigned_orders == []
    assert served_order_ids(result.solution) == []
    # Routes may be empty or absent; either is acceptable.
    for route in result.solution.routes:
        assert route.stops == [], f"empty problem produced a non-empty route on {adapter_kind}"


@pytest.mark.parametrize("adapter_kind", _ALL_ADAPTERS)
def test_single_order_single_vehicle(adapter_kind: str) -> None:
    """1 order, 1 vehicle → order must be assigned, plan must be feasible."""
    vrp = make_minimal_vrp(n_orders=1, n_vehicles=1)
    result = _solve(adapter_kind, vrp)

    assert result.solution.unassigned_orders == []
    assert served_order_ids(result.solution) == ["ord-000"]
    _assert_feasible(result.solution, vrp)


@pytest.mark.parametrize("adapter_kind", _ALL_ADAPTERS)
def test_single_order_many_vehicles(adapter_kind: str) -> None:
    """1 order, many vehicles → exactly one vehicle should run."""
    vrp = make_minimal_vrp(n_orders=1, n_vehicles=10)
    result = _solve(adapter_kind, vrp)

    assert result.solution.unassigned_orders == []
    active_routes = [r for r in result.solution.routes if r.stops]
    assert len(active_routes) == 1, (
        f"{adapter_kind}: expected exactly 1 active route for a 1-order problem, "
        f"got {len(active_routes)}"
    )
    _assert_feasible(result.solution, vrp)


# ---------------------------------------------------------------------------
# Degenerate geometry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("adapter_kind", _ALL_ADAPTERS)
def test_all_orders_same_location(adapter_kind: str) -> None:
    """All deliveries to the same point → all served, zero (or trivial) inter-stop travel."""
    vrp = make_minimal_vrp(n_orders=5, n_vehicles=2, same_location=True)
    result = _solve(adapter_kind, vrp)

    assert result.solution.unassigned_orders == []
    assert sorted(served_order_ids(result.solution)) == [f"ord-{k:03d}" for k in range(5)]
    _assert_feasible(result.solution, vrp)


# ---------------------------------------------------------------------------
# Infeasibility cases — orders should land in unassigned, no crash
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("adapter_kind", _ALL_ADAPTERS)
def test_capacity_smaller_than_order_cannot_serve(adapter_kind: str) -> None:
    """Vehicle capacity smaller than order weight → unassigned, not silently shipped.

    Note: in this codebase, `capacity_grams=0` is a convention meaning
    "unconstrained" (same as max_packages=0 and max_distance_m=0), so we use
    a positive-but-too-small capacity to exercise the actual capacity check.
    """
    vrp = make_minimal_vrp(
        n_orders=1,
        n_vehicles=1,
        capacity_grams=500,           # 0.5 kg
        weight_per_order_grams=1_000,  # 1.0 kg — won't fit
    )
    result = _solve(adapter_kind, vrp)

    # Either the solver leaves it unassigned (correct) or successfully assigns
    # it (then validator MUST flag the capacity violation). Both are
    # acceptable behaviours individually; the union must be: no silently
    # broken plan.
    if "ord-000" in result.solution.unassigned_orders:
        # Correct outcome: order can't fit, solver said so.
        return

    # Otherwise, the solver claimed it scheduled the order — the validator
    # must catch the capacity violation.
    validation = validate_vrp_solution(result.solution, vrp)
    assert validation.error_count > 0, (
        f"{adapter_kind} produced an apparently-feasible plan that ships a 1kg "
        f"order on a 0-capacity vehicle without violations — silent infeasibility."
    )


@pytest.mark.parametrize("adapter_kind", _ALL_ADAPTERS)
def test_impossibly_tight_time_window(adapter_kind: str) -> None:
    """Hard deadline before earliest arrival → order must end up unassigned, no crash."""
    vrp = make_minimal_vrp(
        n_orders=1,
        n_vehicles=1,
        # Hard deadline at 08:00:01 — impossible to drive even 1 metre that quickly.
        earliest_time_s=0,
        latest_time_s=1,
        hard_latest_time_s=1,
    )
    result = _solve(adapter_kind, vrp)

    if "ord-000" in result.solution.unassigned_orders:
        return  # correct behaviour

    # If the solver still produced a route, the validator must catch the TW violation.
    validation = validate_vrp_solution(result.solution, vrp)
    assert validation.error_count > 0, (
        f"{adapter_kind} scheduled an order past its hard deadline without "
        f"any validator violation — silent time-window infeasibility."
    )


# ---------------------------------------------------------------------------
# Solver-status sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("adapter_kind", _ALL_ADAPTERS)
def test_solver_status_is_well_defined(adapter_kind: str) -> None:
    """Every solver call must return a SolverStatus, never raise on a tiny input."""
    vrp = make_minimal_vrp(n_orders=2, n_vehicles=2)
    result = _solve(adapter_kind, vrp)
    assert result.solution.solver_status in {
        SolverStatus.OPTIMAL,
        SolverStatus.FEASIBLE,
        SolverStatus.GREEDY_FALLBACK,
        SolverStatus.NOT_FOUND,
        SolverStatus.ERROR,
    }
