"""Tests for the greedy fallback solver."""

from app.algorithms.greedy_fallback import greedy_assign
from app.algorithms.vrp_solver import (
    OrderData,
    SolverStatus,
    VehicleData,
    VRPInput,
)
from app.models.enums import VehicleType


def _make_input(
    orders: list[OrderData],
    vehicles: list[VehicleData],
    locations: list[tuple[float, float]] | None = None,
    duration_matrix: list[list[int]] | None = None,
    distance_matrix: list[list[int]] | None = None,
) -> VRPInput:
    if locations is None:
        locations = [(0.0, 0.0)] * 10
    n = len(locations)
    if duration_matrix is None:
        duration_matrix = [[300] * n for _ in range(n)]
        for i in range(n):
            duration_matrix[i][i] = 0
    if distance_matrix is None:
        distance_matrix = [[1000] * n for _ in range(n)]
        for i in range(n):
            distance_matrix[i][i] = 0
    return VRPInput(
        locations=locations,
        distance_matrix=distance_matrix,
        duration_matrix=duration_matrix,
        orders=orders,
        vehicles=vehicles,
    )


def _order(order_id: str, pickup_idx: int = 1, delivery_idx: int = 2, weight: int = 1000, **kwargs) -> OrderData:
    defaults = {
        "order_id": order_id,
        "pickup_index": pickup_idx,
        "delivery_index": delivery_idx,
        "weight_grams": weight,
        "earliest_time_s": 28800,
        "latest_time_s": 64800,
        "service_time_s": 300,
    }
    defaults.update(kwargs)
    return OrderData(**defaults)


def _vehicle(courier_id: str = "c1", capacity: int = 50000, **kwargs) -> VehicleData:
    defaults = {
        "courier_id": courier_id,
        "vehicle_type": VehicleType.CAR_VAN,
        "capacity_grams": capacity,
        "depot_index": 0,
        "start_time_s": 28800,
        "end_time_s": 64800,
    }
    defaults.update(kwargs)
    return VehicleData(**defaults)


class TestGreedyBasic:
    def test_single_order_single_vehicle(self):
        result = greedy_assign(_make_input(
            orders=[_order("o1")],
            vehicles=[_vehicle()],
        ))
        assert result.solver_status == SolverStatus.GREEDY_FALLBACK
        assert len(result.routes) == 1
        assert len(result.routes[0].stops) == 2
        assert result.unassigned_orders == []
        assert result.routes[0].stops[0].stop_type == "PICKUP"
        assert result.routes[0].stops[1].stop_type == "DELIVERY"

    def test_multiple_orders(self):
        result = greedy_assign(_make_input(
            orders=[_order("o1", pickup_idx=1, delivery_idx=2),
                    _order("o2", pickup_idx=3, delivery_idx=4)],
            vehicles=[_vehicle()],
        ))
        assert len(result.unassigned_orders) == 0
        assert len(result.routes) == 1
        assert len(result.routes[0].stops) == 4

    def test_empty_orders(self):
        result = greedy_assign(_make_input(orders=[], vehicles=[_vehicle()]))
        assert result.solver_status == SolverStatus.NOT_FOUND
        assert len(result.routes) == 0

    def test_empty_vehicles(self):
        result = greedy_assign(_make_input(orders=[_order("o1")], vehicles=[]))
        assert result.solver_status == SolverStatus.NOT_FOUND
        assert len(result.unassigned_orders) == 1


class TestGreedyConstraints:
    def test_capacity_limit(self):
        result = greedy_assign(_make_input(
            orders=[_order("o1", weight=30000), _order("o2", weight=30000, pickup_idx=3, delivery_idx=4)],
            vehicles=[_vehicle(capacity=40000)],
        ))
        assert len(result.unassigned_orders) == 1
        # Should assign first, skip second due to capacity
        assert len(result.routes) == 1
        assert len(result.routes[0].stops) == 2

    def test_fragile_motorcycle_exclusion(self):
        result = greedy_assign(_make_input(
            orders=[_order("o1", is_fragile=True)],
            vehicles=[_vehicle(vehicle_type=VehicleType.MOTORCYCLE)],
        ))
        assert len(result.unassigned_orders) == 1
        assert len(result.routes) == 0

    def test_time_window_infeasible(self):
        result = greedy_assign(_make_input(
            orders=[_order("o1", earliest_time_s=60000, latest_time_s=61000)],
            vehicles=[_vehicle(start_time_s=28800, end_time_s=50000)],
        ))
        assert len(result.unassigned_orders) == 1

    def test_max_stops_respected(self):
        result = greedy_assign(_make_input(
            orders=[
                _order("o1", pickup_idx=1, delivery_idx=2),
                _order("o2", pickup_idx=3, delivery_idx=4),
                _order("o3", pickup_idx=5, delivery_idx=6),
            ],
            vehicles=[_vehicle(max_stops=4)],
        ))
        # max_stops=4 means at most 4 stops (2 orders)
        assigned_count = sum(1 for r in result.routes for _ in r.stops) // 2
        assert assigned_count <= 2


class TestGreedyMultiVehicle:
    def test_overflow_to_second_vehicle(self):
        result = greedy_assign(_make_input(
            orders=[
                _order("o1", weight=30000, pickup_idx=1, delivery_idx=2),
                _order("o2", weight=30000, pickup_idx=3, delivery_idx=4),
            ],
            vehicles=[
                _vehicle("c1", capacity=40000),
                _vehicle("c2", capacity=40000),
            ],
        ))
        assert len(result.unassigned_orders) == 0
        assert len(result.routes) == 2

    def test_larger_vehicle_preferred(self):
        result = greedy_assign(_make_input(
            orders=[_order("o1", weight=30000)],
            vehicles=[
                _vehicle("small", capacity=20000),
                _vehicle("big", capacity=50000),
            ],
        ))
        assert len(result.unassigned_orders) == 0
        # Big vehicle should be used (sorted by capacity desc)
        assert result.routes[0].courier_id == "big"
