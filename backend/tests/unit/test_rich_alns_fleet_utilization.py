"""Regression tests for fleet-utilization (spread_fleet) in the rich_alns engine."""
from __future__ import annotations

import pytest

from app.algorithms.rich_vrp.alns.engine import ALNSConfig
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import (
    CostBreakdown,
    MatrixView,
    Problem,
    Request,
    Route,
    Vehicle,
    VehicleCost,
    VisitKind,
    Visit,
)
from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution
from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.vrp_solver import VehicleData, VehicleType, VRPInput
from app.models.enums import OrderPriority


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vehicle(idx: int, start_node: int = 0) -> Vehicle:
    return Vehicle(
        id=f"v{idx}", physical_id=f"v{idx}", index=idx, trip_index=0,
        start_node=start_node, end_node=start_node,
        shift_window=(0, 86400), hard_shift_end=None,
        capacity={"weight_g": 100_000, "volume_cm3": 100_000, "package_count": 100},
        tags=frozenset(),
        cost=VehicleCost(),
        profile="driving",
        max_distance_m=1_000_000, max_stops=100,
        break_window=None, break_duration_s=0,
        allow_fragile=True, allow_temp_sensitive=True,
    )


def _make_route(vehicle: Vehicle, delivery_count: int) -> Route:
    visits: list[Visit] = [
        Visit(request_id=None, kind=VisitKind.DEPOT_START, node=0, arrival_s=0, departure_s=0),
    ]
    for i in range(delivery_count):
        visits.append(
            Visit(request_id=f"r{vehicle.index}_{i}", kind=VisitKind.DELIVERY, node=1,
                  arrival_s=1800, departure_s=2100)
        )
    visits.append(
        Visit(request_id=None, kind=VisitKind.DEPOT_END, node=0, arrival_s=3600, departure_s=3600)
    )
    cost = CostBreakdown(total_generalized_cost=100)
    return Route(vehicle=vehicle, visits=visits, cost=cost)


def _make_problem(spread_fleet: bool, n_vehicles: int = 2) -> Problem:
    n_nodes = 2
    matrix = MatrixView([[0, 1000], [1000, 0]], [[0, 5000], [5000, 0]])
    vehicles = [_make_vehicle(i) for i in range(n_vehicles)]
    return Problem(
        requests=[],
        vehicles=vehicles,
        matrix=matrix,
        locations=[(41.0, 29.0), (41.1, 29.1)],
        spread_fleet=spread_fleet,
    )


# ---------------------------------------------------------------------------
# Test 1 — imbalance penalty maths
# ---------------------------------------------------------------------------

def test_imbalance_penalty_maths():
    """counts [5, 1] → spread_fleet=True adds (5-1)*coeff to generalized_cost."""
    from app.algorithms.rich_vrp.objective.lexicographic import _DELIVERY_SPREAD_COEFFICIENT_TL
    problem = _make_problem(spread_fleet=True, n_vehicles=2)
    v0, v1 = problem.vehicles
    routes = [_make_route(v0, 5), _make_route(v1, 1)]
    base_cost = sum(r.cost.total_generalized_cost for r in routes)

    obj_with = objective_for_solution(routes, set(), problem)
    assert obj_with.generalized_cost == base_cost + (5 - 1) * _DELIVERY_SPREAD_COEFFICIENT_TL

    problem_eff = _make_problem(spread_fleet=False, n_vehicles=2)
    obj_without = objective_for_solution(routes, set(), problem_eff)
    assert obj_without.generalized_cost == base_cost


# ---------------------------------------------------------------------------
# Test 2 — single active route → no penalty
# ---------------------------------------------------------------------------

def test_single_active_route_no_penalty():
    """When only one route has deliveries, spread_fleet=True adds zero penalty."""
    problem = _make_problem(spread_fleet=True, n_vehicles=2)
    v0, v1 = problem.vehicles
    routes = [_make_route(v0, 5), _make_route(v1, 0)]
    base_cost = sum(r.cost.total_generalized_cost for r in routes)

    obj = objective_for_solution(routes, set(), problem)
    assert obj.generalized_cost == base_cost


# ---------------------------------------------------------------------------
# Test 3 — from_options plumbing
# ---------------------------------------------------------------------------

def test_from_options_propagates_spread_fleet():
    """ALNSConfig.from_options must copy spread_fleet from SolveOptions."""
    opts_full = SolveOptions(spread_fleet=True)
    opts_eff = SolveOptions(spread_fleet=False)
    assert ALNSConfig.from_options(opts_full).spread_fleet is True
    assert ALNSConfig.from_options(opts_eff).spread_fleet is False


