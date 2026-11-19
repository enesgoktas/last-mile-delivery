"""Human-dispatcher baseline routing heuristic.

This is intentionally not an optimizer.  It mirrors how a competent manual
dispatcher tends to work: draw delivery territories, put nearby couriers on
nearby territories, build routes by urgency and local road time, then do a
final pass over anything missed before publishing the plan.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from app.algorithms.vrp_solver import (
    OrderData,
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VehicleData,
    VRPInput,
    VRPSolution,
    build_route_cost_breakdown,
    build_solution_cost_breakdown,
)
from app.models.enums import OrderPriority, VehicleType

_PRIORITY_RANK = {
    OrderPriority.URGENT: 0,
    OrderPriority.HIGH: 1,
    OrderPriority.NORMAL: 2,
    OrderPriority.LOW: 3,
}

_EXHAUSTIVE_INSERTION_STOP_LIMIT = 48
_NEARBY_INSERTION_CANDIDATES = 5


@dataclass(frozen=True, slots=True)
class DispatchZone:
    pickup_index: int
    order_ids: tuple[str, ...]
    center_lat: float
    center_lon: float
    start_angle: float
    end_angle: float


@dataclass(frozen=True, slots=True)
class _StopIntent:
    order_id: str
    stop_type: str


@dataclass(frozen=True, slots=True)
class _RouteSimulation:
    route: PlannedRoute
    assigned_order_ids: frozenset[str]


@dataclass
class _RouteBoard:
    vehicle: VehicleData
    intents: list[_StopIntent] = field(default_factory=list)
    simulation: _RouteSimulation | None = None
    assigned_zone_count: int = 0


def build_sweep_zones(vrp_input: VRPInput) -> list[DispatchZone]:
    """Split orders into deterministic pickup-source sweep zones."""
    if not vrp_input.orders:
        return []

    orders_by_id = {order.order_id: order for order in vrp_input.orders}
    grouped: dict[int, list[OrderData]] = {}
    for order in sorted(vrp_input.orders, key=_order_sort_key):
        grouped.setdefault(order.pickup_index, []).append(order)

    zones: list[DispatchZone] = []
    vehicle_count = max(len(vrp_input.vehicles), 1)
    target_zone_size = max(1, math.ceil(len(vrp_input.orders) / vehicle_count))

    for pickup_index in sorted(grouped):
        group = grouped[pickup_index]
        anchor_lat, anchor_lon = vrp_input.locations[pickup_index]
        sorted_group = sorted(
            group,
            key=lambda order: (
                _angle_from_anchor(vrp_input, anchor_lat, anchor_lon, order.delivery_index),
                order.latest_time_s,
                order.order_id,
            ),
        )
        zone_count = max(1, math.ceil(len(sorted_group) / target_zone_size))
        chunk_size = max(1, math.ceil(len(sorted_group) / zone_count))

        for start in range(0, len(sorted_group), chunk_size):
            chunk = sorted_group[start:start + chunk_size]
            angles = [
                _angle_from_anchor(vrp_input, anchor_lat, anchor_lon, order.delivery_index)
                for order in chunk
            ]
            center_lat = sum(vrp_input.locations[order.delivery_index][0] for order in chunk) / len(chunk)
            center_lon = sum(vrp_input.locations[order.delivery_index][1] for order in chunk) / len(chunk)
            zones.append(
                DispatchZone(
                    pickup_index=pickup_index,
                    order_ids=tuple(order.order_id for order in chunk),
                    center_lat=center_lat,
                    center_lon=center_lon,
                    start_angle=min(angles),
                    end_angle=max(angles),
                )
            )

    zones.sort(
        key=lambda zone: (
            zone.pickup_index,
            zone.start_angle,
            zone.end_angle,
            tuple(orders_by_id[order_id].latest_time_s for order_id in zone.order_ids),
            zone.order_ids,
        )
    )
    return zones


def solve_human_dispatcher_baseline(vrp_input: VRPInput) -> VRPSolution:
    """Build a deterministic, human-style baseline route plan."""
    if not vrp_input.orders:
        return VRPSolution(
            routes=[],
            unassigned_orders=[],
            total_distance_m=0,
            total_duration_s=0,
            solver_status=SolverStatus.FEASIBLE,
        )
    if not vrp_input.vehicles:
        return VRPSolution(
            routes=[],
            unassigned_orders=[order.order_id for order in vrp_input.orders],
            total_distance_m=0,
            total_duration_s=0,
            solver_status=SolverStatus.NOT_FOUND,
        )

    orders_by_id = {order.order_id: order for order in vrp_input.orders}
    boards = [
        _RouteBoard(vehicle=vehicle)
        for vehicle in sorted(
            vrp_input.vehicles,
            key=lambda vehicle: (
                vehicle.depot_index,
                vehicle.start_time_s,
                vehicle.end_time_s,
                vehicle.courier_id,
            ),
        )
    ]

    zones = build_sweep_zones(vrp_input)
    zone_assignments = _assign_zones_to_boards(vrp_input, zones, boards, orders_by_id)

    assigned_order_ids: set[str] = set()
    for board, zone_orders in zone_assignments:
        remaining = [order for order in zone_orders if order.order_id not in assigned_order_ids]
        _build_board_route(vrp_input, board, remaining, assigned_order_ids, orders_by_id)

    unassigned = [
        order
        for order in sorted(vrp_input.orders, key=_order_sort_key)
        if order.order_id not in assigned_order_ids
    ]
    _repair_unassigned_orders(vrp_input, boards, unassigned, assigned_order_ids, orders_by_id)

    routes = [
        board.simulation.route
        for board in boards
        if board.simulation is not None and board.simulation.route.stops
    ]
    unassigned_order_ids = [
        order.order_id
        for order in sorted(vrp_input.orders, key=_order_sort_key)
        if order.order_id not in assigned_order_ids
    ]
    cost_breakdown = build_solution_cost_breakdown(
        routes=routes,
        unassigned_order_ids=unassigned_order_ids,
        orders_by_id=orders_by_id,
    )
    return VRPSolution(
        routes=routes,
        unassigned_orders=unassigned_order_ids,
        total_distance_m=sum(route.total_distance_m for route in routes),
        total_duration_s=sum(route.total_duration_s for route in routes),
        solver_status=SolverStatus.FEASIBLE if routes else SolverStatus.NOT_FOUND,
        cost_breakdown=cost_breakdown,
    )


def _assign_zones_to_boards(
    vrp_input: VRPInput,
    zones: Sequence[DispatchZone],
    boards: list[_RouteBoard],
    orders_by_id: dict[str, OrderData],
) -> list[tuple[_RouteBoard, list[OrderData]]]:
    orders_for_board: dict[str, list[OrderData]] = {
        board.vehicle.courier_id: []
        for board in boards
    }

    for zone in zones:
        zone_orders = [orders_by_id[order_id] for order_id in zone.order_ids]
        board = min(
            boards,
            key=lambda candidate: _zone_board_score(vrp_input, zone, zone_orders, candidate),
        )
        board.assigned_zone_count += 1
        orders_for_board[board.vehicle.courier_id].extend(zone_orders)

    return [
        (board, orders_for_board[board.vehicle.courier_id])
        for board in boards
        if orders_for_board[board.vehicle.courier_id]
    ]


def _build_board_route(
    vrp_input: VRPInput,
    board: _RouteBoard,
    candidate_orders: list[OrderData],
    assigned_order_ids: set[str],
    orders_by_id: dict[str, OrderData],
) -> None:
    remaining = [
        order
        for order in sorted(candidate_orders, key=_order_sort_key)
        if order.order_id not in assigned_order_ids
    ]

    while remaining:
        best: tuple[tuple[int, int, int, str], OrderData, list[_StopIntent], _RouteSimulation] | None = None
        for order in remaining:
            if order.order_id in assigned_order_ids:
                continue
            proposed = _preferred_append_intents(board.intents, order, board.vehicle)
            simulation = _simulate_intents(vrp_input, board.vehicle, proposed, orders_by_id)
            if simulation is None:
                continue
            score = _manual_next_order_score(vrp_input, board, order)
            if best is None or score < best[0]:
                best = (score, order, proposed, simulation)

        if best is None:
            break

        _, chosen, intents, simulation = best
        board.intents = intents
        board.simulation = simulation
        assigned_order_ids.add(chosen.order_id)
        remaining = [order for order in remaining if order.order_id != chosen.order_id]


def _repair_unassigned_orders(
    vrp_input: VRPInput,
    boards: list[_RouteBoard],
    unassigned: list[OrderData],
    assigned_order_ids: set[str],
    orders_by_id: dict[str, OrderData],
) -> None:
    for order in sorted(unassigned, key=_order_sort_key):
        if order.order_id in assigned_order_ids:
            continue
        existing_boards = [board for board in boards if board.simulation is not None]
        unused_boards = [board for board in boards if board.simulation is None]
        for candidate_boards in (existing_boards, unused_boards):
            best = _best_repair_insertion(vrp_input, candidate_boards, order, orders_by_id)
            if best is None:
                continue
            _, board, intents, simulation = best
            board.intents = intents
            board.simulation = simulation
            assigned_order_ids.add(order.order_id)
            break


def _best_repair_insertion(
    vrp_input: VRPInput,
    boards: Sequence[_RouteBoard],
    order: OrderData,
    orders_by_id: dict[str, OrderData],
) -> tuple[tuple[int, int, int, int, str], _RouteBoard, list[_StopIntent], _RouteSimulation] | None:
    best: tuple[tuple[int, int, int, int, str], _RouteBoard, list[_StopIntent], _RouteSimulation] | None = None
    for board in boards:
        for intents in _candidate_insertion_intents(vrp_input, board.intents, order, board.vehicle):
            simulation = _simulate_intents(vrp_input, board.vehicle, intents, orders_by_id)
            if simulation is None:
                continue
            current_distance = board.simulation.route.total_distance_m if board.simulation else 0
            current_duration = board.simulation.route.total_duration_s if board.simulation else 0
            score = (
                simulation.route.total_distance_m - current_distance,
                simulation.route.total_duration_s - current_duration,
                simulation.route.route_end_time_s,
                len(board.intents),
                board.vehicle.courier_id,
            )
            if best is None or score < best[0]:
                best = (score, board, intents, simulation)
    return best


def _preferred_append_intents(
    current: list[_StopIntent],
    order: OrderData,
    vehicle: VehicleData,
) -> list[_StopIntent]:
    pickup = _StopIntent(order.order_id, "PICKUP")
    delivery = _StopIntent(order.order_id, "DELIVERY")
    if not _is_depot_pickup_order(order, vehicle):
        return [*current, pickup, delivery]

    preload_index = 0
    for index, intent in enumerate(current):
        if intent.stop_type != "PICKUP":
            preload_index = index
            break
    else:
        preload_index = len(current)
    return [*current[:preload_index], pickup, *current[preload_index:], delivery]


def _candidate_insertion_intents(
    vrp_input: VRPInput,
    current: list[_StopIntent],
    order: OrderData,
    vehicle: VehicleData,
) -> Iterable[list[_StopIntent]]:
    pickup = _StopIntent(order.order_id, "PICKUP")
    delivery = _StopIntent(order.order_id, "DELIVERY")
    seen: set[tuple[_StopIntent, ...]] = set()

    def emit(candidate: list[_StopIntent]) -> list[_StopIntent] | None:
        key = tuple(candidate)
        if key in seen:
            return None
        seen.add(key)
        return candidate

    preferred = emit(_preferred_append_intents(current, order, vehicle))
    if preferred is not None:
        yield preferred

    if len(current) <= _EXHAUSTIVE_INSERTION_STOP_LIMIT:
        pickup_positions = range(0, len(current) + 1)
        delivery_positions = range(1, len(current) + 2)
    else:
        pickup_positions = _targeted_positions(
            vrp_input,
            current,
            order.pickup_index,
            always={0, len(current)},
        )
        delivery_positions = _targeted_positions(
            vrp_input,
            current,
            order.delivery_index,
            always={len(current) + 1},
        )

    for pickup_pos in pickup_positions:
        with_pickup = [*current[:pickup_pos], pickup, *current[pickup_pos:]]
        for delivery_pos in delivery_positions:
            if delivery_pos <= pickup_pos:
                continue
            adjusted_delivery_pos = min(delivery_pos, len(with_pickup))
            candidate = emit(
                [
                    *with_pickup[:adjusted_delivery_pos],
                    delivery,
                    *with_pickup[adjusted_delivery_pos:],
                ]
            )
            if candidate is not None:
                yield candidate


def _simulate_intents(
    vrp_input: VRPInput,
    vehicle: VehicleData,
    intents: Sequence[_StopIntent],
    orders_by_id: dict[str, OrderData],
) -> _RouteSimulation | None:
    if not intents:
        return None
    if vehicle.max_stops > 0 and _visible_intent_count(intents, orders_by_id, vehicle) > vehicle.max_stops:
        return None

    distance_matrix, duration_matrix = _vehicle_matrices(vrp_input, vehicle)
    current_loc = vehicle.depot_index
    current_time = vehicle.start_time_s
    total_distance = 0
    running_weight = 0
    running_volume = 0
    running_packages = 0
    break_taken = vehicle.break_duration_s <= 0
    vehicle_index = _vehicle_index(vrp_input, vehicle)
    picked: set[str] = set()
    delivered: set[str] = set()
    stops: list[PlannedStop] = []
    initial_depot_load_charged = False

    for intent in intents:
        order = orders_by_id[intent.order_id]
        if not _can_vehicle_ever_take_order(order, vehicle, vehicle_index):
            return None

        stop_index = order.pickup_index if intent.stop_type == "PICKUP" else order.delivery_index
        travel_s = duration_matrix[current_loc][stop_index]
        current_time, break_taken = _maybe_take_break_before_leg(
            current_time,
            travel_s,
            vehicle,
            break_taken,
        )
        arrival = current_time + travel_s
        total_distance += distance_matrix[current_loc][stop_index]

        if intent.stop_type == "PICKUP":
            if order.order_id in picked:
                return None
            earliest = order.pickup_earliest_time_s or 0
            latest = (
                order.pickup_latest_time_s
                if order.pickup_latest_time_s is not None
                else order.latest_time_s
            )
            depot_pickup = _is_depot_pickup_order(order, vehicle)
            if not depot_pickup:
                arrival = max(arrival, earliest)
            if not depot_pickup and arrival > latest:
                return None
            running_weight += order.weight_grams
            running_volume += order.volume_cm3
            running_packages += order.package_count
            picked.add(order.order_id)
        else:
            if order.order_id not in picked or order.order_id in delivered:
                return None
            arrival = max(arrival, order.earliest_time_s)
            latest = order.hard_latest_time_s or order.latest_time_s
            if arrival > latest:
                return None
            running_weight -= order.weight_grams
            running_volume -= order.volume_cm3
            running_packages -= order.package_count
            delivered.add(order.order_id)

        if not _load_within_limits(vehicle, running_weight, running_volume, running_packages):
            return None

        service_time_s = order.service_time_s
        if intent.stop_type == "PICKUP" and _is_depot_pickup_order(order, vehicle):
            service_time_s = 0
            if not delivered and not initial_depot_load_charged:
                service_time_s = order.service_time_s
                initial_depot_load_charged = True
        departure = arrival + service_time_s
        current_time = departure
        current_loc = stop_index
        lat, lon = vrp_input.locations[stop_index]
        stops.append(
            PlannedStop(
                order_id=order.order_id,
                stop_type=intent.stop_type,
                location_index=stop_index,
                lat=lat,
                lon=lon,
                arrival_time_s=arrival,
                departure_time_s=departure,
            )
        )

    if picked != delivered:
        return None

    # Phase 5 multi-depot — return leg targets `end_depot_index` when
    # set, otherwise the home depot. Single-depot vehicles
    # (`end_depot_index is None`) are unchanged.
    return_depot = (
        vehicle.end_depot_index
        if vehicle.end_depot_index is not None
        else vehicle.depot_index
    )
    return_travel_s = duration_matrix[current_loc][return_depot]
    current_time, break_taken = _maybe_take_break_before_leg(
        current_time,
        return_travel_s,
        vehicle,
        break_taken,
    )
    route_end_time = current_time + return_travel_s
    total_distance += distance_matrix[current_loc][return_depot]

    hard_end = vehicle.hard_end_time_s or vehicle.end_time_s
    if route_end_time > hard_end:
        return None
    if vehicle.break_duration_s > 0 and not break_taken and route_end_time > vehicle.break_latest_s:
        return None
    if vehicle.max_distance_m > 0 and total_distance > vehicle.max_distance_m:
        return None

    route_duration = max(route_end_time - vehicle.start_time_s, 0)
    route = PlannedRoute(
        courier_id=vehicle.courier_id,
        stops=stops,
        total_distance_m=total_distance,
        total_duration_s=route_duration,
        route_start_time_s=vehicle.start_time_s,
        route_end_time_s=route_end_time,
        cost_breakdown=build_route_cost_breakdown(
            vehicle=vehicle,
            route_distance_m=total_distance,
            route_duration_s=route_duration,
            route_end_time_s=route_end_time,
            stops=stops,
            orders_by_id=orders_by_id,
        ),
    )
    return _RouteSimulation(route=route, assigned_order_ids=frozenset(delivered))


def _maybe_take_break_before_leg(
    current_time: int,
    travel_s: int,
    vehicle: VehicleData,
    break_taken: bool,
) -> tuple[int, bool]:
    if break_taken or vehicle.break_duration_s <= 0:
        return current_time, break_taken
    if vehicle.break_latest_s <= vehicle.break_earliest_s:
        return current_time, True
    if current_time + travel_s <= vehicle.break_earliest_s:
        return current_time, break_taken
    if current_time >= vehicle.break_latest_s:
        return current_time, break_taken

    break_start = max(current_time, vehicle.break_earliest_s)
    if break_start + vehicle.break_duration_s <= vehicle.break_latest_s:
        return break_start + vehicle.break_duration_s, True
    return current_time, break_taken


def _targeted_positions(
    vrp_input: VRPInput,
    current: list[_StopIntent],
    target_index: int,
    *,
    always: set[int],
) -> list[int]:
    scored: list[tuple[int, int]] = []
    for idx, intent in enumerate(current):
        order = next(order for order in vrp_input.orders if order.order_id == intent.order_id)
        loc = order.pickup_index if intent.stop_type == "PICKUP" else order.delivery_index
        scored.append((vrp_input.duration_matrix[loc][target_index], idx))
        scored.append((vrp_input.duration_matrix[target_index][loc], idx + 1))
    positions = set(always)
    for _, pos in sorted(scored)[:_NEARBY_INSERTION_CANDIDATES]:
        positions.add(max(0, min(pos, len(current))))
    return sorted(positions)


def _manual_next_order_score(
    vrp_input: VRPInput,
    board: _RouteBoard,
    order: OrderData,
) -> tuple[int, int, int, str]:
    current_loc = board.vehicle.depot_index
    if board.simulation is not None and board.simulation.route.stops:
        current_loc = board.simulation.route.stops[-1].location_index
    _, duration_matrix = _vehicle_matrices(vrp_input, board.vehicle)
    if _is_depot_pickup_order(order, board.vehicle):
        road_time = duration_matrix[current_loc][order.delivery_index]
    else:
        road_time = (
            duration_matrix[current_loc][order.pickup_index]
            + duration_matrix[order.pickup_index][order.delivery_index]
        )
    return (
        _PRIORITY_RANK.get(order.priority, 2),
        order.latest_time_s,
        road_time,
        order.order_id,
    )


def _zone_board_score(
    vrp_input: VRPInput,
    zone: DispatchZone,
    zone_orders: Sequence[OrderData],
    board: _RouteBoard,
) -> tuple[int, int, int, int, int, str]:
    _, duration_matrix = _vehicle_matrices(vrp_input, board.vehicle)
    nearest_zone_index = min(
        (order.delivery_index for order in zone_orders),
        key=lambda index: duration_matrix[board.vehicle.depot_index][index],
    )
    compatible_count = sum(
        1 for order in zone_orders
        if _can_vehicle_ever_take_order(
            order,
            board.vehicle,
            _vehicle_index(vrp_input, board.vehicle),
        )
    )
    return (
        0 if compatible_count else 1,
        board.assigned_zone_count,
        0 if board.vehicle.depot_index == zone.pickup_index else 1,
        duration_matrix[board.vehicle.depot_index][nearest_zone_index],
        board.vehicle.start_time_s,
        board.vehicle.courier_id,
    )


def _can_vehicle_ever_take_order(
    order: OrderData,
    vehicle: VehicleData,
    vehicle_index: int | None,
) -> bool:
    if order.weight_grams > vehicle.capacity_grams:
        return False
    if vehicle.capacity_volume_cm3 > 0 and order.volume_cm3 > vehicle.capacity_volume_cm3:
        return False
    if vehicle.max_packages > 0 and order.package_count > vehicle.max_packages:
        return False
    if order.is_fragile and not vehicle.allow_fragile:
        return False
    if order.is_temp_sensitive and not vehicle.allow_temp_sensitive:
        return False
    if order.is_fragile and vehicle.vehicle_type == VehicleType.MOTORCYCLE:
        return False
    if order.required_vehicle_tags and not order.required_vehicle_tags.issubset(vehicle.vehicle_tags):
        return False
    return order.allowed_vehicle_indices is None or vehicle_index in order.allowed_vehicle_indices


def _vehicle_index(vrp_input: VRPInput, vehicle: VehicleData) -> int | None:
    for index, candidate in enumerate(vrp_input.vehicles):
        if candidate.courier_id == vehicle.courier_id:
            return index
    return None


def _load_within_limits(
    vehicle: VehicleData,
    weight: int,
    volume: int,
    packages: int,
) -> bool:
    if weight < 0 or volume < 0 or packages < 0:
        return False
    if vehicle.capacity_grams > 0 and weight > vehicle.capacity_grams:
        return False
    if vehicle.capacity_volume_cm3 > 0 and volume > vehicle.capacity_volume_cm3:
        return False
    return not (vehicle.max_packages > 0 and packages > vehicle.max_packages)


def _visible_intent_count(
    intents: Sequence[_StopIntent],
    orders_by_id: dict[str, OrderData],
    vehicle: VehicleData,
) -> int:
    return sum(
        0
        if intent.stop_type == "PICKUP"
        and _is_depot_pickup_order(orders_by_id[intent.order_id], vehicle)
        else 1
        for intent in intents
    )


def _is_depot_pickup_order(order: OrderData, vehicle: VehicleData) -> bool:
    """A pickup at any allowed depot (not just the home depot) is a
    depot pickup. Single-depot vehicles have `allowed_depot_indices=()`,
    so the fallback `(depot_index,)` matches the pre-multi-depot
    behavior exactly."""
    if order.pickup_is_depot:
        return True
    allowed = vehicle.allowed_depot_indices or (vehicle.depot_index,)
    return order.pickup_index in allowed


def _vehicle_matrices(
    vrp_input: VRPInput,
    vehicle: VehicleData,
) -> tuple[list[list[int]], list[list[int]]]:
    distance_matrix = (vrp_input.distance_matrices or {}).get(
        vehicle.osrm_profile,
        vrp_input.distance_matrix,
    )
    duration_matrix = (vrp_input.duration_matrices or {}).get(
        vehicle.osrm_profile,
        vrp_input.duration_matrix,
    )
    return distance_matrix, duration_matrix


def _angle_from_anchor(
    vrp_input: VRPInput,
    anchor_lat: float,
    anchor_lon: float,
    location_index: int,
) -> float:
    lat, lon = vrp_input.locations[location_index]
    return math.atan2(lat - anchor_lat, lon - anchor_lon)


def _order_sort_key(order: OrderData) -> tuple[int, int, int, str]:
    return (
        _PRIORITY_RANK.get(order.priority, 2),
        order.latest_time_s,
        order.earliest_time_s,
        order.order_id,
    )
