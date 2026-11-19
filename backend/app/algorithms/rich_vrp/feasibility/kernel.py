"""Feasibility kernel for the rich VRP engine.

Provides full route re-evaluation and incremental feasibility checking.
The SegmentCache (cache.py) accelerates these checks in M3 local search.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from app.algorithms.rich_vrp.domain.models import (
    CostBreakdown,
    Problem,
    Request,
    Route,
    Vehicle,
    Visit,
    VisitKind,
)
from app.algorithms.rich_vrp.objective.cost import compute_route_cost


class Feasibility(Enum):
    FEASIBLE = "FEASIBLE"
    SOFT_INFEASIBLE = "SOFT_INFEASIBLE"   # only soft windows / overtime violated
    HARD_INFEASIBLE = "HARD_INFEASIBLE"   # hard window, capacity, or precedence violated


@dataclass(frozen=True, slots=True)
class Violation:
    kind: str   # "time_window", "capacity", "precedence", "compatibility", "distance", "stops"
    detail: str


@dataclass(frozen=True, slots=True)
class SoftMetrics:
    lateness_minutes: int = 0
    overtime_minutes: int = 0


@dataclass
class RouteEvaluation:
    feasibility: Feasibility
    hard_violations: list[Violation]
    soft_metrics: SoftMetrics
    cost: CostBreakdown


def evaluate_route(route: Route, problem: Problem) -> RouteEvaluation:
    """Full re-evaluation of a route. Updates visit arrival/departure times in place."""
    vehicle = route.vehicle
    matrix = problem.matrix
    hard_violations: list[Violation] = []

    cumulative_load: dict[str, int] = dict.fromkeys(vehicle.capacity, 0)
    prev_node = vehicle.start_node
    prev_departure = vehicle.shift_window[0]
    delivery_order: dict[str, int] = {}   # request_id -> sequence position of pickup
    sequence_pos = 0

    # Phase 2.3 — per-segment battery tracking. Active only when the
    # vehicle's `full_range_m` is set (i.e. the dispatch helper marked
    # the courier as EV-auto-charging-eligible). When inactive, the
    # route-wide `max_distance_m` check below behaves exactly as
    # before. When active, `remaining_range_m` is decremented per-arc
    # and reset to `full_range_m` when a charging-request pickup is
    # processed; a hard violation fires the instant remaining_range_m
    # would go negative on any leg.
    battery_aware = vehicle.full_range_m > 0
    remaining_range_m = vehicle.max_distance_m if battery_aware else 0
    battery_violation = False

    for visit in route.visits:
        if visit.kind == VisitKind.DEPOT_START:
            visit.arrival_s = vehicle.shift_window[0]
            visit.departure_s = vehicle.shift_window[0]
            prev_node = visit.node
            prev_departure = visit.departure_s
            continue

        travel = matrix.duration(prev_node, visit.node, vehicle.profile)
        arrival = prev_departure + travel
        visit.arrival_s = arrival

        # Per-segment battery accounting. Done here so we have access to
        # both prev_node and visit.node before the kind-specific block
        # potentially resets state. The reset on charging visits happens
        # inside the PICKUP branch below.
        if battery_aware:
            leg_m = matrix.distance(prev_node, visit.node, vehicle.profile)
            remaining_range_m -= leg_m
            if remaining_range_m < 0 and not battery_violation:
                hard_violations.append(
                    Violation(
                        "battery",
                        f"vehicle would run out of battery {-remaining_range_m}m before {visit.node}",
                    )
                )
                # Note: we don't `break` — letting the loop continue
                # ensures arrival_s + cumulative_load still get computed
                # for downstream code that reads them. We just remember
                # that we already flagged the violation so we don't
                # double-report on subsequent legs.
                battery_violation = True

        # Time-window handling per visit kind
        if visit.kind == VisitKind.PICKUP and visit.request_id:
            try:
                req = problem.request(visit.request_id)
            except KeyError:
                req = None
            if req is not None:
                tw_start, tw_end = req.pickup_window
                if arrival < tw_start:
                    arrival = tw_start  # wait
                # Pickup window close is a hard constraint (production validator
                # treats it as an error). The previous check used hard_delivery_deadline
                # by mistake, which let the engine accept solutions the validator
                # would reject — forcing fallback rescue.
                if arrival > tw_end:
                    hard_violations.append(
                        Violation(
                            "time_window",
                            f"pickup {visit.request_id} arrives after pickup window close ({arrival - tw_end}s late)",
                        )
                    )
                service = req.service_time_pickup_s
                # Check pickup window (soft — late pickup just propagates)
                departure = arrival + service
                # Load up
                for dim, val in req.load.items():
                    cumulative_load[dim] = cumulative_load.get(dim, 0) + val
                    cap = vehicle.capacity.get(dim, 0)
                    if cap > 0 and cumulative_load[dim] > cap:
                        hard_violations.append(Violation("capacity", f"capacity exceeded on {dim} at pickup {visit.request_id}"))
                delivery_order[visit.request_id] = sequence_pos
                # Phase 2.3 — charging-request pickup means the courier
                # has been at the charger for `service_time_pickup_s`
                # seconds. Refill remaining range to the post-charge
                # budget. Once we already flagged a battery violation
                # this run, we still reset so downstream legs get
                # evaluated under the post-charge budget.
                if battery_aware and req.is_charging:
                    remaining_range_m = vehicle.full_range_m

        elif visit.kind == VisitKind.DELIVERY and visit.request_id:
            try:
                req = problem.request(visit.request_id)
            except KeyError:
                req = None
            if req is not None:
                tw_start, tw_end = req.delivery_window
                if arrival < tw_start:
                    arrival = tw_start  # wait
                # Hard deadline check
                if req.hard_delivery_deadline is not None and arrival > req.hard_delivery_deadline:
                    hard_violations.append(Violation("time_window", f"delivery {visit.request_id} past hard deadline"))
                # Precedence: pickup must have appeared before delivery
                if visit.request_id not in delivery_order:
                    hard_violations.append(Violation("precedence", f"delivery {visit.request_id} without prior pickup"))
                service = req.service_time_delivery_s
                departure = arrival + service
                # Unload
                for dim, val in req.load.items():
                    cumulative_load[dim] = cumulative_load.get(dim, 0) - val
            else:
                departure = arrival

        elif visit.kind == VisitKind.DEPOT_END:
            departure = arrival

        elif visit.kind == VisitKind.RELOAD:
            departure = arrival
            # Phase 3 multi-depot — RELOAD is only valid at a depot the
            # vehicle is allowed to serve. Empty allowed_depot_nodes is
            # the single-depot sentinel, equivalent to (start_node,).
            allowed = vehicle.allowed_depot_nodes or (vehicle.start_node,)
            if visit.node not in allowed:
                hard_violations.append(
                    Violation(
                        "depot",
                        f"RELOAD at node {visit.node} not in allowed depots {allowed}",
                    )
                )
            # Reload resets the load to zero (vehicle goes back and reloads)
            for dim in cumulative_load:
                cumulative_load[dim] = 0

        elif visit.kind == VisitKind.BREAK:
            try:
                req = None
            except Exception:
                req = None
            # Break has a fixed duration from vehicle config
            departure = arrival + vehicle.break_duration_s

        else:
            departure = arrival

        visit.arrival_s = arrival
        visit.departure_s = departure
        visit.cumulative_load = dict(cumulative_load)
        prev_node = visit.node
        prev_departure = departure
        sequence_pos += 1

    # Compatibility check
    if not _check_compatibility_flags(route, problem):
        hard_violations.append(Violation("compatibility", "vehicle incompatible with one or more requests"))

    # Distance constraint. Battery-aware routes (full_range_m > 0) use
    # the per-segment check above instead of this route-wide cap, since
    # a charging visit can legitimately push total distance beyond the
    # initial budget. Non-EV / non-charging vehicles fall through here.
    if (
        not battery_aware
        and vehicle.max_distance_m > 0
        and route.total_distance_m > vehicle.max_distance_m
    ):
        hard_violations.append(Violation("distance", f"route distance {route.total_distance_m}m exceeds max {vehicle.max_distance_m}m"))

    # Stop count (exclude depot visits)
    actual_stops = sum(1 for v in route.visits if v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END))
    if vehicle.max_stops > 0 and actual_stops > vehicle.max_stops:
        hard_violations.append(Violation("stops", f"{actual_stops} stops exceeds max {vehicle.max_stops}"))

    # Compute cost (also captures lateness_minutes, overtime_minutes)
    cost = compute_route_cost(route, problem)
    route.cost = cost
    route.total_distance_m = _compute_distance(route, problem)
    route.total_duration_s = _compute_duration(route, vehicle)

    soft_metrics = SoftMetrics(
        lateness_minutes=cost.late_minutes,
        overtime_minutes=cost.overtime_minutes,
    )
    has_soft = cost.late_minutes > 0 or cost.overtime_minutes > 0

    if hard_violations:
        feasibility = Feasibility.HARD_INFEASIBLE
    elif has_soft:
        feasibility = Feasibility.SOFT_INFEASIBLE
    else:
        feasibility = Feasibility.FEASIBLE

    route.feasible = feasibility != Feasibility.HARD_INFEASIBLE

    return RouteEvaluation(
        feasibility=feasibility,
        hard_violations=hard_violations,
        soft_metrics=soft_metrics,
        cost=cost,
    )


def evaluate_segment(
    route: Route,
    start_idx: int,
    end_idx: int,
    problem: Problem,
) -> RouteEvaluation:
    """Partial re-evaluation between start_idx and end_idx (inclusive).

    In M1 this delegates to full evaluate_route; M3 replaces with delta evaluation.
    """
    return evaluate_route(route, problem)


def is_compatible(request: Request, vehicle: Vehicle) -> bool:
    """Return True if request can be assigned to vehicle."""
    if not request.required_tags.issubset(vehicle.tags):
        return False
    if request.allowed_vehicle_indices is not None and vehicle.index not in request.allowed_vehicle_indices:
        return False
    if request.is_fragile and not vehicle.allow_fragile:
        return False
    return not (request.is_temp_sensitive and not vehicle.allow_temp_sensitive)


def feasible_insertion_positions(
    route: Route,
    request: Request,
    problem: Problem,
) -> Iterator[tuple[int, int]]:
    """Yield valid (pickup_idx, delivery_idx) pairs for inserting request into route.

    Both indices are insert-before positions into the visits list, suitable for
    direct use with `list.insert(pickup_idx, ...)` then `list.insert(delivery_idx, ...)`.
    Pickup must be after DEPOT_START (index 1 minimum).
    Delivery must be after pickup in the resulting list.

    Fast path: builds a SegmentCache once (O(n)) then uses check_pd_insertion_c
    (O(1) for adjacent, O(de-pu) for non-adjacent) via the Cython extension when
    compiled, or the pure-Python equivalent.  This replaces the original O(n³)
    implementation (O(n²) pairs × O(n) evaluate_route each).
    """
    if not is_compatible(request, route.vehicle):
        return

    n = len(route.visits)
    vehicle = route.vehicle

    # Try the Cython-accelerated path via SegmentCache
    try:
        from app.algorithms.rich_vrp.feasibility.cache import SegmentCache  # noqa: PLC0415
        from app.algorithms.rich_vrp.local_search.evaluator_cpp import (
            check_pd_insertion_c,  # noqa: PLC0415
        )
        _fast_path = True
    except ImportError:
        _fast_path = False

    if _fast_path:
        import numpy as np  # noqa: PLC0415
        cache = SegmentCache.build(route, problem)
        dur_np = problem.matrix.np_duration

        pu_node = int(request.pickup_node)
        de_node = int(request.delivery_node)
        pu_tw_open = int(request.pickup_window[0])
        # The Cython feasibility check doesn't accept a pickup_window_close
        # parameter — it treats pickup TW close as +inf. We post-filter below
        # using the cached forward arrivals so the production validator
        # (which DOES treat pickup TW close as a hard error) doesn't reject
        # solutions the engine accepted. Without this, the engine can insert
        # pickups that arrive 1-2 hours after the window closes.
        pu_tw_close = int(request.pickup_window[1])
        de_tw_open = int(request.delivery_window[0])
        de_hard_deadline = int(request.hard_delivery_deadline) if request.hard_delivery_deadline is not None else -1
        pu_service = int(request.service_time_pickup_s)
        de_service = int(request.service_time_delivery_s)
        shift_hard_end = int(vehicle.hard_shift_end) if vehicle.hard_shift_end else int(vehicle.shift_window[1]) + 7200

        fwd_arr = np.array(cache.forward_arrival, dtype=np.int64)
        bwd_lat = np.array(cache.backward_latest, dtype=np.int64)
        nodes_np = cache.nodes_np
        tw_open_np = cache.tw_open_np
        svc_np = cache.service_np

        # Precompute per-visit cumulative loads for capacity running-max check.
        # Only track dimensions the request actually uses with a vehicle cap > 0.
        req_load = request.load
        dims_with_cap = [
            dim for dim in req_load if vehicle.capacity.get(dim, 0) > 0
        ]
        cumloads_per_dim: dict[str, list[int]] = {
            dim: [v.cumulative_load.get(dim, 0) for v in route.visits]
            for dim in dims_with_cap
        }

        for pickup_pos in range(1, n):
            # Running max of cumulative load in [pickup_pos-1, delivery_pos-2].
            # This represents the maximum load that would exist in the segment
            # between our new pickup and delivery after insertion — any position
            # in that range gets +request.load on top of its current load, so
            # we must ensure max_in_range + request.load <= vehicle.capacity.
            running_max = {
                dim: cumloads_per_dim[dim][pickup_pos - 1]
                for dim in dims_with_cap
            }

            for delivery_pos in range(pickup_pos + 1, n + 1):
                # Extend running max to include the new element as delivery_pos grows.
                if delivery_pos > pickup_pos + 1:
                    k = delivery_pos - 2
                    if k < n:
                        for dim in dims_with_cap:
                            cl = cumloads_per_dim[dim][k]
                            if cl > running_max[dim]:
                                running_max[dim] = cl

                # Capacity check (Python, O(|dims|) per pair, O(1) amortized over
                # the inner loop thanks to the running max).
                if dims_with_cap and any(
                    running_max[dim] + req_load[dim] > vehicle.capacity.get(dim, 0)
                    for dim in dims_with_cap
                ):
                    continue

                # Time-window check via Cython hot path.
                # Capacity is handled above; pass dummy values to disable the
                # Cython-internal capacity check.
                feasible = check_pd_insertion_c(
                    fwd_arr, svc_np, bwd_lat, nodes_np, tw_open_np, dur_np,
                    pickup_pos, delivery_pos,
                    pu_node, de_node,
                    pu_tw_open, de_tw_open,
                    de_hard_deadline,
                    pu_service, de_service,
                    999_999_999, 0,
                    shift_hard_end,
                )
                if feasible:
                    # Post-filter: the Cython kernel doesn't check pickup_window
                    # close (it treats it as +inf). Confirm the candidate against
                    # the full evaluate_route to catch any constraint the Cython
                    # path skips. This is O(route-length) per yielded candidate
                    # but only fires when Cython already said "feasible", which
                    # is rare in dense routes.
                    pickup_visit = Visit(
                        request_id=request.id, kind=VisitKind.PICKUP,
                        node=request.pickup_node, arrival_s=0, departure_s=0,
                    )
                    delivery_visit = Visit(
                        request_id=request.id, kind=VisitKind.DELIVERY,
                        node=request.delivery_node, arrival_s=0, departure_s=0,
                    )
                    new_visits = list(route.visits)
                    new_visits.insert(pickup_pos, pickup_visit)
                    new_visits.insert(delivery_pos, delivery_visit)
                    trial_route = Route(
                        vehicle=route.vehicle,
                        visits=new_visits,
                        total_distance_m=0,
                        total_duration_s=0,
                    )
                    if evaluate_route(trial_route, problem).feasibility == Feasibility.HARD_INFEASIBLE:
                        continue
                    yield (pickup_pos, delivery_pos)
                    break  # take first feasible delivery pos for this pickup pos
        return

    # Fallback: original O(n²×n) path
    for pickup_pos in range(1, n):
        for delivery_pos in range(pickup_pos + 1, n + 1):
            pickup_visit = Visit(
                request_id=request.id,
                kind=VisitKind.PICKUP,
                node=request.pickup_node,
                arrival_s=0,
                departure_s=0,
            )
            delivery_visit = Visit(
                request_id=request.id,
                kind=VisitKind.DELIVERY,
                node=request.delivery_node,
                arrival_s=0,
                departure_s=0,
            )
            new_visits = list(route.visits)
            new_visits.insert(pickup_pos, pickup_visit)
            new_visits.insert(delivery_pos, delivery_visit)

            trial_route = Route(
                vehicle=route.vehicle,
                visits=new_visits,
                total_distance_m=0,
                total_duration_s=0,
            )
            evaluation = evaluate_route(trial_route, problem)
            if evaluation.feasibility != Feasibility.HARD_INFEASIBLE:
                yield (pickup_pos, delivery_pos)
                break  # take first feasible delivery pos for this pickup pos


def _check_compatibility_flags(route: Route, problem: Problem) -> bool:
    for visit in route.visits:
        if visit.kind not in (VisitKind.PICKUP, VisitKind.DELIVERY):
            continue
        if visit.request_id is None:
            continue
        try:
            req = problem.request(visit.request_id)
        except KeyError:
            continue
        if not is_compatible(req, route.vehicle):
            return False
    return True


def _compute_distance(route: Route, problem: Problem) -> int:
    vehicle = route.vehicle
    matrix = problem.matrix
    distance = 0
    prev_node = vehicle.start_node
    for visit in route.visits:
        if visit.kind == VisitKind.DEPOT_START:
            prev_node = visit.node
            continue
        distance += matrix.distance(prev_node, visit.node, vehicle.profile)
        prev_node = visit.node
    return distance


def _compute_duration(route: Route, vehicle: Vehicle) -> int:
    actual = [v for v in route.visits if v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)]
    if not actual:
        return 0
    return actual[-1].departure_s - vehicle.shift_window[0]
