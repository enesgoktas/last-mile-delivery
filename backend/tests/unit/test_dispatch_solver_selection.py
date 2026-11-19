from __future__ import annotations

from types import SimpleNamespace

from app.algorithms.solution_validator import ValidationResult, Violation, ViolationType
from app.algorithms.solver_adapter import SolveResult
from app.algorithms.vrp_solver import (
    OrderData,
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VehicleData,
    VRPSolution,
)
from app.models.enums import VehicleType
from app.schemas.operations_policy import OptimizationSettings
from app.services import dispatch_service


class _MockAdapter:
    """Stand-in for get_solver(...) in tests that don't invoke a real solver."""

    name = "ortools_cvrptw"

    def __init__(self, solution: VRPSolution) -> None:
        self._solution = solution

    def solve(self, problem, options) -> SolveResult:
        return SolveResult(
            solution=self._solution,
            solver_kind=self.name,
            elapsed_s=0.0,
        )

    def supports(self, problem) -> bool:
        return True

    def explain_limits(self, problem) -> list[str]:
        return []

HOUR = 3600


def _order(order_id: str = "order-1") -> OrderData:
    return OrderData(
        order_id=order_id,
        pickup_index=0,
        delivery_index=1,
        weight_grams=1_000,
        earliest_time_s=8 * HOUR,
        latest_time_s=12 * HOUR,
        hard_latest_time_s=13 * HOUR,
        pickup_earliest_time_s=0,
        pickup_latest_time_s=12 * HOUR,
        service_time_s=0,
        unserved_penalty=1_000,
    )


def _vehicle(courier_id: str = "courier-1") -> VehicleData:
    return VehicleData(
        courier_id=courier_id,
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=10_000,
        depot_index=0,
        start_time_s=8 * HOUR,
        end_time_s=18 * HOUR,
        hard_end_time_s=19 * HOUR,
    )


def _solution(
    *,
    solver_status: SolverStatus = SolverStatus.FEASIBLE,
    order_id: str = "order-1",
    courier_id: str = "courier-1",
    unassigned_orders: list[str] | None = None,
) -> VRPSolution:
    route = PlannedRoute(
        courier_id=courier_id,
        stops=[
            PlannedStop(
                order_id=order_id,
                stop_type="PICKUP",
                location_index=0,
                lat=41.0,
                lon=29.0,
                arrival_time_s=8 * HOUR,
                departure_time_s=8 * HOUR,
            ),
            PlannedStop(
                order_id=order_id,
                stop_type="DELIVERY",
                location_index=1,
                lat=41.01,
                lon=29.01,
                arrival_time_s=9 * HOUR,
                departure_time_s=9 * HOUR,
            ),
        ],
        total_distance_m=2_000,
        total_duration_s=HOUR,
    )
    return VRPSolution(
        routes=[route],
        unassigned_orders=unassigned_orders or [],
        total_distance_m=2_000,
        total_duration_s=HOUR,
        solver_status=solver_status,
    )


def _empty_solution(*, unassigned_orders: list[str]) -> VRPSolution:
    return VRPSolution(
        routes=[],
        unassigned_orders=unassigned_orders,
        total_distance_m=0,
        total_duration_s=0,
        solver_status=SolverStatus.FEASIBLE,
    )


def _valid_validation() -> ValidationResult:
    return ValidationResult(is_valid=True, routes_checked=1)


def _invalid_validation() -> ValidationResult:
    return ValidationResult(
        is_valid=False,
        violations=[
            Violation(
                violation_type=ViolationType.SHIFT_EXCEEDED,
                route_courier_id="courier-1",
                order_id="order-1",
                message="route exceeds shift",
            )
        ],
        routes_checked=1,
        routes_with_violations=1,
    )


def _patch_minimal_vrp(monkeypatch, *, orders: list[OrderData], vehicles: list[VehicleData]) -> None:
    monkeypatch.setattr(
        dispatch_service,
        "_build_vrp_data_structures",
        lambda *args, **kwargs: (orders, vehicles),
    )
    monkeypatch.setattr(dispatch_service, "build_initial_routes", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch_service, "two_opt_improve", lambda solution, *args, **kwargs: solution)
    monkeypatch.setattr(dispatch_service.settings, "FLEXIBLE_RELOAD_SOLVER_ENABLED", True)


