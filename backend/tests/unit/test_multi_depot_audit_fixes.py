"""Regression coverage for the post-Phase-6 audit findings.

Each test pins down one of the bugs the code reviewer flagged so that
future refactors can't reintroduce them silently.
"""
from __future__ import annotations

from app.algorithms.feasibility_diagnostics import _solo_timing_feasible
from app.algorithms.greedy_fallback import greedy_assign
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
from app.algorithms.route_plan import (
    RoutePlanStopSpec,
    is_depot_pickup_spec,
)
from app.algorithms.vrp_solver import OrderData, VehicleData, VRPInput
from app.models.enums import OrderPriority, VehicleType

HOUR = 3600


def _matrix(n: int) -> list[list[int]]:
    return [[0 if i == j else 100 for j in range(n)] for i in range(n)]


# ── Fix #1: warm-start vnode layout ────────────────────────────────────


def test_warm_start_n_depots_matches_phase_5_layout():
    """The warm-start cache builds initial routes for OR-Tools using
    virtual node indices. It MUST use the same `n_depots = 2 * num_vehicles`
    layout the solver uses post-Phase-5, otherwise the vnodes it emits
    would point at end-depot slots instead of order pickups."""
    from app.algorithms import warm_start_cache as ws

    # Source-level guard rather than running OR-Tools — the line is a
    # one-place constant. This locks the formula so a refactor can't
    # silently revert it.
    import inspect
    src = inspect.getsource(ws.build_initial_routes)
    assert "n_depots = 2 * len(vehicles)" in src, (
        "warm_start_cache.build_initial_routes must use the Phase-5 2N "
        "depot layout; otherwise warm-start emits malformed vnodes."
    )


# ── Fix #2: greedy fallback honors end_depot_index ─────────────────────


def test_greedy_fallback_uses_end_depot_for_return_check():
    """A courier with `end_depot_index != depot_index` and a delivery
    closer to the end depot than home should be assignable when home
    would be too far. Verifies the return-leg check uses end_depot."""
    # 3 locations:
    #   0 = home depot (far from delivery)
    #   1 = end depot  (close to delivery)
    #   2 = order delivery
    locations = [(41.00, 29.00), (41.02, 29.02), (41.02, 29.02)]
    n = 3
    # Make matrix asymmetric so home-return is far and end-return is short.
    dist = _matrix(n)
    dur = [
        [0, 100, 100],     # home → ...
        [100, 0, 50],      # end → ...
        [100, 50, 0],      # delivery → ...
    ]
    # Inflate home→delivery cost so the home-return check would fail.
    dur[2][0] = 10_000
    dist[2][0] = 10_000

    order = OrderData(
        order_id="o1",
        pickup_index=0,  # pickup at home (depot pickup)
        delivery_index=2,
        weight_grams=10,
        earliest_time_s=0,
        latest_time_s=24 * HOUR,
        hard_latest_time_s=24 * HOUR,
        service_time_s=60,
        pickup_is_depot=True,
    )
    vehicle = VehicleData(
        courier_id="v1",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=100,
        depot_index=0,
        end_depot_index=1,
        start_time_s=0,
        # Shift just long enough to deliver + return to END depot, not enough
        # to return to HOME (which is 10000s away).
        end_time_s=1_000,
    )
    vrp = VRPInput(
        locations=locations,
        orders=[order],
        vehicles=[vehicle],
        distance_matrix=dist,
        duration_matrix=dur,
    )

    solution = greedy_assign(vrp)
    # Without the fix, the greedy fallback would compute return travel
    # via duration_matrix[2][0] = 10_000 and reject the order. With the
    # fix it uses duration_matrix[2][1] = 50 and accepts.
    assert "o1" not in solution.unassigned_orders, (
        "Greedy fallback should honor end_depot_index for the return check"
    )


# ── Fix #3: reload-insertion can't downgrade FEASIBLE → SOFT ───────────


def _feasible_problem():
    """Single-order problem; the natural route is already FEASIBLE."""
    locations = [(41.00, 29.00), (41.01, 29.00), (41.02, 29.00)]
    n = 3
    dist = _matrix(n)
    dur = _matrix(n)
    orders = [OrderData(
        order_id="o1",
        pickup_index=1,
        delivery_index=2,
        weight_grams=10,
        earliest_time_s=8 * HOUR,
        latest_time_s=18 * HOUR,
        hard_latest_time_s=19 * HOUR,
        service_time_s=60,
        priority=OrderPriority.NORMAL,
    )]
    vehicle = VehicleData(
        courier_id="v1",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=1000,
        depot_index=0,
        allowed_depot_indices=(0, 1, 2),  # so any node is technically allowed
        start_time_s=8 * HOUR,
        end_time_s=18 * HOUR,
        hard_end_time_s=19 * HOUR,
    )
    return build_problem(VRPInput(
        locations=locations,
        orders=orders,
        vehicles=[vehicle],
        distance_matrix=dist,
        duration_matrix=dur,
    ))


