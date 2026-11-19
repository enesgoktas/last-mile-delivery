"""Phase 3 V&V — feasibility kernel honors the allowed-depot set.

A RELOAD visit at a node outside the vehicle's allowed depots is a
HARD violation (kind="depot"). A RELOAD at any allowed depot remains
feasible. Single-depot vehicles (empty `allowed_depot_indices`) keep
pre-Phase-3 behavior bit-identically.
"""
from __future__ import annotations

from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import Route, Visit, VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import (
    Feasibility,
    evaluate_route,
)
from app.algorithms.vrp_solver import OrderData, VehicleData, VRPInput
from app.models.enums import OrderPriority, VehicleType

HOUR = 3600


def _input(*, allowed_depot_indices: tuple[int, ...] = ()) -> VRPInput:
    # 5-node toy world:
    #   0 = home depot A
    #   1 = order 1 pickup
    #   2 = order 1 delivery
    #   3 = additional depot B
    #   4 = order 2 pickup
    locations = [
        (41.00, 29.00),
        (41.01, 29.00),
        (41.01, 29.01),
        (41.05, 29.05),
        (41.06, 29.06),
    ]
    orders = [
        OrderData(
            order_id="o1",
            pickup_index=1,
            delivery_index=2,
            weight_grams=10_000,
            earliest_time_s=8 * HOUR,
            latest_time_s=18 * HOUR,
            hard_latest_time_s=19 * HOUR,
            service_time_s=60,
            priority=OrderPriority.NORMAL,
        ),
    ]
    vehicle = VehicleData(
        courier_id="v1",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=50_000,
        depot_index=0,
        allowed_depot_indices=allowed_depot_indices,
        start_time_s=8 * HOUR,
        end_time_s=18 * HOUR,
        hard_end_time_s=19 * HOUR,
    )
    n = len(locations)
    dist = [[0 if i == j else 1000 for j in range(n)] for i in range(n)]
    dur = [[0 if i == j else 120 for j in range(n)] for i in range(n)]
    return VRPInput(
        locations=locations,
        orders=orders,
        vehicles=[vehicle],
        distance_matrix=dist,
        duration_matrix=dur,
    )


def _route_with_reload(problem, reload_node: int) -> Route:
    """DEPOT_START(0) → PICKUP(1) → DELIVERY(2) → RELOAD(reload_node) → DEPOT_END(0)."""
    v = problem.vehicles[0]
    req = problem.requests[0]
    return Route(
        vehicle=v,
        visits=[
            Visit(request_id=None, kind=VisitKind.DEPOT_START, node=v.start_node, arrival_s=0, departure_s=0),
            Visit(request_id=req.id, kind=VisitKind.PICKUP, node=req.pickup_node, arrival_s=0, departure_s=0),
            Visit(request_id=req.id, kind=VisitKind.DELIVERY, node=req.delivery_node, arrival_s=0, departure_s=0),
            Visit(request_id=None, kind=VisitKind.RELOAD, node=reload_node, arrival_s=0, departure_s=0),
            Visit(request_id=None, kind=VisitKind.DEPOT_END, node=v.end_node, arrival_s=0, departure_s=0),
        ],
    )


def test_reload_at_allowed_depot_is_feasible():
    vrp = _input(allowed_depot_indices=(0, 3))
    problem = build_problem(vrp)
    route = _route_with_reload(problem, reload_node=3)
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.FEASIBLE
    assert not any(v.kind == "depot" for v in result.hard_violations)


def test_reload_at_non_allowed_node_is_hard_infeasible():
    vrp = _input(allowed_depot_indices=(0, 3))
    problem = build_problem(vrp)
    # Node 4 is an order pickup, not an allowed depot for this vehicle.
    route = _route_with_reload(problem, reload_node=4)
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.HARD_INFEASIBLE
    assert any(v.kind == "depot" for v in result.hard_violations)


def test_single_depot_reload_at_home_is_feasible_back_compat():
    """Empty allowed_depot_indices is the pre-Phase-3 sentinel — RELOAD at
    the vehicle's own start_node remains feasible exactly like before.
    """
    vrp = _input(allowed_depot_indices=())
    problem = build_problem(vrp)
    route = _route_with_reload(problem, reload_node=0)  # home depot
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.FEASIBLE


def test_single_depot_reload_at_other_node_is_hard_infeasible():
    """For a single-depot vehicle, RELOAD at any node other than start_node
    is rejected — the implicit allowed set is (start_node,)."""
    vrp = _input(allowed_depot_indices=())
    problem = build_problem(vrp)
    route = _route_with_reload(problem, reload_node=3)  # not the home depot
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.HARD_INFEASIBLE
    assert any(v.kind == "depot" for v in result.hard_violations)