async def test_solve_batch_variant_calls_registry_with_requested_solver_kind(monkeypatch) -> None:
    """Explicit guard that the dispatch path actually goes through
    solver_registry.get_solver() with the kind it was asked for, and that
    the time-budget flows into SolveOptions. Catches regressions where some
    refactor instantiates an adapter directly or hard-codes the kind.
    """
    captured_kinds: list[str] = []
    captured_budgets: list[float] = []
    primary_solution = _solution()

    class _CapturingAdapter:
        name = "rich_alns"

        def solve(self, problem, options) -> SolveResult:
            captured_budgets.append(options.time_budget_s)
            return SolveResult(
                solution=primary_solution,
                solver_kind=self.name,
                elapsed_s=0.0,
            )

        def supports(self, problem) -> bool:
            return True

        def explain_limits(self, problem) -> list[str]:
            return []

    def _capture_get_solver(kind: str):
        captured_kinds.append(kind)
        return _CapturingAdapter()

    _patch_minimal_vrp(monkeypatch, orders=[_order()], vehicles=[_vehicle()])
    monkeypatch.setattr(dispatch_service, "get_solver", _capture_get_solver)
    monkeypatch.setattr(dispatch_service, "_validate_batch_solution", lambda *args, **kwargs: _valid_validation())

    await dispatch_service._solve_batch_variant(
        db=None,
        tenant_id="tenant-1",
        orders=[],
        couriers=[],
        locations=[(41.0, 29.0), (41.01, 29.01)],
        loc_index={},
        distance_matrix=[[0, 1_000], [1_000, 0]],
        duration_matrix=[[0, 60], [60, 0]],
        global_constraints={"service_time_minutes": 0},
        order_packages={},
        optimization_settings=OptimizationSettings(),
        cache_solution_result=False,
        skip_multi_trip=True,
        solver_time_limit_seconds=42,
        solver_kind="rich_alns",
    )

    assert captured_kinds == ["rich_alns"], (
        f"dispatch should look up exactly one solver via the registry; got {captured_kinds!r}"
    )
    assert captured_budgets == [42.0], (
        f"time_budget_s should flow from solver_time_limit_seconds; got {captured_budgets!r}"
    )


async def test_solve_batch_variant_prefers_valid_ortools_primary(monkeypatch) -> None:
    primary_solution = _solution()
    _patch_minimal_vrp(monkeypatch, orders=[_order()], vehicles=[_vehicle()])
    monkeypatch.setattr(dispatch_service, "get_solver", lambda kind: _MockAdapter(primary_solution))
    monkeypatch.setattr(dispatch_service, "_validate_batch_solution", lambda *args, **kwargs: _valid_validation())

    def fail_lexicographic(*args, **kwargs):
        raise AssertionError("valid OR-Tools primary should not invoke lexicographic fallback")

    monkeypatch.setattr(dispatch_service, "lexicographic_solve_batch", fail_lexicographic)

    solution, _vrp_vehicles, multi_trip_vehicles, _inputs, metrics = await dispatch_service._solve_batch_variant(
        db=None,
        tenant_id="tenant-1",
        orders=[],
        couriers=[],
        locations=[(41.0, 29.0), (41.01, 29.01)],
        loc_index={},
        distance_matrix=[[0, 1_000], [1_000, 0]],
        duration_matrix=[[0, 60], [60, 0]],
        global_constraints={"service_time_minutes": 0},
        order_packages={},
        optimization_settings=OptimizationSettings(),
        cache_solution_result=False,
        skip_multi_trip=True,
        solver_time_limit_seconds=1,
    )

    assert solution is primary_solution
    assert multi_trip_vehicles == [_vehicle()]
    assert metrics["selected_solver"] == "rich_alns"
    assert metrics["lexicographic_fallback_attempted"] is False


async def test_invalid_ortools_primary_uses_labeled_lexicographic_fallback(monkeypatch) -> None:
    primary_solution = _solution()
    fallback_solution = _solution(order_id="order-1")
    _patch_minimal_vrp(monkeypatch, orders=[_order()], vehicles=[_vehicle()])
    monkeypatch.setattr(dispatch_service, "get_solver", lambda kind: _MockAdapter(primary_solution))
    validations = [_invalid_validation(), _valid_validation()]
    monkeypatch.setattr(dispatch_service, "_validate_batch_solution", lambda *args, **kwargs: validations.pop(0))
    monkeypatch.setattr(
        dispatch_service,
        "lexicographic_solve_batch",
        lambda *args, **kwargs: SimpleNamespace(
            solution=fallback_solution,
            diagnostics={
                "optimization_trace": [{"stage": "seed"}],
                "assignment_count_status": "all_globally_feasible_assigned",
            },
        ),
    )

    solution, _vrp_vehicles, multi_trip_vehicles, _inputs, metrics = await dispatch_service._solve_batch_variant(
        db=None,
        tenant_id="tenant-1",
        orders=[],
        couriers=[],
        locations=[(41.0, 29.0), (41.01, 29.01)],
        loc_index={},
        distance_matrix=[[0, 1_000], [1_000, 0]],
        duration_matrix=[[0, 60], [60, 0]],
        global_constraints={"service_time_minutes": 0, "depot_reload_time_minutes": 15},
        order_packages={},
        optimization_settings=OptimizationSettings(),
        cache_solution_result=False,
        skip_multi_trip=True,
        solver_time_limit_seconds=1,
    )

    assert solution is fallback_solution
    assert multi_trip_vehicles == [_vehicle()]
    assert metrics["selected_solver"] == "lexicographic_fallback"
    assert metrics["lexicographic_fallback_selected"] is True
    assert metrics["lexicographic_fallback_reason"] == "primary_validation_failed"
    assert metrics["primary_ortools_validation"]["error_count"] == 1
    assert metrics["lexicographic_fallback_validation"]["error_count"] == 0


