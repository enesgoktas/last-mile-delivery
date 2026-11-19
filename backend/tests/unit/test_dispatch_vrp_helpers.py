import uuid
from datetime import datetime
from datetime import time as dt_time
from types import SimpleNamespace

from app.algorithms.vrp_solver import (
    EconomicCostBreakdown,
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VehicleData,
    VRPSolution,
)
from app.models.enums import FuelType, OrderPriority, OrderType, VehicleType
from app.schemas.operations_policy import OptimizationSettings
from app.services.dispatch_vrp_helpers import (
    build_location_index,
    build_vrp_data_structures,
    expand_virtual_vehicles,
    should_skip_multi_trip,
    stitch_multi_trip_routes,
)


def _order(
    *,
    order_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    weight_kg: float = 1.0,
) -> SimpleNamespace:
    now = datetime(2026, 4, 27, 9, 0, 0)
    return SimpleNamespace(
        id=order_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        type=OrderType.SCHEDULED,
        priority=OrderPriority.NORMAL,
        pickup_lat=41.0,
        pickup_lon=29.0,
        delivery_lat=41.1,
        delivery_lon=29.1,
        weight_kg=weight_kg,
        is_fragile=False,
        is_temp_sensitive=False,
        earliest_pickup=None,
        latest_pickup=None,
        earliest_delivery=now,
        latest_delivery=now.replace(hour=17),
        required_vehicle_tags=[],
    )


def _courier(
    *,
    courier_id: uuid.UUID | None = None,
    vehicle_type: VehicleType = VehicleType.MOTORCYCLE,
    vehicle_spec: SimpleNamespace | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=courier_id or uuid.uuid4(),
        vehicle_type=vehicle_type,
        max_weight_kg=50,
        max_volume_cm3=None,
        max_packages=None,
        constraints={},
        depot_lat=41.0,
        depot_lon=29.0,
        working_hours_start=dt_time(8, 0),
        working_hours_end=dt_time(18, 0),
        vehicle_spec=vehicle_spec,
        vehicle_tags=[],
    )


def _global_constraints() -> dict:
    return {
        "service_time_minutes": 5,
        "max_weight_kg": 50.0,
        "max_route_distance_km": None,
        "max_volume_cm3": None,
        "max_packages": None,
        "max_stops_per_route": 50,
        "max_shift_hours": None,
        "break_required": False,
        "break_duration_minutes": 30,
        "break_earliest_offset_hours": 3.0,
        "break_latest_offset_hours": 6.0,
        "allow_fragile": True,
        "allow_temp_sensitive": True,
    }


def test_build_vrp_uses_vehicle_type_fuel_default_when_spec_missing():
    order = _order()
    courier = _courier(vehicle_type=VehicleType.TRUCK)
    locations, loc_index = build_location_index([order], [courier])
    settings = OptimizationSettings(
        economic_batch_optimizer_enabled=True,
        fuel_priority_multiplier=1.0,
    )

    _, vehicles = build_vrp_data_structures(
        [order],
        [courier],
        loc_index,
        _global_constraints(),
        {},
        settings,
    )

    assert len(locations) == 2
    assert vehicles[0].fuel_cost_per_km == 8
    assert vehicles[0].fuel_efficiency_km_per_liter == 5.0


def test_build_vrp_vehicle_spec_overrides_type_fuel_default():
    order = _order()
    spec = SimpleNamespace(
        fuel_type=FuelType.GASOLINE,
        fuel_efficiency_km_per_liter=10.0,
        co2_emissions_g_per_km=180.0,
    )
    courier = _courier(vehicle_type=VehicleType.MOTORCYCLE, vehicle_spec=spec)
    _, loc_index = build_location_index([order], [courier])
    settings = OptimizationSettings(
        economic_batch_optimizer_enabled=True,
        fuel_priority_multiplier=1.0,
    )

    _, vehicles = build_vrp_data_structures(
        [order],
        [courier],
        loc_index,
        _global_constraints(),
        {},
        settings,
    )

    assert vehicles[0].fuel_cost_per_km == 4
    assert vehicles[0].fuel_efficiency_km_per_liter == 10.0
    assert vehicles[0].co2_g_per_km == 180.0


def test_build_vrp_applies_fuel_priority_multiplier():
    """fuel_priority_multiplier scales the derived cost: 0 → off, 1 → normal, 2 → strong."""
    order = _order()
    courier = _courier(vehicle_type=VehicleType.TRUCK)
    _, loc_index = build_location_index([order], [courier])

    def _build(multiplier: float) -> int:
        _, vehicles = build_vrp_data_structures(
            [order],
            [courier],
            loc_index,
            _global_constraints(),
            {},
            OptimizationSettings(
                economic_batch_optimizer_enabled=True,
                fuel_priority_multiplier=multiplier,
            ),
        )
        return vehicles[0].fuel_cost_per_km

    # TRUCK default: 5 km/L, diesel @ 38 → 38/5 = 7.6 → round(8)
    # × 0  → 0  (fuel ignored)
    # × 1  → 8  (normal fuel-aware)
    # × 2  → 16 (strong fuel preference)
    assert _build(0.0) == 0
    assert _build(1.0) == 8
    assert _build(2.0) == 16


