from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

from app.algorithms.vrp_solver import (
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VehicleData,
    VRPSolution,
)
from app.models.enums import FuelType, OrderPriority, OrderStatus, OrderType, VehicleType
from app.models.order import Order
from app.schemas.operations_policy import OptimizationSettings
from app.services.energy_price_service import EnergyPriceBasis
from app.services.route_scenario_service import (
    HUMAN_DISPATCHER_BASELINE_KEY,
    _scenario_sort_key,
    _solve_scenario_vrp,
    build_scenario_optimization_settings,
    generate_scenario_definitions,
    summarize_solution,
)


def test_generate_scenario_definitions_includes_baseline_then_five_public_combinations() -> None:
    definitions = generate_scenario_definitions()

    assert [item["key"] for item in definitions] == [
        HUMAN_DISPATCHER_BASELINE_KEY,
        "rich_alns_service_first",
        "rich_alns_fuel_aware",
        "rich_alns_efficient",
        "fuel_aware_full_fleet",
    ]
    assert definitions[0]["label"] == "Human dispatcher baseline"
    assert definitions[0]["routing_objective"] == "SERVICE_FIRST"
    assert definitions[0]["fleet_utilization"] == "FULL_FLEET"
    # The 4 non-baseline scenarios cover 3 objective/utilization combos
    assert {
        (item["routing_objective"], item["fleet_utilization"])
        for item in definitions[1:]
    } == {
        ("SERVICE_FIRST", "FULL_FLEET"),
        ("FUEL_AWARE", "FULL_FLEET"),
        ("FUEL_AWARE", "EFFICIENT"),
    }
    # All three rich_alns scenarios carry the correct solver_kind
    rich_alns_keys = {"rich_alns_service_first", "rich_alns_fuel_aware", "rich_alns_efficient"}
    for d in definitions:
        if d["key"] in rich_alns_keys:
            assert d["solver_kind"] == "rich_alns"


def test_scenario_sort_key_keeps_baseline_first_for_unordered_relationships() -> None:
    scenarios = [
        SimpleNamespace(key="service_first_efficient"),
        SimpleNamespace(key=HUMAN_DISPATCHER_BASELINE_KEY),
        SimpleNamespace(key="fuel_aware_full_fleet"),
    ]

    scenarios.sort(key=_scenario_sort_key)

    assert [scenario.key for scenario in scenarios] == [
        HUMAN_DISPATCHER_BASELINE_KEY,
        "fuel_aware_full_fleet",
        "service_first_efficient",
    ]


def test_scenario_objective_mapping_keeps_realized_prices_available() -> None:
    base = OptimizationSettings(
        fuel_prices={
            FuelType.GASOLINE: 40.0,
            FuelType.DIESEL: 42.0,
            FuelType.LPG: 22.0,
            FuelType.ELECTRIC: 11.0,
            FuelType.HYBRID: 39.0,
        }
    )

    fuel_aware = build_scenario_optimization_settings(
        base_settings=base,
        routing_objective="FUEL_AWARE",
        fleet_utilization="FULL_FLEET",
        energy_prices={FuelType.DIESEL: 50.0},
    )
    service_first = build_scenario_optimization_settings(
        base_settings=base,
        routing_objective="SERVICE_FIRST",
        fleet_utilization="EFFICIENT",
        energy_prices={FuelType.DIESEL: 50.0},
    )

    assert fuel_aware.preset_key == "COST_SAVER"
    assert fuel_aware.fuel_priority_multiplier == 1.0
    assert fuel_aware.fuel_prices[FuelType.DIESEL] == 50.0
    assert service_first.preset_key == "SERVICE_FIRST"
    assert service_first.fuel_priority_multiplier == 0.0
    assert service_first.fuel_prices[FuelType.DIESEL] == 50.0
    assert service_first.fleet_utilization == "EFFICIENT"