async def test_assignment_collapse_uses_labeled_lexicographic_fallback(monkeypatch) -> None:
    primary_solution = _empty_solution(unassigned_orders=["order-1"])
    fallback_solution = _solution(order_id="order-1")
    _patch_minimal_vrp(monkeypatch, orders=[_order()], vehicles=[_vehicle()])
    monkeypatch.setattr(dispatch_service, "get_solver", lambda kind: _MockAdapter(primary_solution))
    validations = [_valid_validation(), _valid_validation()]
    monkeypatch.setattr(dispatch_service, "_validate_batch_solution", lambda *args, **kwargs: validations.pop(0))
    monkeypatch.setattr(
        dispatch_service,
        "lexicographic_solve_batch",
        lambda *args, **kwargs: SimpleNamespace(
            solution=fallback_solution,
            diagnostics={
                "optimization_trace": [{"stage": "seed"}],
                "assignment_count_status": "all_globally_feasible_assigned",
            },
        ),
    )

    solution, _vrp_vehicles, _multi_trip_vehicles, _inputs, metrics = await dispatch_service._solve_batch_variant(
        db=None,
        tenant_id="tenant-1",
        orders=[],
        couriers=[],
        locations=[(41.0, 29.0), (41.01, 29.01)],
        loc_index={},
        distance_matrix=[[0, 1_000], [1_000, 0]],
        duration_matrix=[[0, 60], [60, 0]],
        global_constraints={"service_time_minutes": 0, "depot_reload_time_minutes": 15},
        order_packages={},
        optimization_settings=OptimizationSettings(),
        cache_solution_result=False,
        skip_multi_trip=True,
        solver_time_limit_seconds=1,
    )

    assert solution is fallback_solution
    assert metrics["selected_solver"] == "lexicographic_fallback"
    assert metrics["lexicographic_fallback_selected"] is True
    assert metrics["lexicographic_fallback_reason"] == "primary_assignment_collapse"
    assert metrics["primary_ortools_summary"]["assigned_count"] == 0
    assert metrics["lexicographic_fallback_summary"]["assigned_count"] == 1


async def test_human_baseline_remains_human_only_when_it_leaves_orders_unassigned(monkeypatch) -> None:
    baseline_solution = _solution(unassigned_orders=["order-2"])
    _patch_minimal_vrp(
        monkeypatch,
        orders=[_order("order-1"), _order("order-2")],
        vehicles=[_vehicle()],
    )
    monkeypatch.setattr(
        dispatch_service,
        "solve_human_dispatcher_baseline",
        lambda *args, **kwargs: baseline_solution,
    )
    monkeypatch.setattr(dispatch_service, "_validate_batch_solution", lambda *args, **kwargs: _valid_validation())

    def fail_lexicographic(*args, **kwargs):
        raise AssertionError("human baseline should not be completed by lexicographic fallback")

    monkeypatch.setattr(dispatch_service, "lexicographic_solve_batch", fail_lexicographic)

    solution, _vrp_vehicles, _multi_trip_vehicles, _inputs, metrics = (
        await dispatch_service._solve_human_dispatcher_baseline_variant(
            db=None,
            tenant_id="tenant-1",
            orders=[],
            couriers=[],
            locations=[(41.0, 29.0), (41.01, 29.01)],
            loc_index={},
            distance_matrix=[[0, 1_000], [1_000, 0]],
            duration_matrix=[[0, 60], [60, 0]],
            global_constraints={"service_time_minutes": 0},
            order_packages={},
            optimization_settings=OptimizationSettings(),
            skip_multi_trip=True,
        )
    )

    assert solution is baseline_solution
    assert solution.unassigned_orders == ["order-2"]
    assert metrics["selected_solver"] == "human_dispatcher_baseline"
    assert metrics["baseline_raw_assigned_count"] == 1
    assert metrics["baseline_flexible_completion_applied"] is False
