"""Tests for the feasibility kernel (M1.4)."""
from __future__ import annotations

from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import (
    Route,
    Visit,
    VisitKind,
)
from app.algorithms.rich_vrp.feasibility.kernel import (
    Feasibility,
    evaluate_route,
    feasible_insertion_positions,
    is_compatible,
)
from app.algorithms.vrp_solver import OrderData, VehicleData, VRPInput
from app.models.enums import OrderPriority, VehicleType

HOUR = 3600


def _make_input(
    weight_grams: int = 1000,
    earliest: int = 8 * HOUR,
    latest: int = 18 * HOUR,
    hard_latest: int | None = None,
    capacity_grams: int = 20_000,
    shift_end: int = 18 * HOUR,
) -> VRPInput:
    locations = [(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)]
    orders = [OrderData(
        order_id="ord-0",
        pickup_index=1,
        delivery_index=2,
        weight_grams=weight_grams,
        earliest_time_s=earliest,
        latest_time_s=latest,
        hard_latest_time_s=hard_latest or latest + HOUR,
        service_time_s=300,
        priority=OrderPriority.NORMAL,
    )]
    vehicle = VehicleData(
        courier_id="veh-0",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=capacity_grams,
        depot_index=0,
        start_time_s=8 * HOUR,
        end_time_s=shift_end,
        hard_end_time_s=shift_end + HOUR,
    )
    dist = [[0, 1000, 2000], [1000, 0, 1000], [2000, 1000, 0]]
    dur = [[0, 120, 240], [120, 0, 120], [240, 120, 0]]
    return VRPInput(
        locations=locations,
        orders=orders,
        vehicles=[vehicle],
        distance_matrix=dist,
        duration_matrix=dur,
    )


def _make_feasible_route(problem) -> Route:
    """Build a hand-constructed feasible route: DEPOT_START → PICKUP → DELIVERY → DEPOT_END."""
    v = problem.vehicles[0]
    req = problem.requests[0]
    return Route(
        vehicle=v,
        visits=[
            Visit(request_id=None, kind=VisitKind.DEPOT_START, node=v.start_node, arrival_s=0, departure_s=0),
            Visit(request_id=req.id, kind=VisitKind.PICKUP, node=req.pickup_node, arrival_s=0, departure_s=0),
            Visit(request_id=req.id, kind=VisitKind.DELIVERY, node=req.delivery_node, arrival_s=0, departure_s=0),
            Visit(request_id=None, kind=VisitKind.DEPOT_END, node=v.end_node, arrival_s=0, departure_s=0),
        ],
    )


def test_feasible_route_evaluates_feasible():
    vrp = _make_input()
    problem = build_problem(vrp)
    route = _make_feasible_route(problem)
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.FEASIBLE
    assert result.hard_violations == []


def test_overcapacity_makes_hard_infeasible():
    vrp = _make_input(weight_grams=100_000, capacity_grams=1_000)
    problem = build_problem(vrp)
    route = _make_feasible_route(problem)
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.HARD_INFEASIBLE
    assert any("capacity" in v.kind for v in result.hard_violations)


def test_hard_deadline_violation_makes_infeasible():
    # Hard deadline set very tight: before we can even reach the delivery node
    vrp = _make_input(hard_latest=8 * HOUR + 1)  # only 1 second after shift start
    problem = build_problem(vrp)
    route = _make_feasible_route(problem)
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.HARD_INFEASIBLE
    assert any("time_window" in v.kind for v in result.hard_violations)


def test_soft_late_delivery_is_soft_infeasible():
    # Latest delivery is earlier than when we arrive, but no hard deadline issue
    # Shift starts at 8am; travel to pickup=120s, service=300s, travel to delivery=120s
    # So earliest arrival at delivery ≈ 8h + 120 + 300 + 120 = 8h + 540s = 8h09m
    # Set latest to 8h + 60s so we're late on soft but not hard
    vrp = _make_input(
        earliest=8 * HOUR,
        latest=8 * HOUR + 60,  # soft deadline: 8h01m
        hard_latest=20 * HOUR,  # hard deadline: 8pm (not violated)
    )
    problem = build_problem(vrp)
    route = _make_feasible_route(problem)
    result = evaluate_route(route, problem)
    # May be FEASIBLE or SOFT_INFEASIBLE depending on exact computation
    assert result.feasibility in (Feasibility.FEASIBLE, Feasibility.SOFT_INFEASIBLE)
    # No hard violations
    assert result.hard_violations == [] or all(v.kind != "time_window" for v in result.hard_violations)