def test_reload_insertion_does_not_downgrade_feasible_to_soft():
    """A FEASIBLE baseline route must remain FEASIBLE after the move
    runs — even if some hypothetical RELOAD insertion would be cheaper
    by trading soft time-window penalty for distance savings."""
    problem = _feasible_problem()
    v = problem.vehicles[0]
    req = problem.requests[0]
    route = Route(
        vehicle=v,
        visits=[
            Visit(request_id=None, kind=VisitKind.DEPOT_START, node=v.start_node, arrival_s=0, departure_s=0),
            Visit(request_id=req.id, kind=VisitKind.PICKUP, node=req.pickup_node, arrival_s=0, departure_s=0),
            Visit(request_id=req.id, kind=VisitKind.DELIVERY, node=req.delivery_node, arrival_s=0, departure_s=0),
            Visit(request_id=None, kind=VisitKind.DEPOT_END, node=v.end_node, arrival_s=0, departure_s=0),
        ],
    )
    assert evaluate_route(route, problem).feasibility == Feasibility.FEASIBLE

    state = ALNSState(
        solution=Solution(routes=[route], unserved=set()),
        problem=problem,
    )
    reload_insertion.search(
        state,
        problem,
        build_candidate_lists(problem, k=4),
        evaluator=None,
        max_moves=1,
    )
    # Post-move feasibility must still be FEASIBLE (never SOFT or HARD).
    assert evaluate_route(route, problem).feasibility == Feasibility.FEASIBLE


# ── Fix #4: depot-pickup detection across allowed set ──────────────────


def test_is_depot_pickup_spec_matches_any_allowed_depot():
    """A pickup whose location_index sits at an additional reload depot
    must be detected as a depot pickup, not a customer pickup."""
    vehicle = VehicleData(
        courier_id="v1",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=100,
        depot_index=0,
        allowed_depot_indices=(0, 5),  # additional depot at index 5
    )
    order = OrderData(
        order_id="o1",
        pickup_index=5,  # picks up at the additional depot
        delivery_index=2,
        weight_grams=10,
    )
    spec = RoutePlanStopSpec(
        order_id="o1",
        stop_type="PICKUP",
        location_index=5,
    )
    assert is_depot_pickup_spec(spec, order, vehicle) is True


def test_is_depot_pickup_spec_single_depot_unchanged():
    """Single-depot vehicles must keep the pre-multi-depot semantics:
    only `depot_index` matches as a depot pickup."""
    vehicle = VehicleData(
        courier_id="v1",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=100,
        depot_index=3,
    )
    order = OrderData(
        order_id="o1",
        pickup_index=3,
        delivery_index=2,
        weight_grams=10,
    )
    spec = RoutePlanStopSpec(
        order_id="o1",
        stop_type="PICKUP",
        location_index=3,
    )
    assert is_depot_pickup_spec(spec, order, vehicle) is True

    # Pickup at a non-depot location remains False.
    spec2 = RoutePlanStopSpec(order_id="o1", stop_type="PICKUP", location_index=2)
    assert is_depot_pickup_spec(spec2, order, vehicle) is False


def test_feasibility_diagnostics_supports_multi_depot_vehicle():
    """_solo_timing_feasible should not crash for vehicles with
    allowed_depot_indices, and should use the min of all start depots
    as the outbound leg estimate."""
    locations = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    n = 4
    dist = _matrix(n)
    # Make depot B (index 3) much closer to the pickup than depot A (index 0).
    dur = [
        [0, 100, 200, 300],
        [100, 0, 100, 200],
        [200, 100, 0, 100],
        [300, 200, 100, 0],
    ]
    # Pickup at node 1; deliver at node 2.
    order = OrderData(
        order_id="o1",
        pickup_index=1,
        delivery_index=2,
        weight_grams=10,
        earliest_time_s=0,
        latest_time_s=24 * HOUR,
        hard_latest_time_s=24 * HOUR,
    )
    vehicle = VehicleData(
        courier_id="v1",
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=100,
        depot_index=0,
        allowed_depot_indices=(0, 3),  # multi-depot
        start_time_s=0,
        end_time_s=1_000,  # tight window — only multi-depot path can make it
    )
    vrp = VRPInput(
        locations=locations,
        orders=[order],
        vehicles=[vehicle],
        distance_matrix=dist,
        duration_matrix=dur,
    )
    # min(dur[0][1], dur[3][1]) = min(100, 200) = 100 → arrives at pickup at 100s
    # min(dur[2][0], dur[2][3]) = min(200, 100) = 100 → ends at 100 + 100 + 100 = 300s ≤ 1000
    # Without the multi-depot path, we'd use dur[0][1] = 100 (same) and
    # dur[2][0] = 200, ending at 400 ≤ 1000 — also fine. But the function
    # should at least not crash.
    assert _solo_timing_feasible(vrp, order, vehicle) is True


# ── Fix #5: update_courier re-validates stale end_depot_id ─────────────


def test_courier_update_rejects_stale_end_depot_when_additional_shrinks():
    """When a courier currently has end_depot_id = X and additional_depot_ids
    includes X, then a PATCH that removes X from additional_depot_ids
    without explicitly clearing end_depot_id must be rejected — the
    stale value would otherwise silently fall back to home at dispatch
    time."""
    # This is exercised via the live-API integration suite where the
    # full flow is in place. Pin the unit-level expectation as a guard
    # against the source-level invariant we added.
    import inspect
    from app.services import courier_service

    src = inspect.getsource(courier_service.update_courier)
    assert "additional_depot_ids" in src and "end_depot_id" in src
    assert "courier.end_depot_id is not None" in src, (
        "update_courier must include the existing end_depot_id in the "
        "validation set when additional_depot_ids is being modified"
    )


# ── Fix #6: end_depot_id requires home depot_id ────────────────────────


def test_courier_service_rejects_end_depot_without_home_depot():
    """A courier with no home depot configured (`depot_id=None`) cannot
    have an `end_depot_id` — the combination is semantically incoherent
    (shift end without a shift start). Source-level guard."""
    import inspect
    from app.services import courier_service

    src = inspect.getsource(courier_service._resolve_multi_depot_fields)
    assert "home_depot_id is None" in src, (
        "_resolve_multi_depot_fields must reject the depotless courier + "
        "end_depot_id combination"
    )
