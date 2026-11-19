"""Phase 4 — reload-insertion local-search move repairs overcapacity routes.

A pickup-pickup-delivery-delivery sequence (both orders in flight at
the same time) lets us exceed capacity, which is the condition the
move is designed to fix. The move inserts a `VisitKind.RELOAD` at a
position + allowed depot that produces a feasible route.

Multi-depot back-compat: a vehicle with empty `allowed_depot_nodes`
falls back to `(start_node,)`, so the reload location is the home
depot — matching pre-multi-depot behavior.
"""
from __future__ import annotations

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import (
    Route,
    Solution,
    Visit,
    VisitKind,
)
from app.algorithms.rich_vrp.feasibility.kernel import (
    Feasibility,
    evaluate_route,
)
from app.algorithms.rich_vrp.local_search.candidate_lists import (
    build_candidate_lists,
)
from app.algorithms.rich_vrp.local_search.moves import reload_insertion
from app.algorithms.vrp_solver import OrderData, VehicleData, VRPInput
from app.models.enums import OrderPriority, VehicleType

HOUR = 3600


def _problem(*, allowed_depot_indices: tuple[int, ...] = (), capacity_grams: int = 10):
    """6-node toy world with two depots and 4 pickup/delivery nodes."""
    locations = [
        (41.10, 29.00),  # 0 = home depot A
        (41.11, 29.00),  # 1 = order 1 pickup
        (41.12, 29.01),  # 2 = order 1 delivery
        (41.00, 29.10),  # 3 = additional depot B
        (41.13, 29.02),  # 4 = order 2 pickup
        (41.14, 29.03),  # 5 = order 2 delivery
    ]
    n = len(locations)
    dist = [[0 if i == j else 1_000 for j in range(n)] for i in range(n)]
    dur = [[0 if i == j else 120 for j in range(n)] for i in range(n)]

    orders = [
        OrderData(
            order_id="o1",
            pickup_index=1,
            delivery_index=2,
            # Each order is light individually (5g) but two-in-flight
            # totals 10g, which equals the vehicle capacity. The
            # specific overflow we engineer is at three-in-flight, but
            # we only need two orders to trigger pickup-pickup-overflow.
            weight_grams=5,
            earliest_time_s=8 * HOUR,
            latest_time_s=18 * HOUR,
            hard_latest_time_s=19 * HOUR,
            service_time_s=60,
            priority=OrderPriority.NORMAL,
        ),
        OrderData(
            order_id="o2",
            pickup_index=4,
            delivery_index=5,
            weight_grams=7,
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
        capacity_grams=capacity_grams,
        depot_index=0,
        allowed_depot_indices=allowed_depot_indices,
        start_time_s=8 * HOUR,
        end_time_s=18 * HOUR,
        hard_end_time_s=19 * HOUR,
    )
    return build_problem(
        VRPInput(
            locations=locations,
            orders=orders,
            vehicles=[vehicle],
            distance_matrix=dist,
            duration_matrix=dur,
        )
    )


def _overcapacity_route(problem) -> Route:
    """PICKUP1, PICKUP2, DELIVERY1, DELIVERY2 — both in flight at
    PICKUP2 time, which overflows the capacity."""
    v = problem.vehicles[0]
    reqs = problem.requests
    return Route(
        vehicle=v,
        visits=[
            Visit(request_id=None, kind=VisitKind.DEPOT_START, node=v.start_node, arrival_s=0, departure_s=0),
            Visit(request_id=reqs[0].id, kind=VisitKind.PICKUP, node=reqs[0].pickup_node, arrival_s=0, departure_s=0),
            Visit(request_id=reqs[1].id, kind=VisitKind.PICKUP, node=reqs[1].pickup_node, arrival_s=0, departure_s=0),
            Visit(request_id=reqs[0].id, kind=VisitKind.DELIVERY, node=reqs[0].delivery_node, arrival_s=0, departure_s=0),
            Visit(request_id=reqs[1].id, kind=VisitKind.DELIVERY, node=reqs[1].delivery_node, arrival_s=0, departure_s=0),
            Visit(request_id=None, kind=VisitKind.DEPOT_END, node=v.end_node, arrival_s=0, departure_s=0),
        ],
    )


def _state_for(problem, route: Route) -> ALNSState:
    return ALNSState(
        solution=Solution(routes=[route], unserved=set()),
        problem=problem,
    )


def test_overcapacity_route_is_actually_infeasible():
    """Sanity check on the fixture — two pickups before any delivery
    overflows the capacity, so the kernel flags HARD_INFEASIBLE."""
    problem = _problem(allowed_depot_indices=())
    route = _overcapacity_route(problem)
    ev = evaluate_route(route, problem)
    assert ev.feasibility == Feasibility.HARD_INFEASIBLE
    assert any(v.kind == "capacity" for v in ev.hard_violations)


def test_reload_insertion_repairs_overcapacity_route_single_depot():
    """Single-depot vehicle (allowed=()) — the move inserts a RELOAD at
    the home depot (node 0). The repaired route is feasible."""
    problem = _problem(allowed_depot_indices=())
    route = _overcapacity_route(problem)
    state = _state_for(problem, route)

    n_applied = reload_insertion.search(
        state,
        problem,
        build_candidate_lists(problem, k=8),
        evaluator=None,
        max_moves=1,
    )
    assert n_applied == 1
    reload_visits = [v for v in route.visits if v.kind == VisitKind.RELOAD]
    assert len(reload_visits) == 1
    assert reload_visits[0].node == 0  # home depot
    assert evaluate_route(route, problem).feasibility == Feasibility.FEASIBLE


def test_reload_insertion_picks_depot_from_allowed_set_multi_depot():
    """Multi-depot vehicle (allowed={0, 3}) — both depots are valid
    reload locations. The move must pick one of them (which one is an
    optimization-quality matter handled by Phase 4's first-improving
    cost comparison)."""
    problem = _problem(allowed_depot_indices=(0, 3))
    route = _overcapacity_route(problem)
    state = _state_for(problem, route)

    n_applied = reload_insertion.search(
        state,
        problem,
        build_candidate_lists(problem, k=8),
        evaluator=None,
        max_moves=1,
    )
    assert n_applied == 1
    reload_visits = [v for v in route.visits if v.kind == VisitKind.RELOAD]
    assert len(reload_visits) == 1
    assert reload_visits[0].node in (0, 3)
    assert evaluate_route(route, problem).feasibility == Feasibility.FEASIBLE


def test_reload_insertion_skips_already_feasible_route():
    """Vehicle with capacity well above demand — no overflow, no
    reload needed. Move applies zero changes (any insertion would
    raise cost over the feasible baseline)."""
    problem = _problem(allowed_depot_indices=(0, 3), capacity_grams=100)
    route = _overcapacity_route(problem)
    state = _state_for(problem, route)
    # Pre-condition: this same visit sequence is feasible with the
    # bigger capacity.
    assert evaluate_route(route, problem).feasibility == Feasibility.FEASIBLE

    n_applied = reload_insertion.search(
        state,
        problem,
        build_candidate_lists(problem, k=8),
        evaluator=None,
        max_moves=1,
    )
    assert n_applied == 0
    assert not any(v.kind == VisitKind.RELOAD for v in route.visits)


def test_reload_insertion_never_inserts_at_disallowed_depot():
    """Even when the route is desperate (infeasible baseline), the
    move only considers depots in the allowed set — node 4 (an order
    pickup) must not be picked, even though it's a valid index."""
    problem = _problem(allowed_depot_indices=(0,))
    route = _overcapacity_route(problem)
    state = _state_for(problem, route)

    reload_insertion.search(
        state,
        problem,
        build_candidate_lists(problem, k=8),
        evaluator=None,
        max_moves=1,
    )
    reload_visits = [v for v in route.visits if v.kind == VisitKind.RELOAD]
    if reload_visits:
        for rv in reload_visits:
            assert rv.node == 0  # only allowed depot
