from __future__ import annotations

import app.algorithms.adapters.human_dispatcher as hd_mod
import app.algorithms.adapters.ortools as ortools_mod
from app.algorithms.adapters.human_dispatcher import HumanDispatcherBaselineAdapter
from app.algorithms.adapters.ortools import ORToolsCVRPTWAdapter
from app.algorithms.adapters.rich_alns import RichALNSAdapter
from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.vrp_solver import (
    OrderData,
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VehicleData,
    VRPSolution,
    build_vrp_input,
)
from app.models.enums import VehicleType

HOUR = 3600


def _fixed_solution() -> VRPSolution:
    route = PlannedRoute(
        courier_id="c1",
        stops=[
            PlannedStop(
                order_id="o1",
                stop_type="PICKUP",
                location_index=1,
                lat=41.01,
                lon=29.01,
                arrival_time_s=8 * HOUR,
                departure_time_s=8 * HOUR,
            ),
            PlannedStop(
                order_id="o1",
                stop_type="DELIVERY",
                location_index=2,
                lat=41.02,
                lon=29.02,
                arrival_time_s=9 * HOUR,
                departure_time_s=9 * HOUR,
            ),
        ],
        total_distance_m=2_000,
        total_duration_s=HOUR,
    )
    return VRPSolution(
        routes=[route],
        unassigned_orders=[],
        total_distance_m=2_000,
        total_duration_s=HOUR,
        solver_status=SolverStatus.FEASIBLE,
    )


def _small_vrp_input():
    order = OrderData(
        order_id="o1",
        pickup_index=1,
        delivery_index=2,
        weight_grams=1_000,
        earliest_time_s=0,
        latest_time_s=20 * HOUR,
        hard_latest_time_s=20 * HOUR,
        pickup_earliest_time_s=0,
        pickup_latest_time_s=20 * HOUR,
        service_time_s=0,
        unserved_penalty=1_000,
    )
    vehicle = VehicleData(
        courier_id="c1",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=10_000,
        depot_index=0,
        start_time_s=0,
        end_time_s=20 * HOUR,
        hard_end_time_s=20 * HOUR,
    )
    return build_vrp_input(
        locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
        distance_matrix=[[0, 1_000, 2_000], [1_000, 0, 1_000], [2_000, 1_000, 0]],
        duration_matrix=[[0, 60, 120], [60, 0, 60], [120, 60, 0]],
        orders=[order],
        vehicles=[vehicle],
    )


def test_rich_alns_solver_kind_is_always_rich_alns(monkeypatch):
    """solver_kind must be rich_alns regardless of what the engine returns."""
    from unittest.mock import patch

    from app.algorithms.solver_adapter import SolveResult

    fixed = _fixed_solution()
    engine_result = SolveResult(solution=fixed, solver_kind="rich_alns", elapsed_s=1.0, metrics={})

    problem = _small_vrp_input()
    adapter = RichALNSAdapter()
    with patch("app.algorithms.adapters.rich_alns._run_real_engine", return_value=engine_result):
        for seed in range(5):
            result = adapter.solve(problem, SolveOptions(time_budget_s=1.0, seed=seed))
            assert result.solver_kind == "rich_alns"
            assert result.metrics.get("delegating_to") is None


def test_ortools_adapter_solver_kind(monkeypatch):
    fixed = _fixed_solution()
    monkeypatch.setattr(ortools_mod, "solve", lambda *a, **kw: fixed)

    result = ORToolsCVRPTWAdapter().solve(_small_vrp_input(), SolveOptions())
    assert result.solver_kind == "ortools_cvrptw"
    assert result.solution is fixed


def test_human_dispatcher_adapter_solver_kind(monkeypatch):
    fixed = _fixed_solution()
    monkeypatch.setattr(hd_mod, "solve_human_dispatcher_baseline", lambda *a, **kw: fixed)

    result = HumanDispatcherBaselineAdapter().solve(_small_vrp_input(), SolveOptions())
    assert result.solver_kind == "human_dispatcher"
    assert result.solution is fixed


def test_all_adapters_support_any_input(monkeypatch):
    monkeypatch.setattr(ortools_mod, "solve", lambda *a, **kw: _fixed_solution())
    monkeypatch.setattr(hd_mod, "solve_human_dispatcher_baseline", lambda *a, **kw: _fixed_solution())

    problem = _small_vrp_input()
    assert ORToolsCVRPTWAdapter().supports(problem) is True
    assert RichALNSAdapter().supports(problem) is True
    assert HumanDispatcherBaselineAdapter().supports(problem) is True
