from __future__ import annotations

import math
import time

from app.algorithms.human_dispatcher_baseline import (
    build_sweep_zones,
    solve_human_dispatcher_baseline,
)
from app.algorithms.vrp_solver import OrderData, VehicleData, VRPInput
from app.models.enums import FuelType, OrderPriority, VehicleType

HOUR = 3600


def _matrix(locations: list[tuple[float, float]], *, meters_per_degree: int = 100_000):
    distances: list[list[int]] = []
    durations: list[list[int]] = []
    for from_lat, from_lon in locations:
        d_row: list[int] = []
        t_row: list[int] = []
        for to_lat, to_lon in locations:
            distance = int(round(math.hypot(to_lat - from_lat, to_lon - from_lon) * meters_per_degree))
            d_row.append(distance)
            t_row.append(max(0, distance // 10))
        distances.append(d_row)
        durations.append(t_row)
    return distances, durations


def _order(
    order_id: str,
    *,
    pickup_index: int = 0,
    delivery_index: int = 1,
    weight_grams: int = 1_000,
    latest: int = 18 * HOUR,
    hard_latest: int | None = 20 * HOUR,
    priority: OrderPriority = OrderPriority.NORMAL,
    fragile: bool = False,
    tags: frozenset[str] = frozenset(),
) -> OrderData:
    return OrderData(
        order_id=order_id,
        pickup_index=pickup_index,
        delivery_index=delivery_index,
        weight_grams=weight_grams,
        is_fragile=fragile,
        required_vehicle_tags=tags,
        earliest_time_s=8 * HOUR,
        latest_time_s=latest,
        hard_latest_time_s=hard_latest,
        pickup_earliest_time_s=0,
        pickup_latest_time_s=18 * HOUR,
        service_time_s=0,
        priority=priority,
        unserved_penalty=1_000,
        lateness_penalty_per_minute=10,
    )


def _vehicle(
    courier_id: str = "vehicle-1",
    *,
    depot_index: int = 0,
    vehicle_type: VehicleType = VehicleType.CAR_VAN,
    capacity_grams: int = 50_000,
    max_stops: int = 50,
    tags: frozenset[str] = frozenset(),
    osrm_profile: str = "driving",
    break_duration_s: int = 0,
) -> VehicleData:
    return VehicleData(
        courier_id=courier_id,
        vehicle_type=vehicle_type,
        capacity_grams=capacity_grams,
        depot_index=depot_index,
        start_time_s=8 * HOUR,
        end_time_s=20 * HOUR,
        hard_end_time_s=22 * HOUR,
        break_duration_s=break_duration_s,
        break_earliest_s=11 * HOUR,
        break_latest_s=14 * HOUR,
        max_stops=max_stops,
        allow_fragile=True,
        allow_temp_sensitive=True,
        vehicle_tags=tags,
        fuel_type=FuelType.DIESEL,
        fuel_efficiency_km_per_liter=10.0,
        co2_g_per_km=100.0,
        distance_cost_per_km=10,
        fuel_cost_per_km=5,
        labor_cost_per_minute=2,
        fixed_route_cost=25,
        osrm_profile=osrm_profile,
    )


def _input(
    locations: list[tuple[float, float]],
    orders: list[OrderData],
    vehicles: list[VehicleData],
    *,
    distance_matrices: dict[str, list[list[int]]] | None = None,
    duration_matrices: dict[str, list[list[int]]] | None = None,
) -> VRPInput:
    distances, durations = _matrix(locations)
    return VRPInput(
        locations=locations,
        distance_matrix=distances,
        duration_matrix=durations,
        orders=orders,
        vehicles=vehicles,
        distance_matrices=distance_matrices,
        duration_matrices=duration_matrices,
    )


def test_build_sweep_zones_is_deterministic() -> None:
    locations = [
        (41.0, 29.0),
        (41.01, 29.0),
        (41.0, 29.01),
        (40.99, 29.0),
        (41.0, 28.99),
        (41.02, 29.0),
        (41.0, 29.02),
        (40.98, 29.0),
        (41.0, 28.98),
    ]
    orders = [_order(f"order-{idx}", delivery_index=idx) for idx in range(1, len(locations))]
    vrp_input = _input(locations, orders, [_vehicle("v1"), _vehicle("v2")])

    first = build_sweep_zones(vrp_input)
    second = build_sweep_zones(vrp_input)

    assert [zone.order_ids for zone in first] == [zone.order_ids for zone in second]
    assert len(first) == 2
    assert sorted(order_id for zone in first for order_id in zone.order_ids) == [
        order.order_id for order in orders
    ]


def test_assigns_feasible_orders_and_preloads_depot_pickups() -> None:
    locations = [(41.0, 29.0), (41.01, 29.0), (41.02, 29.0)]
    orders = [
        _order("order-1", delivery_index=1),
        _order("order-2", delivery_index=2, priority=OrderPriority.HIGH),
    ]
    solution = solve_human_dispatcher_baseline(_input(locations, orders, [_vehicle(max_stops=4)]))

    assert solution.unassigned_orders == []
    route = solution.routes[0]
    assert [stop.stop_type for stop in route.stops[:2]] == ["PICKUP", "PICKUP"]
    assert {stop.order_id for stop in route.stops if stop.stop_type == "DELIVERY"} == {
        "order-1",
        "order-2",
    }


def test_leaves_capacity_and_time_impossible_orders_unassigned() -> None:
    locations = [(41.0, 29.0), (41.5, 29.5), (41.6, 29.6)]
    orders = [
        _order("too-heavy", delivery_index=1, weight_grams=10_000),
        _order("too-tight", delivery_index=2, latest=8 * HOUR + 1, hard_latest=8 * HOUR + 1),
    ]
    solution = solve_human_dispatcher_baseline(
        _input(locations, orders, [_vehicle(capacity_grams=1_000)])
    )

    assert solution.routes == []
    assert solution.unassigned_orders == ["too-tight", "too-heavy"]


def test_required_vehicle_tags_drive_assignment() -> None:
    locations = [(41.0, 29.0), (41.01, 29.0)]
    orders = [_order("cold-chain", delivery_index=1, tags=frozenset({"cold"}))]
    vehicles = [
        _vehicle("plain"),
        _vehicle("cold", tags=frozenset({"cold"})),
    ]
    solution = solve_human_dispatcher_baseline(_input(locations, orders, vehicles))

    assert solution.unassigned_orders == []
    assert solution.routes[0].courier_id == "cold"


def test_fragile_orders_are_not_put_on_motorcycles() -> None:
    locations = [(41.0, 29.0), (41.01, 29.0)]
    orders = [_order("fragile", delivery_index=1, fragile=True)]
    vehicles = [_vehicle("moto", vehicle_type=VehicleType.MOTORCYCLE)]
    solution = solve_human_dispatcher_baseline(_input(locations, orders, vehicles))

    assert solution.routes == []
    assert solution.unassigned_orders == ["fragile"]


def test_repair_pass_moves_missed_zone_work_to_available_capacity() -> None:
    locations = [
        (41.0, 29.0),
        (41.01, 29.0),
        (41.011, 29.0),
        (40.99, 29.0),
    ]
    orders = [
        _order("zone-a-1", delivery_index=1),
        _order("zone-a-2", delivery_index=2),
        _order("zone-b", delivery_index=3),
    ]
    vehicles = [
        _vehicle("small", max_stops=2),
        _vehicle("spare", max_stops=6),
    ]

    solution = solve_human_dispatcher_baseline(_input(locations, orders, vehicles))

    assert solution.unassigned_orders == []
    assigned_to_spare = {
        stop.order_id
        for route in solution.routes
        if route.courier_id == "spare"
        for stop in route.stops
        if stop.stop_type == "DELIVERY"
    }
    assert assigned_to_spare


def test_uses_vehicle_profile_specific_road_matrices_and_cost_math() -> None:
    locations = [(41.0, 29.0), (41.01, 29.0)]
    base_distances, base_durations = _matrix(locations)
    truck_distances = [[0, 1_000], [1_000, 0]]
    truck_durations = [[0, 100], [100, 0]]
    order = _order("truck-order", delivery_index=1)
    vehicle = _vehicle("truck", vehicle_type=VehicleType.TRUCK, osrm_profile="truck")
    vrp_input = VRPInput(
        locations=locations,
        distance_matrix=base_distances,
        duration_matrix=base_durations,
        orders=[order],
        vehicles=[vehicle],
        distance_matrices={"truck": truck_distances},
        duration_matrices={"truck": truck_durations},
    )

    solution = solve_human_dispatcher_baseline(vrp_input)

    route = solution.routes[0]
    assert route.total_distance_m == 2_000
    assert route.cost_breakdown is not None
    assert route.cost_breakdown.distance_cost == 20
    assert route.cost_breakdown.fuel_cost == 10
    assert route.cost_breakdown.estimated_fuel_liters == 0.2
    assert solution.cost_breakdown is not None
    assert solution.cost_breakdown.total_generalized_cost >= route.cost_breakdown.total_generalized_cost


def test_625_order_smoke_completes_without_becoming_the_bottleneck() -> None:
    locations = [(41.0, 29.0)]
    orders: list[OrderData] = []
    for idx in range(625):
        ring = idx // 25
        angle = (idx % 25) * (2 * math.pi / 25)
        locations.append((41.0 + 0.001 * ring * math.cos(angle), 29.0 + 0.001 * ring * math.sin(angle)))
        orders.append(
            _order(
                f"order-{idx:03d}",
                delivery_index=idx + 1,
                latest=23 * HOUR,
                hard_latest=24 * HOUR,
            )
        )
    vehicles = [
        _vehicle(f"vehicle-{idx:02d}", max_stops=60, capacity_grams=1_000_000)
        for idx in range(25)
    ]
    vrp_input = _input(locations, orders, vehicles)

    started = time.monotonic()
    solution = solve_human_dispatcher_baseline(vrp_input)
    elapsed = time.monotonic() - started

    assert solution.unassigned_orders == []
    assert len(solution.routes) <= 25
    assert elapsed < 8.0