def _vehicle(
    *,
    courier_id: str = "courier-1",
    trip_index: int = 0,
    physical_courier_id: str = "courier-1",
    start_time_s: int = 8 * 3600,
    end_time_s: int = 16 * 3600,
    hard_end_time_s: int | None = 17 * 3600,
    fixed_route_cost: int = 500,
) -> VehicleData:
    return VehicleData(
        courier_id=courier_id,
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=10_000,
        depot_index=0,
        start_time_s=start_time_s,
        end_time_s=end_time_s,
        hard_end_time_s=hard_end_time_s,
        trip_index=trip_index,
        physical_courier_id=physical_courier_id,
        fixed_route_cost=fixed_route_cost,
        distance_cost_per_km=10,
        labor_cost_per_minute=2,
        fuel_cost_per_km=4,
        overtime_cost_per_minute=3,
        overtime_penalty_per_minute=7,
    )


def _planned_route(
    *,
    courier_id: str,
    stop_suffix: str,
    start_time_s: int,
    end_time_s: int,
    fixed_route_cost: int,
) -> PlannedRoute:
    return PlannedRoute(
        courier_id=courier_id,
        stops=[
            PlannedStop(
                order_id=f"order-{stop_suffix}",
                stop_type="PICKUP",
                location_index=1,
                lat=41.0,
                lon=29.0,
                arrival_time_s=start_time_s + 300,
                departure_time_s=start_time_s + 600,
            ),
            PlannedStop(
                order_id=f"order-{stop_suffix}",
                stop_type="DELIVERY",
                location_index=2,
                lat=41.01,
                lon=29.01,
                arrival_time_s=end_time_s - 300,
                departure_time_s=end_time_s,
            ),
        ],
        total_distance_m=12_000,
        total_duration_s=end_time_s - start_time_s,
        route_start_time_s=start_time_s,
        route_end_time_s=end_time_s,
        cost_breakdown=EconomicCostBreakdown(
            total_generalized_cost=200 + fixed_route_cost,
            distance_cost=120,
            labor_cost=50,
            fuel_cost=30,
            fixed_route_cost=fixed_route_cost,
            active_vehicle_count=1,
        ),
    )


def test_expand_virtual_vehicles_charges_fixed_cost_once_and_preserves_local_hard_caps():
    expanded = expand_virtual_vehicles(
        [_vehicle()],
        {"max_trips_per_day": 2, "depot_reload_time_minutes": 30},
    )

    assert len(expanded) == 2
    assert expanded[0].fixed_route_cost == 500
    assert expanded[1].fixed_route_cost == 0
    assert expanded[0].hard_end_time_s == expanded[1].start_time_s
    assert expanded[1].hard_end_time_s == 17 * 3600


def test_windowed_and_reverse_orders_do_not_disable_multi_trip_globally():
    windowed_reverse_order = _order()
    windowed_reverse_order.type = OrderType.REVERSE
    windowed_reverse_order.earliest_pickup = datetime(2026, 4, 27, 8, 0, 0)
    windowed_reverse_order.latest_pickup = datetime(2026, 4, 27, 10, 0, 0)

    assert should_skip_multi_trip(
        [windowed_reverse_order],
        {"max_trips_per_day": 3},
    ) is False


def test_multi_trip_skips_when_policy_allows_only_one_trip():
    assert should_skip_multi_trip([], {"max_trips_per_day": 1}) is True


def test_stitch_multi_trip_routes_recomputes_physical_cost_breakdown():
    virtual_vehicles = [
        _vehicle(courier_id="courier-1__trip0", trip_index=0, fixed_route_cost=500),
        _vehicle(
            courier_id="courier-1__trip1",
            trip_index=1,
            start_time_s=12 * 3600,
            end_time_s=16 * 3600,
            fixed_route_cost=0,
        ),
    ]
    raw_solution = VRPSolution(
        routes=[
            _planned_route(
                courier_id="courier-1__trip0",
                stop_suffix="a",
                start_time_s=8 * 3600,
                end_time_s=10 * 3600,
                fixed_route_cost=500,
            ),
            _planned_route(
                courier_id="courier-1__trip1",
                stop_suffix="b",
                start_time_s=12 * 3600,
                end_time_s=14 * 3600,
                fixed_route_cost=0,
            ),
        ],
        unassigned_orders=["late-order"],
        total_distance_m=24_000,
        total_duration_s=14_400,
        solver_status=SolverStatus.FEASIBLE,
        cost_breakdown=EconomicCostBreakdown(
            total_generalized_cost=1_100,
            distance_cost=240,
            labor_cost=100,
            fuel_cost=60,
            fixed_route_cost=500,
            unserved_penalty=200,
            active_vehicle_count=2,
        ),
    )

    stitched = stitch_multi_trip_routes(
        raw_solution,
        virtual_vehicles,
        [(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
    )

    assert len(stitched.routes) == 1
    stitched_route = stitched.routes[0]
    assert stitched_route.courier_id == "courier-1"
    assert stitched_route.route_start_time_s == 8 * 3600
    assert stitched_route.route_end_time_s == 14 * 3600
    assert stitched_route.cost_breakdown is not None
    assert stitched_route.cost_breakdown.fixed_route_cost == 500
    assert stitched_route.cost_breakdown.active_vehicle_count == 1
    assert stitched.cost_breakdown is not None
    assert stitched.cost_breakdown.fixed_route_cost == 500
    assert stitched.cost_breakdown.unserved_penalty == 200
    assert stitched.cost_breakdown.active_vehicle_count == 1