async def test_baseline_scenario_branch_uses_human_solver(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_human_solver(**kwargs):
        calls.append("human")
        return "solution", "vrp_vehicles", "multi_trip_vehicles", [], {"selected_solver": "human"}

    async def fail_ortools_solver(**kwargs):
        raise AssertionError("baseline should not call OR-Tools scenario solver")

    monkeypatch.setattr(
        "app.services.route_scenario_service._solve_human_dispatcher_baseline_variant",
        fake_human_solver,
    )
    monkeypatch.setattr(
        "app.services.route_scenario_service._solve_batch_variant",
        fail_ortools_solver,
    )
    problem = {
        "orders": [],
        "couriers": [],
        "locations": [],
        "loc_index": {},
        "matrix": SimpleNamespace(distances=[]),
        "matrix_by_profile": {},
        "adjusted_matrix": SimpleNamespace(durations=[]),
        "adjusted_by_profile": {},
        "hourly_by_profile": {},
        "target_time": datetime(2026, 5, 4, 8, 0, 0),
        "global_constraints": {},
        "order_packages": {},
        "skip_multi_trip": True,
    }

    result = await _solve_scenario_vrp(
        None,
        scenario=SimpleNamespace(
            key=HUMAN_DISPATCHER_BASELINE_KEY,
            solver_kind="human_dispatcher",
            tenant_id=uuid.uuid4(),
            fleet_utilization="FULL_FLEET",
        ),
        problem=problem,
        scenario_settings=OptimizationSettings(),
    )

    assert calls == ["human"]
    assert result == (
        "solution",
        "vrp_vehicles",
        "multi_trip_vehicles",
        [],
        {"selected_solver": "human"},
    )


async def test_weighted_scenarios_reach_solver_with_distinct_controls(monkeypatch) -> None:
    calls: list[tuple[float, bool, str]] = []

    async def fake_batch_solver(**kwargs):
        settings = kwargs["optimization_settings"]
        calls.append((
            settings.fuel_priority_multiplier,
            kwargs["spread_fleet"],
            settings.fleet_utilization,
        ))
        return "solution", "vrp_vehicles", "multi_trip_vehicles", [], {"selected_solver": "ortools"}

    monkeypatch.setattr(
        "app.services.route_scenario_service._solve_batch_variant",
        fake_batch_solver,
    )
    problem = {
        "orders": [],
        "couriers": [],
        "locations": [],
        "loc_index": {},
        "matrix": SimpleNamespace(distances=[]),
        "matrix_by_profile": {},
        "adjusted_matrix": SimpleNamespace(durations=[]),
        "adjusted_by_profile": {},
        "hourly_by_profile": {},
        "target_time": datetime(2026, 5, 4, 8, 0, 0),
        "global_constraints": {},
        "order_packages": {},
        "skip_multi_trip": True,
    }
    base_settings = OptimizationSettings()

    for definition in generate_scenario_definitions()[1:]:
        scenario_settings = build_scenario_optimization_settings(
            base_settings=base_settings,
            routing_objective=definition["routing_objective"],
            fleet_utilization=definition["fleet_utilization"],
            energy_prices={},
        )
        await _solve_scenario_vrp(
            None,
            scenario=SimpleNamespace(
                key=definition["key"],
                solver_kind=definition["solver_kind"],
                tenant_id=uuid.uuid4(),
                fleet_utilization=definition["fleet_utilization"],
            ),
            problem=problem,
            scenario_settings=scenario_settings,
        )

    # rich_alns_fuel_aware and fuel_aware_full_fleet share (1.0, True, FULL_FLEET);
    # set() collapses duplicates leaving 3 distinct combinations.
    assert set(calls) == {
        (0.0, True, "FULL_FLEET"),
        (1.0, True, "FULL_FLEET"),
        (1.0, False, "EFFICIENT"),
    }


def test_summarize_solution_reports_actual_energy_cost_independent_of_objective() -> None:
    tenant_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        tenant_id=tenant_id,
        type=OrderType.ON_DEMAND,
        status=OrderStatus.PENDING,
        priority=OrderPriority.NORMAL,
        pickup_address="Depot, Istanbul",
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_address="Customer, Istanbul",
        delivery_lat=41.1,
        delivery_lon=29.1,
        weight_kg=1.0,
        earliest_delivery=datetime(2026, 5, 4, 8, 0, 0),
        latest_delivery=datetime(2026, 5, 4, 10, 0, 0),
        customer_email="customer@example.test",
        tracking_token="tok",
    )
    vehicle = VehicleData(
        courier_id=str(courier_id),
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=1000,
        fuel_type=FuelType.DIESEL,
        fuel_efficiency_km_per_liter=10.0,
        co2_g_per_km=100.0,
    )
    solution = VRPSolution(
        routes=[
            PlannedRoute(
                courier_id=str(courier_id),
                stops=[
                    PlannedStop(
                        order_id=str(order_id),
                        stop_type="DELIVERY",
                        location_index=1,
                        lat=41.1,
                        lon=29.1,
                        arrival_time_s=9 * 3600,
                        departure_time_s=9 * 3600 + 300,
                    )
                ],
                total_distance_m=10_000,
                total_duration_s=1800,
            )
        ],
        unassigned_orders=[],
        total_distance_m=10_000,
        total_duration_s=1800,
        solver_status=SolverStatus.FEASIBLE,
    )
    basis = EnergyPriceBasis(
        prices={FuelType.DIESEL: 40.0},
        sources=[
            {
                "fuel_type": "DIESEL",
                "unit": "LITER",
                "price_try": 40.0,
                "effective_date": "2026-05-04",
                "source_name": "TEST",
                "source_url": "https://example.test",
                "freshness_status": "FRESH",
                "province_code": "34",
                "province_name": "Istanbul",
            }
        ],
        freshness_status="FRESH",
        province_code="34",
        province_name="Istanbul",
    )

    summary = summarize_solution(
        solution=solution,
        orders=[order],
        vehicles=[vehicle],
        price_basis_by_courier={str(courier_id): basis},
    )

    assert summary.realized_energy_cost_try == 40.0
    assert summary.fuel_liters == 1.0
    assert summary.co2_kg == 1.0
    assert summary.on_time_rate == 1.0


