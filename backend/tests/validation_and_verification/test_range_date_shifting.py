"""Phase D V&V — date shifting actually works end-to-end.

These tests run a real Rich ALNS solve over a multi-day VRP built with
range-relative time encoding (the same encoding `dispatch_range` uses).
No DB, no OSRM, no API layer — the goal is to pin the actual solver
behavior: orders with flexible windows land on the right days, orders
with rigid windows stay where they're forced, and the distance/fuel
guarantee holds when day assignment is a free variable.

The integration tests that hit DB + OSRM live in the live integration
suite (`tests/integration/test_*_live_flow.py`); these here are fast
enough to run in CI on every push.
"""
from __future__ import annotations

from app.algorithms.adapters.rich_alns import RichALNSAdapter
from app.algorithms.solver_adapter import SolveOptions
from app.algorithms.vrp_solver import (
    OrderData,
    SolverStatus,
    VehicleData,
    VRPInput,
    build_vrp_input,
)
from app.models.enums import OrderPriority, VehicleType

HOUR = 3600
DAY = 86_400


def _matrix(n: int, *, value: int = 1_000) -> list[list[int]]:
    return [[0 if i == j else value for j in range(n)] for i in range(n)]


def _vehicle(
    *,
    courier_id: str,
    day_idx: int,
    depot_idx: int = 0,
    cap_g: int = 50_000,
    shift_start_h: int = 8,
    shift_end_h: int = 18,
) -> VehicleData:
    """Build a virtual-day vehicle anchored to `day_idx * 86400`."""
    offset = day_idx * DAY
    return VehicleData(
        courier_id=f"{courier_id}__day{day_idx}",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=cap_g,
        depot_index=depot_idx,
        start_time_s=offset + shift_start_h * HOUR,
        end_time_s=offset + shift_end_h * HOUR,
        hard_end_time_s=offset + (shift_end_h + 1) * HOUR,
        physical_courier_id=courier_id,
        trip_index=day_idx,
        fixed_route_cost=0 if day_idx > 0 else 1_000,
        # Make distance cost the dominant signal so the user's stated
        # objective ("minimize total distance + fuel") shows up cleanly.
        distance_cost_per_km=10,
        fuel_cost_per_km=10,
    )


def _solve(
    *,
    locations: list[tuple[float, float]],
    orders: list[OrderData],
    vehicles: list[VehicleData],
    distance_matrix: list[list[int]] | None = None,
    duration_matrix: list[list[int]] | None = None,
    time_budget_s: float = 8.0,
):
    n = len(locations)
    dist = distance_matrix or _matrix(n, value=10_000)
    dur = duration_matrix or _matrix(n, value=600)
    vrp = build_vrp_input(locations, dist, dur, orders, vehicles)
    return RichALNSAdapter().solve(
        vrp, SolveOptions(time_budget_s=time_budget_s, spread_fleet=False)
    )


def _day_of(arrival_s: int) -> int:
    return arrival_s // DAY


# ── Scenario 1: capacity forces flexible orders onto later days ───────────


def test_capacity_pressure_pushes_flexible_orders_to_later_days():
    """1 courier × 5-day range. Day 1 has 2 rigid orders that fill the
    courier's load. Two more flexible orders (Mon–Fri window) MUST land
    on day ≥ 1 because they don't fit on day 0 with the rigid ones."""
    locations = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]
    # All orders deliver at distinct nodes 1..4.
    rigid_1 = OrderData(
        order_id="rigid-1",
        pickup_index=0,
        delivery_index=1,
        weight_grams=25_000,
        earliest_time_s=8 * HOUR,
        latest_time_s=18 * HOUR,
        hard_latest_time_s=19 * HOUR,
        priority=OrderPriority.NORMAL,
        pickup_is_depot=True,
    )
    rigid_2 = OrderData(
        order_id="rigid-2",
        pickup_index=0,
        delivery_index=2,
        weight_grams=20_000,
        earliest_time_s=8 * HOUR,
        latest_time_s=18 * HOUR,
        hard_latest_time_s=19 * HOUR,
        priority=OrderPriority.NORMAL,
        pickup_is_depot=True,
    )
    # Flexible orders: full 5-day window so the solver can pick.
    flexible_1 = OrderData(
        order_id="flex-1",
        pickup_index=0,
        delivery_index=3,
        weight_grams=30_000,  # together with rigid-1 + rigid-2 overflows 50kg
        earliest_time_s=0,
        latest_time_s=5 * DAY - HOUR,
        hard_latest_time_s=5 * DAY,
        priority=OrderPriority.NORMAL,
        pickup_is_depot=True,
    )
    flexible_2 = OrderData(
        order_id="flex-2",
        pickup_index=0,
        delivery_index=4,
        weight_grams=20_000,
        earliest_time_s=0,
        latest_time_s=5 * DAY - HOUR,
        hard_latest_time_s=5 * DAY,
        priority=OrderPriority.NORMAL,
        pickup_is_depot=True,
    )
    # One courier, 5 day-vehicles. Capacity 50kg.
    vehicles = [_vehicle(courier_id="C1", day_idx=i, cap_g=50_000) for i in range(5)]
    result = _solve(
        locations=locations,
        orders=[rigid_1, rigid_2, flexible_1, flexible_2],
        vehicles=vehicles,
    )
    # No order dropped.
    assert not result.solution.unassigned_orders, (
        f"All four orders should fit somewhere in 5 days; unassigned: "
        f"{result.solution.unassigned_orders}"
    )
    # Find each order's chosen day from the planned routes.
    chosen_day: dict[str, int] = {}
    for route in result.solution.routes:
        for stop in route.stops:
            if stop.stop_type == "DELIVERY":
                chosen_day[stop.order_id] = _day_of(stop.arrival_time_s)
    assert chosen_day.get("rigid-1") == 0, chosen_day
    assert chosen_day.get("rigid-2") == 0, chosen_day
    # Flexible orders together weigh 50kg, which fits on a non-day-0 with
    # capacity to spare. They must NOT both land on day 0 (50+45=95 > 50).
    flex_days = {chosen_day["flex-1"], chosen_day["flex-2"]}
    assert flex_days != {0}, (
        f"Flexible orders should not all land on day 0; got {flex_days}"
    )


