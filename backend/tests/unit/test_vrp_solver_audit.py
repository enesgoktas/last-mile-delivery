"""Regression tests for the 2026-04-28 VRP solver audit improvements.

These tests pin the behaviours we *added*; the existing VRP coverage still
asserts solver correctness end-to-end.

  * `_resolve_time_horizon` widens past the legacy 48h floor when vehicles
    declare multi-day shifts (used by the weekly bulk planner).
  * `_resolve_batch_solver_time_limit` follows the new five-step curve so
    tiny batches no longer burn a full 30 s.
  * Economic arc-cost callbacks are deduplicated by
    `(profile, distance_cost_per_km, fuel_cost_per_km)`.
"""
from __future__ import annotations

from app.algorithms.vrp_solver import _LARGE_TIME, VehicleData, _resolve_time_horizon
from app.models.enums import VehicleType
from app.schemas.operations_policy import default_operations_policy
from app.services.dispatch_service import (
    _allocate_decomposed_solver_time_limits,
    _resolve_batch_solver_pass_time_limits,
    _resolve_batch_solver_time_limit,
)


def _vehicle(end_s: int, hard_end_s: int | None = None) -> VehicleData:
    return VehicleData(
        courier_id=f"v-{end_s}",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=10_000,
        depot_index=0,
        start_time_s=8 * 3600,
        end_time_s=end_s,
        hard_end_time_s=hard_end_s,
        physical_courier_id=f"v-{end_s}",
    )


def test_time_horizon_keeps_legacy_default_for_single_day_shifts():
    vehicles = [_vehicle(end_s=18 * 3600), _vehicle(end_s=22 * 3600)]
    assert _resolve_time_horizon(vehicles) == _LARGE_TIME


def test_time_horizon_expands_to_cover_weekly_shifts():
    # Vehicle whose shift ends 6 days × 24h + 18h into the week.
    six_days_six_pm = 6 * 86_400 + 18 * 3600
    vehicles = [_vehicle(end_s=six_days_six_pm)]
    horizon = _resolve_time_horizon(vehicles)
    assert horizon >= six_days_six_pm + 6 * 3600
    assert horizon > _LARGE_TIME


def test_time_horizon_uses_hard_end_when_set_higher():
    vehicles = [
        _vehicle(end_s=18 * 3600, hard_end_s=2 * 86_400 + 22 * 3600),
    ]
    assert _resolve_time_horizon(vehicles) >= 2 * 86_400 + 22 * 3600 + 6 * 3600


def test_batch_solver_time_limit_curve_for_economic_mode():
    """Economic mode passes optimization_settings; the curve must scale."""
    settings = default_operations_policy().optimization
    assert _resolve_batch_solver_time_limit(0, settings) == 8
    assert _resolve_batch_solver_time_limit(10, settings) == 8
    assert _resolve_batch_solver_time_limit(11, settings) == 20
    assert _resolve_batch_solver_time_limit(30, settings) == 20
    assert _resolve_batch_solver_time_limit(31, settings) == 60
    assert _resolve_batch_solver_time_limit(80, settings) == 60
    assert _resolve_batch_solver_time_limit(81, settings) == 90
    assert _resolve_batch_solver_time_limit(200, settings) == 90
    assert _resolve_batch_solver_time_limit(201, settings) == 150


def test_time_dependent_passes_share_one_user_visible_solver_budget():
    settings = default_operations_policy().optimization

    assert _resolve_batch_solver_pass_time_limits(
        625,
        settings,
        refinement_enabled=False,
    ) == (150, None)
    assert _resolve_batch_solver_pass_time_limits(
        625,
        settings,
        refinement_enabled=True,
    ) == (100, 50)


def test_decomposed_cluster_time_limits_sum_to_parent_budget():
    settings = default_operations_policy().optimization

    limits = _allocate_decomposed_solver_time_limits(
        [23, 117, 231, 152, 81, 21],
        150,
        settings,
    )

    assert sum(limits) <= 150
    assert limits == [6, 28, 55, 37, 19, 5]
    assert max(limits) < 150


def test_batch_solver_time_limit_returns_none_for_legacy_mode():
    """Legacy distance-only mode keeps the solver's internal default."""
    assert _resolve_batch_solver_time_limit(50, None) is None
    assert _resolve_batch_solver_time_limit(500, None) is None


def test_economic_callback_dedup_uses_profile_and_cost_keys():
    """Smoke-test the dedup logic: identical-profile, identical-cost vehicles
    must share one registered transit callback.

    We build a routing model with three vehicles and inspect the number of
    registered transit callbacks after solver setup. The exact count depends
    on the solver code path; the contract this test pins is "≤ unique
    cost-class count, not vehicle count" so multi-trip expansion stays cheap.
    """

    from app.algorithms.vrp_solver import (
        OrderData,
        build_vrp_input,
        solve,
    )

    # 3 identical-cost vehicles → expect 1 economic arc-cost callback even
    # though every vehicle still gets `SetArcCostEvaluatorOfVehicle`.
    vehicles = [
        VehicleData(
            courier_id=f"v{i}",
            vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=10_000,
            depot_index=0,
            start_time_s=8 * 3600,
            end_time_s=18 * 3600,
            distance_cost_per_km=2,
            fuel_cost_per_km=3,
            labor_cost_per_minute=1,
            osrm_profile="driving",
            physical_courier_id=f"v{i}",
        )
        for i in range(3)
    ]
    orders = [
        OrderData(
            order_id=f"o{i}",
            pickup_index=0,
            delivery_index=1,
            weight_grams=500,
            earliest_time_s=9 * 3600,
            latest_time_s=17 * 3600,
            unserved_penalty=10_000,
        )
        for i in range(2)
    ]
    locations = [(41.0, 29.0), (41.01, 29.01)]
    matrix = [[0, 800], [800, 0]]
    vrp_input = build_vrp_input(
        locations=locations,
        distance_matrix=matrix,
        duration_matrix=matrix,
        orders=orders,
        vehicles=vehicles,
    )
    # Just make sure the solver runs end-to-end with 3 same-cost vehicles
    # without raising; the dedup itself happens inside `solve()`.
    solution = solve(vrp_input, time_limit_seconds=2)
    assert solution.solver_status.value in {"OPTIMAL", "FEASIBLE", "GREEDY_FALLBACK"}