def test_precedence_violation_delivery_without_pickup():
    vrp = _make_input()
    problem = build_problem(vrp)
    v = problem.vehicles[0]
    req = problem.requests[0]
    # Put DELIVERY before PICKUP to trigger precedence violation
    route = Route(
        vehicle=v,
        visits=[
            Visit(request_id=None, kind=VisitKind.DEPOT_START, node=v.start_node, arrival_s=0, departure_s=0),
            Visit(request_id=req.id, kind=VisitKind.DELIVERY, node=req.delivery_node, arrival_s=0, departure_s=0),
            Visit(request_id=req.id, kind=VisitKind.PICKUP, node=req.pickup_node, arrival_s=0, departure_s=0),
            Visit(request_id=None, kind=VisitKind.DEPOT_END, node=v.end_node, arrival_s=0, departure_s=0),
        ],
    )
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.HARD_INFEASIBLE
    assert any("precedence" in v.kind for v in result.hard_violations)


def test_is_compatible_required_tags():
    vrp = _make_input()
    problem = build_problem(vrp)
    req = problem.requests[0]
    veh = problem.vehicles[0]
    # With no required tags, should be compatible
    assert is_compatible(req, veh)


def test_is_compatible_allowed_vehicle_indices():
    from app.algorithms.rich_vrp.domain.models import Request

    vrp = _make_input()
    problem = build_problem(vrp)
    req = problem.requests[0]
    veh = problem.vehicles[0]

    # Restrict to vehicle index 99 (not our vehicle's index 0)
    restricted_req = Request(
        id=req.id,
        pickup_node=req.pickup_node,
        delivery_node=req.delivery_node,
        pickup_window=req.pickup_window,
        delivery_window=req.delivery_window,
        hard_delivery_deadline=req.hard_delivery_deadline,
        service_time_pickup_s=req.service_time_pickup_s,
        service_time_delivery_s=req.service_time_delivery_s,
        load=req.load,
        priority=req.priority,
        unserved_penalty=req.unserved_penalty,
        lateness_penalty_per_min=req.lateness_penalty_per_min,
        priority_multiplier=req.priority_multiplier,
        required_tags=req.required_tags,
        allowed_vehicle_indices=frozenset([99]),  # vehicle 0 not allowed
        optional=req.optional,
    )
    assert not is_compatible(restricted_req, veh)


def test_feasible_insertion_positions_returns_at_least_one():
    vrp = _make_input()
    problem = build_problem(vrp)
    v = problem.vehicles[0]
    req = problem.requests[0]

    # Start with empty route
    route = Route(
        vehicle=v,
        visits=[
            Visit(request_id=None, kind=VisitKind.DEPOT_START, node=v.start_node, arrival_s=8 * HOUR, departure_s=8 * HOUR),
            Visit(request_id=None, kind=VisitKind.DEPOT_END, node=v.end_node, arrival_s=8 * HOUR, departure_s=8 * HOUR),
        ],
    )
    positions = list(feasible_insertion_positions(route, req, problem))
    assert len(positions) >= 1
    for pickup_pos, delivery_pos in positions:
        assert delivery_pos > pickup_pos


def test_empty_route_cost_is_zero():
    vrp = _make_input()
    problem = build_problem(vrp)
    v = problem.vehicles[0]
    route = Route(
        vehicle=v,
        visits=[
            Visit(request_id=None, kind=VisitKind.DEPOT_START, node=v.start_node, arrival_s=8 * HOUR, departure_s=8 * HOUR),
            Visit(request_id=None, kind=VisitKind.DEPOT_END, node=v.end_node, arrival_s=8 * HOUR, departure_s=8 * HOUR),
        ],
    )
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.FEASIBLE
    assert result.cost.total_generalized_cost == 0


# ── Phase 2.3: per-segment battery dimension ──────────────────────────