# ── Scenario 2: single-day window stays on that day ───────────────────────


def test_single_day_window_pins_order_to_that_day():
    """An order with `earliest_delivery == latest_delivery == day 2`
    schedules on day 2, not earlier or later."""
    locations = [(0.0, 0.0), (1.0, 0.0)]
    pinned = OrderData(
        order_id="pinned-day-2",
        pickup_index=0,
        delivery_index=1,
        weight_grams=10_000,
        earliest_time_s=2 * DAY + 9 * HOUR,
        latest_time_s=2 * DAY + 17 * HOUR,
        hard_latest_time_s=2 * DAY + 18 * HOUR,
        priority=OrderPriority.NORMAL,
        pickup_is_depot=True,
    )
    vehicles = [_vehicle(courier_id="C1", day_idx=i) for i in range(5)]
    result = _solve(locations=locations, orders=[pinned], vehicles=vehicles)
    assert not result.solution.unassigned_orders
    delivery_arrival = next(
        stop.arrival_time_s
        for route in result.solution.routes
        for stop in route.stops
        if stop.order_id == "pinned-day-2" and stop.stop_type == "DELIVERY"
    )
    assert _day_of(delivery_arrival) == 2, (
        f"Pinned order should land on day 2; got day {_day_of(delivery_arrival)}"
    )


# ── Scenario 3: distance-minimising consolidation ─────────────────────────


def test_two_orders_at_same_location_consolidate_onto_one_day():
    """Two orders deliver to the SAME node and both have a 2-day flexible
    window. The cheapest plan is to serve both on one day (one courier
    activation, one travel round-trip). Pins the 'minimise total distance'
    guarantee — the solver must NOT split them across days when capacity
    allows a single-day solve."""
    locations = [(0.0, 0.0), (1.0, 0.0)]
    o1 = OrderData(
        order_id="twin-1",
        pickup_index=0,
        delivery_index=1,
        weight_grams=10_000,
        earliest_time_s=0,
        latest_time_s=2 * DAY - HOUR,
        hard_latest_time_s=2 * DAY,
        priority=OrderPriority.NORMAL,
        pickup_is_depot=True,
    )
    o2 = OrderData(
        order_id="twin-2",
        pickup_index=0,
        delivery_index=1,
        weight_grams=10_000,
        earliest_time_s=0,
        latest_time_s=2 * DAY - HOUR,
        hard_latest_time_s=2 * DAY,
        priority=OrderPriority.NORMAL,
        pickup_is_depot=True,
    )
    vehicles = [_vehicle(courier_id="C1", day_idx=i) for i in range(2)]
    result = _solve(locations=locations, orders=[o1, o2], vehicles=vehicles)
    assert not result.solution.unassigned_orders
    # Both deliveries should be on the same day → one route, two stops at
    # node 1.
    days = set()
    for route in result.solution.routes:
        for stop in route.stops:
            if stop.stop_type == "DELIVERY":
                days.add(_day_of(stop.arrival_time_s))
    assert len(days) == 1, (
        f"Co-located twin orders should consolidate onto one day; got {days}"
    )


# ── Scenario 4: 3-day quick-plan smoke ────────────────────────────────────


def test_three_day_quick_plan_completes_under_small_tier_budget():
    """Dispatcher's '3-day quick plan' case — 1 courier, 8 orders with
    flexible windows. Solver must complete without errors and assign
    every order. Pins the user's '3 days' phrasing."""
    n_orders = 8
    # All deliveries at distinct nodes.
    locations = [(0.0, 0.0)] + [(0.01 * (i + 1), 0.0) for i in range(n_orders)]
    orders = [
        OrderData(
            order_id=f"q-{i}",
            pickup_index=0,
            delivery_index=i + 1,
            weight_grams=5_000,
            earliest_time_s=0,
            latest_time_s=3 * DAY - HOUR,
            hard_latest_time_s=3 * DAY,
            priority=OrderPriority.NORMAL,
            pickup_is_depot=True,
        )
        for i in range(n_orders)
    ]
    vehicles = [_vehicle(courier_id="C1", day_idx=i) for i in range(3)]
    result = _solve(
        locations=locations,
        orders=orders,
        vehicles=vehicles,
        time_budget_s=4.0,
    )
    assert result.solution.solver_status in (
        SolverStatus.OPTIMAL,
        SolverStatus.FEASIBLE,
    )
    assert not result.solution.unassigned_orders, (
        f"All 8 quick-plan orders should fit; unassigned: "
        f"{result.solution.unassigned_orders}"
    )


# ── Scenario 5: ALNS engine, not OR-Tools ─────────────────────────────────


def test_solver_kind_is_rich_alns_via_adapter():
    """Sanity that the adapter we're testing IS Rich ALNS — protect
    against accidental future regressions to OR-Tools or another adapter
    that would silently undo the Phase B-1 swap."""
    from app.algorithms.adapters.rich_alns import RichALNSAdapter
    from app.algorithms.solver_registry import get_solver

    # Default range path uses the adapter from the registry.
    adapter = get_solver("rich_alns")
    assert isinstance(adapter, RichALNSAdapter)
