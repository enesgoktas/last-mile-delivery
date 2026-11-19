from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from time import perf_counter

from app.algorithms.vrp_solver import (
    OrderData,
    SolverStatus,
    VehicleData,
    VRPInput,
    VRPSolution,
    build_vrp_input,
    solve,
)
from app.models.enums import VehicleType


@dataclass(frozen=True, slots=True)
class BenchmarkExpectation:
    max_total_distance_m: int
    max_unassigned_orders: int
    expected_assignments: dict[str, set[str]]
    max_elapsed_ms: float | None = None
    expected_unassigned_orders: set[str] | None = None
    disallowed_statuses: tuple[SolverStatus, ...] = (
        SolverStatus.ERROR,
        SolverStatus.GREEDY_FALLBACK,
        SolverStatus.NOT_FOUND,
    )


@dataclass(frozen=True, slots=True)
class BenchmarkScenario:
    name: str
    description: str
    vrp_input: VRPInput
    expectation: BenchmarkExpectation
    solver_time_limit_s: int = 2


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    name: str
    elapsed_ms: float
    solver_status: SolverStatus
    total_distance_m: int
    total_duration_s: int
    assigned_orders: int
    unassigned_orders: list[str]
    route_assignments: dict[str, list[str]]


def _elapsed_budget_ms(time_limit_seconds: int) -> float:
    return (time_limit_seconds * 1000) + 750


