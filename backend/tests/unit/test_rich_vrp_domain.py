"""Tests for the rich VRP domain layer (M1.1 / M1.2)."""
from __future__ import annotations

from app.algorithms.rich_vrp.domain.builders import build_problem, export_solution
from app.algorithms.rich_vrp.domain.models import (
    Route,
    Solution,
    Visit,
    VisitKind,
)
from app.algorithms.vrp_solver import (
    OrderData,
    VehicleData,
    VRPInput,
)
from app.models.enums import OrderPriority, VehicleType

HOUR = 3600


def _make_vrp_input(n_orders: int = 2) -> VRPInput:
    locations = [(41.0, 29.0)]  # depot
    orders = []
    for i in range(n_orders):
        locations.append((41.0 + i * 0.01, 29.0 + i * 0.01))   # pickup
        locations.append((41.0 + i * 0.01 + 0.005, 29.0 + i * 0.01 + 0.005))  # delivery
        orders.append(OrderData(
            order_id=f"ord-{i}",
            pickup_index=1 + i * 2,
            delivery_index=2 + i * 2,
            weight_grams=1000,
            earliest_time_s=8 * HOUR,
            latest_time_s=18 * HOUR,
            hard_latest_time_s=19 * HOUR,
            service_time_s=300,
            priority=OrderPriority.NORMAL,
        ))
    n = len(locations)
    dist_matrix = [[abs(i - j) * 1000 for j in range(n)] for i in range(n)]
    dur_matrix = [[abs(i - j) * 60 for j in range(n)] for i in range(n)]
    vehicle = VehicleData(
        courier_id="veh-0",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=20_000,
        depot_index=0,
        start_time_s=8 * HOUR,
        end_time_s=18 * HOUR,
        hard_end_time_s=19 * HOUR,
    )
    return VRPInput(
        locations=locations,
        orders=orders,
        vehicles=[vehicle],
        distance_matrix=dist_matrix,
        duration_matrix=dur_matrix,
    )


def test_build_problem_creates_requests_and_vehicles():
    vrp = _make_vrp_input(n_orders=3)
    problem = build_problem(vrp)

    assert len(problem.requests) == 3
    assert len(problem.vehicles) == 1
    assert len(problem.locations) == 7  # depot + 3*2


def test_build_problem_request_fields():
    vrp = _make_vrp_input(n_orders=1)
    problem = build_problem(vrp)
    req = problem.request("ord-0")

    assert req.id == "ord-0"
    assert req.pickup_node == 1
    assert req.delivery_node == 2
    assert req.delivery_window == (8 * HOUR, 18 * HOUR)
    assert req.optional is True  # NORMAL priority → optional


def test_build_problem_vehicle_index():
    vrp = _make_vrp_input()
    problem = build_problem(vrp)
    veh = problem.vehicle("veh-0")
    assert veh.index == 0
    assert veh.start_node == 0
    assert veh.end_node == 0


def test_export_empty_solution():
    vrp = _make_vrp_input(n_orders=2)
    problem = build_problem(vrp)

    # Build an empty solution (all orders unserved)
    empty_routes = []
    for v in problem.vehicles:
        empty_routes.append(Route(
            vehicle=v,
            visits=[
                Visit(request_id=None, kind=VisitKind.DEPOT_START, node=v.start_node, arrival_s=0, departure_s=0),
                Visit(request_id=None, kind=VisitKind.DEPOT_END, node=v.end_node, arrival_s=0, departure_s=0),
            ],
        ))
    solution = Solution(
        routes=empty_routes,
        unserved={"ord-0", "ord-1"},
    )
    exported = export_solution(solution, vrp)

    assert sorted(exported.unassigned_orders) == ["ord-0", "ord-1"]
    # Routes with no actual stops are filtered out
    assert exported.total_distance_m == 0


def test_export_solution_maps_stop_types():
    vrp = _make_vrp_input(n_orders=1)
    problem = build_problem(vrp)
    v = problem.vehicles[0]
    req = problem.requests[0]

    route = Route(
        vehicle=v,
        visits=[
            Visit(request_id=None, kind=VisitKind.DEPOT_START, node=v.start_node, arrival_s=8 * HOUR, departure_s=8 * HOUR),
            Visit(request_id=req.id, kind=VisitKind.PICKUP, node=req.pickup_node, arrival_s=8 * HOUR + 60, departure_s=8 * HOUR + 360),
            Visit(request_id=req.id, kind=VisitKind.DELIVERY, node=req.delivery_node, arrival_s=9 * HOUR, departure_s=9 * HOUR + 300),
            Visit(request_id=None, kind=VisitKind.DEPOT_END, node=v.end_node, arrival_s=9 * HOUR + 360, departure_s=9 * HOUR + 360),
        ],
    )
    solution = Solution(routes=[route], unserved=set())
    exported = export_solution(solution, vrp)

    assert len(exported.routes) == 1
    route_out = exported.routes[0]
    assert route_out.courier_id == "veh-0"
    assert len(route_out.stops) == 2  # pickup + delivery (depot excluded)
    assert route_out.stops[0].stop_type == "PICKUP"
    assert route_out.stops[1].stop_type == "DELIVERY"
    assert route_out.stops[0].order_id == "ord-0"


def test_matrix_view_duration():
    vrp = _make_vrp_input(n_orders=1)
    problem = build_problem(vrp)
    # Duration from node 0 to node 1 (default profile)
    assert problem.matrix.duration(0, 1) == 60
    assert problem.matrix.distance(0, 1) == 1000


def test_request_lookup_raises_keyerror_for_unknown():
    vrp = _make_vrp_input(n_orders=1)
    problem = build_problem(vrp)
    import pytest
    with pytest.raises(KeyError):
        problem.request("nonexistent")