def test_build_problem_propagates_spread_fleet():
    """build_problem(spread_fleet=False) must set Problem.spread_fleet=False."""
    vehicles = [
        VehicleData(
            courier_id="c1",
            vehicle_type=VehicleType.MOTORCYCLE,
            capacity_grams=10_000,
        )
    ]
    vrp = VRPInput(
        orders=[],
        vehicles=vehicles,
        locations=[(41.0, 29.0)],
        duration_matrix=[[0]],
        distance_matrix=[[0]],
    )
    p_full = build_problem(vrp, spread_fleet=True)
    p_eff = build_problem(vrp, spread_fleet=False)
    assert p_full.spread_fleet is True
    assert p_eff.spread_fleet is False


# ---------------------------------------------------------------------------
# Test 4 — balanced counts produce lower penalty than imbalanced
# ---------------------------------------------------------------------------

def test_balanced_counts_lower_penalty_than_imbalanced():
    """Equal distribution (3,3) has zero penalty; (5,1) has non-zero penalty."""
    problem = _make_problem(spread_fleet=True, n_vehicles=2)
    v0, v1 = problem.vehicles

    routes_balanced = [_make_route(v0, 3), _make_route(v1, 3)]
    routes_imbalanced = [_make_route(v0, 5), _make_route(v1, 1)]

    obj_balanced = objective_for_solution(routes_balanced, set(), problem)
    obj_imbalanced = objective_for_solution(routes_imbalanced, set(), problem)

    assert obj_balanced.generalized_cost < obj_imbalanced.generalized_cost


# ---------------------------------------------------------------------------
# Test 5 — spread coefficient is bounded so it can't dominate other objectives
# ---------------------------------------------------------------------------

def test_spread_coefficient_is_bounded():
    """Penalty per imbalance unit must stay <=100 TL, otherwise it dominates a
    fuel-zeroed SERVICE_FIRST objective and the search gets pinned. See the
    May 2026 hang on Compare Routes for the failure mode this guards against."""
    from app.algorithms.rich_vrp.objective.lexicographic import _DELIVERY_SPREAD_COEFFICIENT_TL
    assert _DELIVERY_SPREAD_COEFFICIENT_TL <= 100, (
        "Spread coefficient too large: with fuel cost zeroed in SERVICE_FIRST "
        "scenarios, a >100 TL coefficient becomes the only objective signal "
        "and ALNS rejects nearly every move, producing infeasible solutions "
        "that are filtered out at validation (route_count=0)."
    )


# ---------------------------------------------------------------------------
# Test 6 — adapter enforces a hard deadline even when the engine doesn't return
# ---------------------------------------------------------------------------

def test_adapter_hard_deadline_returns_failed_solution(monkeypatch):
    """If _run_real_engine never returns, RichALNSAdapter must abandon the solve
    within budget × _HARD_DEADLINE_MULTIPLIER and return _failed_solution.
    Without this, the worker can wedge indefinitely on Cython hot paths that
    don't yield to Python's wall-clock checks."""
    import time

    from app.algorithms.adapters import rich_alns as rich_alns_mod
    from app.algorithms.adapters.rich_alns import RichALNSAdapter
    from app.algorithms.vrp_solver import SolverStatus

    def _runaway(_problem, _options, _name):
        time.sleep(60.0)
        raise AssertionError("engine should have been abandoned, not allowed to finish")

    monkeypatch.setattr(rich_alns_mod, "_run_real_engine", _runaway)

    vrp = VRPInput(
        orders=[], vehicles=[
            VehicleData(courier_id="c1", vehicle_type=VehicleType.MOTORCYCLE, capacity_grams=10_000),
        ],
        locations=[(41.0, 29.0)],
        duration_matrix=[[0]],
        distance_matrix=[[0]],
    )

    adapter = RichALNSAdapter()
    t0 = time.perf_counter()
    result = adapter.solve(vrp, SolveOptions(time_budget_s=1.0, spread_fleet=True))
    elapsed = time.perf_counter() - t0

    # Floor is 5s, so for time_budget_s=1.0 the deadline is max(5, 1.5) = 5s.
    assert elapsed < 8.0, f"adapter blocked for {elapsed:.1f}s; deadline didn't fire"
    assert result.metrics.get("engine_failed") is True
    assert result.metrics.get("failure_reason") == "deadline_exceeded"
    assert result.solution.solver_status in (SolverStatus.NOT_FOUND, SolverStatus.ERROR)