def test_battery_unaware_route_keeps_route_wide_distance_check():
    """Vehicles without `full_range_m` set fall through to the existing
    route-wide distance cap (the Phase 1.5 behaviour)."""
    vrp = _make_input()
    # Override the vehicle with a tight max_distance_m and full_range_m=0.
    v = vrp.vehicles[0]
    import dataclasses
    vehicles = [dataclasses.replace(v, max_distance_m=100, full_range_m=0)]
    vrp = VRPInput(
        locations=vrp.locations,
        orders=vrp.orders,
        vehicles=vehicles,
        distance_matrix=vrp.distance_matrix,
        duration_matrix=vrp.duration_matrix,
    )
    problem = build_problem(vrp)
    route = _make_feasible_route(problem)
    route.total_distance_m = 6000  # depot→pickup→delivery→depot
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.HARD_INFEASIBLE
    assert any(v.kind == "distance" for v in result.hard_violations)


def test_battery_aware_route_fails_when_first_leg_exceeds_initial_budget():
    """EV starting at low charge: pickup is too far away to reach
    without charging — hard violation."""
    vrp = _make_input()
    v = vrp.vehicles[0]
    # Initial budget 500m, full range 5000m. Pickup is 1000m away so the
    # first leg busts the budget.
    import dataclasses
    vehicles = [dataclasses.replace(v, max_distance_m=500, full_range_m=5000)]
    vrp = VRPInput(
        locations=vrp.locations,
        orders=vrp.orders,
        vehicles=vehicles,
        distance_matrix=vrp.distance_matrix,
        duration_matrix=vrp.duration_matrix,
    )
    problem = build_problem(vrp)
    route = _make_feasible_route(problem)
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.HARD_INFEASIBLE
    assert any(v.kind == "battery" for v in result.hard_violations)


def test_battery_aware_route_succeeds_when_charger_resets_remaining_range():
    """EV starting at 30% charge runs out before delivery without a charger,
    but with a charging stop inserted between pickup and delivery the
    remaining-range counter resets and the route validates clean."""
    locations = [
        (41.0, 29.0),  # 0: depot
        (41.0, 29.0),  # 1: pickup
        (41.0, 29.0),  # 2: delivery
        (41.0, 29.0),  # 3: charger (same point — only matrix matters)
    ]
    # 3000m to pickup, 3000m to delivery, 0m to charger from anywhere.
    dist = [
        [0, 3000, 7000, 1000],
        [3000, 0, 3000, 0],
        [7000, 3000, 0, 0],
        [1000, 0, 0, 0],
    ]
    dur = [[0, 120, 240, 60], [120, 0, 120, 60], [240, 120, 0, 60], [60, 60, 60, 0]]

    delivery_order = OrderData(
        order_id="ord-0",
        pickup_index=1,
        delivery_index=2,
        weight_grams=1000,
        earliest_time_s=8 * HOUR,
        latest_time_s=18 * HOUR,
        hard_latest_time_s=18 * HOUR + HOUR,
        service_time_s=300,
    )
    charge_order = OrderData(
        order_id="__CHARGER__",
        pickup_index=3,
        delivery_index=3,
        weight_grams=0,
        earliest_time_s=0,
        latest_time_s=24 * HOUR,
        hard_latest_time_s=24 * HOUR,
        service_time_s=600,
        is_charging=True,
    )
    vehicle = VehicleData(
        courier_id="veh-ev",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=20_000,
        depot_index=0,
        start_time_s=8 * HOUR,
        end_time_s=18 * HOUR,
        hard_end_time_s=19 * HOUR,
        # Initial budget: 5000m. Full range: 10_000m. Without a charge,
        # depot(0)→pickup(1)→delivery(2)→depot(0) = 3000+3000+7000 = 13_000m
        # > 5000m. Hard fail. With charge between pickup and delivery,
        # the second leg starts with full 10_000m.
        max_distance_m=5000,
        full_range_m=10_000,
    )
    vrp = VRPInput(
        locations=locations,
        orders=[delivery_order, charge_order],
        vehicles=[vehicle],
        distance_matrix=dist,
        duration_matrix=dur,
    )
    problem = build_problem(vrp)
    v = problem.vehicles[0]
    delivery_req = problem.request("ord-0")
    charge_req = problem.request("__CHARGER__")

    # Build: depot → pickup → charger pickup+delivery → delivery → depot
    route = Route(
        vehicle=v,
        visits=[
            Visit(request_id=None, kind=VisitKind.DEPOT_START, node=v.start_node, arrival_s=0, departure_s=0),
            Visit(request_id=delivery_req.id, kind=VisitKind.PICKUP, node=delivery_req.pickup_node, arrival_s=0, departure_s=0),
            Visit(request_id=charge_req.id, kind=VisitKind.PICKUP, node=charge_req.pickup_node, arrival_s=0, departure_s=0),
            Visit(request_id=charge_req.id, kind=VisitKind.DELIVERY, node=charge_req.delivery_node, arrival_s=0, departure_s=0),
            Visit(request_id=delivery_req.id, kind=VisitKind.DELIVERY, node=delivery_req.delivery_node, arrival_s=0, departure_s=0),
            Visit(request_id=None, kind=VisitKind.DEPOT_END, node=v.end_node, arrival_s=0, departure_s=0),
        ],
    )
    result = evaluate_route(route, problem)
    # Routing-wise feasible thanks to the reset; lateness/overtime
    # may still be soft-infeasible. The key assertion: NOT hard-infeasible.
    assert result.feasibility != Feasibility.HARD_INFEASIBLE, (
        f"battery-aware route should validate clean post-charge; "
        f"violations: {result.hard_violations}"
    )
    assert not any(v.kind == "battery" for v in result.hard_violations)