def test_summarize_solution_does_not_count_multi_trip_depot_stops_as_orders() -> None:
    tenant_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        tenant_id=tenant_id,
        type=OrderType.ON_DEMAND,
        status=OrderStatus.PENDING,
        priority=OrderPriority.NORMAL,
        pickup_address="Depot, Istanbul",
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_address="Customer, Istanbul",
        delivery_lat=41.1,
        delivery_lon=29.1,
        weight_kg=1.0,
        earliest_delivery=datetime(2026, 5, 4, 8, 0, 0),
        latest_delivery=datetime(2026, 5, 4, 10, 0, 0),
        customer_email="customer@example.test",
        tracking_token="tok",
    )
    vehicle = VehicleData(
        courier_id=str(courier_id),
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=1000,
    )
    solution = VRPSolution(
        routes=[
            PlannedRoute(
                courier_id=str(courier_id),
                stops=[
                    PlannedStop(
                        order_id=str(order_id),
                        stop_type="DELIVERY",
                        location_index=1,
                        lat=41.1,
                        lon=29.1,
                        arrival_time_s=9 * 3600,
                        departure_time_s=9 * 3600,
                    ),
                    PlannedStop(
                        order_id="__DEPOT_RETURN__",
                        stop_type="DEPOT_RETURN",
                        location_index=0,
                        lat=41.0,
                        lon=29.0,
                        arrival_time_s=10 * 3600,
                        departure_time_s=10 * 3600,
                    ),
                    PlannedStop(
                        order_id="__DEPOT_RELOAD__",
                        stop_type="DEPOT_RELOAD",
                        location_index=0,
                        lat=41.0,
                        lon=29.0,
                        arrival_time_s=10 * 3600,
                        departure_time_s=10 * 3600 + 900,
                    ),
                ],
                total_distance_m=10_000,
                total_duration_s=1800,
            )
        ],
        unassigned_orders=[],
        total_distance_m=10_000,
        total_duration_s=1800,
        solver_status=SolverStatus.FEASIBLE,
    )
    basis = EnergyPriceBasis(
        prices={FuelType.DIESEL: 40.0},
        sources=[],
        freshness_status="FRESH",
    )

    summary = summarize_solution(
        solution=solution,
        orders=[order],
        vehicles=[vehicle],
        price_basis_by_courier={str(courier_id): basis},
    )

    assert summary.assigned_count == 1
    assert summary.unassigned_count == 0