def _clustered_order_coordinates(
    *,
    cluster_index: int,
    order_index: int,
    cluster_spacing: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    cluster_origin_x = cluster_index * cluster_spacing
    lane_offset = (order_index % 3) * 0.45
    pickup = (
        cluster_origin_x + 0.8 + (order_index * 0.55),
        lane_offset,
    )
    delivery = (
        pickup[0] + 0.45,
        pickup[1] + 0.2,
    )
    return pickup, delivery


def _euclidean_distance_matrix(
    coordinates: list[tuple[float, float]],
    *,
    metres_per_unit: int = 1_000,
) -> list[list[int]]:
    matrix: list[list[int]] = []
    for from_lat, from_lon in coordinates:
        row: list[int] = []
        for to_lat, to_lon in coordinates:
            if (from_lat, from_lon) == (to_lat, to_lon):
                row.append(0)
                continue
            row.append(int(round(hypot(to_lat - from_lat, to_lon - from_lon) * metres_per_unit)))
        matrix.append(row)
    return matrix


def _duration_matrix(
    distance_matrix: list[list[int]],
    *,
    metres_per_second: int = 8,
) -> list[list[int]]:
    return [
        [0 if value == 0 else max(60, int(round(value / metres_per_second))) for value in row]
        for row in distance_matrix
    ]


def _wide_window_order(
    *,
    order_id: str,
    pickup_index: int,
    delivery_index: int,
    weight_grams: int = 5_000,
    is_fragile: bool = False,
) -> OrderData:
    return OrderData(
        order_id=order_id,
        pickup_index=pickup_index,
        delivery_index=delivery_index,
        weight_grams=weight_grams,
        is_fragile=is_fragile,
        is_temp_sensitive=False,
        earliest_time_s=0,
        latest_time_s=86_400,
        service_time_s=300,
    )


def _vehicle(
    *,
    courier_id: str,
    vehicle_type: VehicleType,
    depot_index: int,
) -> VehicleData:
    return VehicleData(
        courier_id=courier_id,
        vehicle_type=vehicle_type,
        capacity_grams=50_000,
        depot_index=depot_index,
        start_time_s=0,
        end_time_s=86_400,
    )


def _clustered_city_scenario(
    *,
    name: str,
    description: str,
    cluster_order_counts: list[int],
    cluster_spacing: float,
    max_total_distance_m: int,
    max_elapsed_ms: float,
    solver_time_limit_s: int,
) -> BenchmarkScenario:
    coordinates: list[tuple[float, float]] = []
    orders: list[OrderData] = []
    vehicles: list[VehicleData] = []
    expected_assignments: dict[str, set[str]] = {}

    for cluster_index, order_count in enumerate(cluster_order_counts, start=1):
        depot_index = len(coordinates)
        coordinates.append(((cluster_index - 1) * cluster_spacing, 0.0))
        courier_id = f"courier_{cluster_index}"
        vehicles.append(
            _vehicle(
                courier_id=courier_id,
                vehicle_type=VehicleType.CAR_VAN,
                depot_index=depot_index,
            )
        )
        cluster_order_ids: set[str] = set()
        for order_offset in range(order_count):
            pickup, delivery = _clustered_order_coordinates(
                cluster_index=cluster_index - 1,
                order_index=order_offset,
                cluster_spacing=cluster_spacing,
            )
            pickup_index = len(coordinates)
            coordinates.append(pickup)
            delivery_index = len(coordinates)
            coordinates.append(delivery)
            order_id = f"c{cluster_index}_{order_offset + 1}"
            cluster_order_ids.add(order_id)
            orders.append(
                _wide_window_order(
                    order_id=order_id,
                    pickup_index=pickup_index,
                    delivery_index=delivery_index,
                )
            )
        expected_assignments[courier_id] = cluster_order_ids

    distance_matrix = _euclidean_distance_matrix(coordinates)
    duration_matrix = _duration_matrix(distance_matrix)
    return BenchmarkScenario(
        name=name,
        description=description,
        vrp_input=build_vrp_input(
            locations=coordinates,
            distance_matrix=distance_matrix,
            duration_matrix=duration_matrix,
            orders=orders,
            vehicles=vehicles,
        ),
        expectation=BenchmarkExpectation(
            max_total_distance_m=max_total_distance_m,
            max_unassigned_orders=0,
            expected_assignments=expected_assignments,
            max_elapsed_ms=max_elapsed_ms,
        ),
        solver_time_limit_s=solver_time_limit_s,
    )


def _balanced_clustered_assignments() -> BenchmarkScenario:
    coordinates = [
        (0.0, 0.0),   # courier_a depot
        (10.0, 0.0),  # courier_b depot
        (0.8, 0.2),   # a1 pickup
        (1.6, 0.2),   # a1 delivery
        (1.2, 1.0),   # a2 pickup
        (2.0, 1.0),   # a2 delivery
        (9.2, 0.2),   # b1 pickup
        (8.4, 0.2),   # b1 delivery
        (8.8, 1.0),   # b2 pickup
        (8.0, 1.0),   # b2 delivery
    ]
    distance_matrix = _euclidean_distance_matrix(coordinates)
    duration_matrix = _duration_matrix(distance_matrix)
    orders = [
        _wide_window_order(order_id="a1", pickup_index=2, delivery_index=3),
        _wide_window_order(order_id="a2", pickup_index=4, delivery_index=5),
        _wide_window_order(order_id="b1", pickup_index=6, delivery_index=7),
        _wide_window_order(order_id="b2", pickup_index=8, delivery_index=9),
    ]
    vehicles = [
        _vehicle(courier_id="courier_a", vehicle_type=VehicleType.CAR_VAN, depot_index=0),
        _vehicle(courier_id="courier_b", vehicle_type=VehicleType.CAR_VAN, depot_index=1),
    ]
    return BenchmarkScenario(
        name="balanced_clustered_assignments",
        description="Two couriers should keep to their local cluster instead of crossing the city.",
        vrp_input=build_vrp_input(
            locations=coordinates,
            distance_matrix=distance_matrix,
            duration_matrix=duration_matrix,
            orders=orders,
            vehicles=vehicles,
        ),
        expectation=BenchmarkExpectation(
            max_total_distance_m=12_000,
            max_unassigned_orders=0,
            expected_assignments={
                "courier_a": {"a1", "a2"},
                "courier_b": {"b1", "b2"},
            },
            max_elapsed_ms=_elapsed_budget_ms(2),
        ),
    )


def _vehicle_capability_split() -> BenchmarkScenario:
    coordinates = [
        (0.0, 0.0),    # motorcycle depot
        (10.0, 0.0),   # van depot
        (0.6, 0.0),    # standard pickup
        (1.2, 0.0),    # standard delivery
        (9.4, 0.0),    # fragile pickup
        (8.6, 0.0),    # fragile delivery
        (10.8, 0.4),   # van-side pickup
        (11.6, 0.8),   # van-side delivery
    ]
    distance_matrix = _euclidean_distance_matrix(coordinates)
    duration_matrix = _duration_matrix(distance_matrix)
    orders = [
        _wide_window_order(order_id="standard", pickup_index=2, delivery_index=3),
        _wide_window_order(
            order_id="fragile",
            pickup_index=4,
            delivery_index=5,
            is_fragile=True,
        ),
        _wide_window_order(order_id="van_side", pickup_index=6, delivery_index=7),
    ]
    vehicles = [
        _vehicle(courier_id="courier_moto", vehicle_type=VehicleType.MOTORCYCLE, depot_index=0),
        _vehicle(courier_id="courier_van", vehicle_type=VehicleType.CAR_VAN, depot_index=1),
    ]
    return BenchmarkScenario(
        name="vehicle_capability_split",
        description="Fragile work must stay off motorcycles while standard work still stays local.",
        vrp_input=build_vrp_input(
            locations=coordinates,
            distance_matrix=distance_matrix,
            duration_matrix=duration_matrix,
            orders=orders,
            vehicles=vehicles,
        ),
        expectation=BenchmarkExpectation(
            max_total_distance_m=11_000,
            max_unassigned_orders=0,
            expected_assignments={
                "courier_moto": {"standard"},
                "courier_van": {"fragile", "van_side"},
            },
            max_elapsed_ms=_elapsed_budget_ms(2),
        ),
    )


def _tight_window_single_drop() -> BenchmarkScenario:
    coordinates = [
        (0.0, 0.0),   # depot
        (0.8, 0.0),   # feasible pickup
        (1.6, 0.0),   # feasible delivery
        (8.0, 0.0),   # impossible pickup
        (9.0, 0.0),   # impossible delivery
    ]
    distance_matrix = _euclidean_distance_matrix(coordinates)
    duration_matrix = _duration_matrix(distance_matrix)
    orders = [
        _wide_window_order(order_id="feasible", pickup_index=1, delivery_index=2),
        OrderData(
            order_id="impossible",
            pickup_index=3,
            delivery_index=4,
            weight_grams=5_000,
            is_fragile=False,
            is_temp_sensitive=False,
            earliest_time_s=0,
            latest_time_s=600,
            service_time_s=300,
            pickup_earliest_time_s=0,
            pickup_latest_time_s=300,
        ),
    ]
    vehicles = [
        _vehicle(courier_id="courier_single", vehicle_type=VehicleType.CAR_VAN, depot_index=0),
    ]
    return BenchmarkScenario(
        name="tight_window_single_drop",
        description="Solver should still keep the feasible order instead of failing the whole batch.",
        vrp_input=build_vrp_input(
            locations=coordinates,
            distance_matrix=distance_matrix,
            duration_matrix=duration_matrix,
            orders=orders,
            vehicles=vehicles,
        ),
        expectation=BenchmarkExpectation(
            max_total_distance_m=4_000,
            max_unassigned_orders=1,
            expected_assignments={"courier_single": {"feasible"}},
            expected_unassigned_orders={"impossible"},
            max_elapsed_ms=_elapsed_budget_ms(2),
        ),
    )


def _twelve_order_clustered_city() -> BenchmarkScenario:
    return _clustered_city_scenario(
        name="twelve_order_clustered_city",
        description="Twelve same-day orders stay in local courier clusters within the benchmark latency budget.",
        cluster_order_counts=[3, 3, 3, 3],
        cluster_spacing=15.0,
        max_total_distance_m=25_000,
        max_elapsed_ms=_elapsed_budget_ms(2),
        solver_time_limit_s=2,
    )


def _twenty_four_order_clustered_city() -> BenchmarkScenario:
    return _clustered_city_scenario(
        name="twenty_four_order_clustered_city",
        description="Twenty-four orders across six courier clusters still solve cleanly without fallback.",
        cluster_order_counts=[4, 4, 4, 4, 4, 4],
        cluster_spacing=16.0,
        max_total_distance_m=60_000,
        max_elapsed_ms=_elapsed_budget_ms(4),
        solver_time_limit_s=4,
    )


def get_vrp_benchmark_scenarios() -> list[BenchmarkScenario]:
    return [
        _balanced_clustered_assignments(),
        _vehicle_capability_split(),
        _tight_window_single_drop(),
        _twelve_order_clustered_city(),
        _twenty_four_order_clustered_city(),
    ]


def summarize_route_assignments(solution: VRPSolution) -> dict[str, list[str]]:
    assignments: dict[str, set[str]] = {}
    for route in solution.routes:
        order_ids = assignments.setdefault(route.courier_id, set())
        for stop in route.stops:
            order_ids.add(stop.order_id)
    return {
        courier_id: sorted(order_ids)
        for courier_id, order_ids in assignments.items()
    }


def run_benchmark_scenario(scenario: BenchmarkScenario) -> BenchmarkResult:
    started = perf_counter()
    solution = solve(
        scenario.vrp_input,
        time_limit_seconds=scenario.solver_time_limit_s,
    )
    elapsed_ms = (perf_counter() - started) * 1000
    return BenchmarkResult(
        name=scenario.name,
        elapsed_ms=elapsed_ms,
        solver_status=solution.solver_status,
        total_distance_m=solution.total_distance_m,
        total_duration_s=solution.total_duration_s,
        assigned_orders=sum(len(route_order_ids) for route_order_ids in summarize_route_assignments(solution).values()),
        unassigned_orders=sorted(solution.unassigned_orders),
        route_assignments=summarize_route_assignments(solution),
    )


def evaluate_benchmark_result(
    scenario: BenchmarkScenario,
    result: BenchmarkResult,
) -> list[str]:
    expectation = scenario.expectation
    failures: list[str] = []

    if result.solver_status in expectation.disallowed_statuses:
        failures.append(f"solver status regressed to {result.solver_status}")
    if result.total_distance_m > expectation.max_total_distance_m:
        failures.append(
            "total distance exceeded threshold: "
            f"{result.total_distance_m} > {expectation.max_total_distance_m}"
        )
    if len(result.unassigned_orders) > expectation.max_unassigned_orders:
        failures.append(
            "unassigned order count exceeded threshold: "
            f"{len(result.unassigned_orders)} > {expectation.max_unassigned_orders}"
        )
    if expectation.max_elapsed_ms is not None and result.elapsed_ms > expectation.max_elapsed_ms:
        failures.append(
            "elapsed time exceeded threshold: "
            f"{round(result.elapsed_ms, 2)}ms > {expectation.max_elapsed_ms}ms"
        )
    if expectation.expected_unassigned_orders is not None:
        actual_unassigned = set(result.unassigned_orders)
        if actual_unassigned != expectation.expected_unassigned_orders:
            failures.append(
                "unassigned orders drifted: "
                f"{sorted(actual_unassigned)} != {sorted(expectation.expected_unassigned_orders)}"
            )

    actual_assignments = {
        courier_id: set(order_ids)
        for courier_id, order_ids in result.route_assignments.items()
    }
    if actual_assignments != expectation.expected_assignments:
        failures.append(
            "assignments drifted: "
            f"{actual_assignments} != {expectation.expected_assignments}"
        )

    return failures