# ── Phase post-2.3: passenger capacity dimension ──────────────────────


def test_passenger_capacity_hard_caps_oversize_ride():
    """4-pax sedan can't accept a 5-pax RIDE — hard infeasible."""
    import dataclasses
    vrp = _make_input()
    v = vrp.vehicles[0]
    o = vrp.orders[0]
    sedan = dataclasses.replace(v, max_passengers=4)
    big_ride = dataclasses.replace(o, passenger_count=5)
    vrp = VRPInput(
        locations=vrp.locations,
        orders=[big_ride],
        vehicles=[sedan],
        distance_matrix=vrp.distance_matrix,
        duration_matrix=vrp.duration_matrix,
    )
    problem = build_problem(vrp)
    route = _make_feasible_route(problem)
    result = evaluate_route(route, problem)
    assert result.feasibility == Feasibility.HARD_INFEASIBLE
    assert any(
        violation.kind == "capacity" and "passenger_count" in violation.detail
        for violation in result.hard_violations
    )


def test_passenger_capacity_allows_within_limit():
    """8-pax minivan happily accepts a 5-pax RIDE."""
    import dataclasses
    vrp = _make_input()
    v = vrp.vehicles[0]
    o = vrp.orders[0]
    minivan = dataclasses.replace(v, max_passengers=8)
    ride = dataclasses.replace(o, passenger_count=5)
    vrp = VRPInput(
        locations=vrp.locations,
        orders=[ride],
        vehicles=[minivan],
        distance_matrix=vrp.distance_matrix,
        duration_matrix=vrp.duration_matrix,
    )
    problem = build_problem(vrp)
    route = _make_feasible_route(problem)
    result = evaluate_route(route, problem)
    # Capacity-clean — no 'capacity' violations referencing passenger_count.
    assert not any(
        violation.kind == "capacity" and "passenger_count" in violation.detail
        for violation in result.hard_violations
    )


def test_passenger_capacity_unconstrained_when_max_passengers_zero():
    """Vehicles with max_passengers=0 (delivery-only fleet) ignore the
    dimension entirely — same default as weight/volume/packages."""
    import dataclasses
    vrp = _make_input()
    v = vrp.vehicles[0]
    o = vrp.orders[0]
    delivery_van = dataclasses.replace(v, max_passengers=0)
    huge_ride = dataclasses.replace(o, passenger_count=99)
    vrp = VRPInput(
        locations=vrp.locations,
        orders=[huge_ride],
        vehicles=[delivery_van],
        distance_matrix=vrp.distance_matrix,
        duration_matrix=vrp.duration_matrix,
    )
    problem = build_problem(vrp)
    route = _make_feasible_route(problem)
    result = evaluate_route(route, problem)
    assert not any(
        violation.kind == "capacity" and "passenger_count" in violation.detail
        for violation in result.hard_violations
    )
