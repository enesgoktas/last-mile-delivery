"""
Cheapest insertion heuristic for real-time on-demand dispatch.

Design goals:
- Deterministic behavior.
- Strong constraint enforcement (capacity, shift, time windows).
- Low overhead per candidate: no trial list allocations and O(1) detour math.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.models.enums import OrderPriority, VehicleType

logger = logging.getLogger(__name__)

TravelMatrixProvider = Callable[
    [list[tuple[float, float]]],
    Awaitable[tuple[list[list[int]], str]],
]


@dataclass(frozen=True, slots=True)
class InsertionCandidate:
    courier_id: str
    route_id: str | None
    pickup_position: int
    delivery_position: int
    detour_cost_s: int
    weighted_cost: float


@dataclass(frozen=True, slots=True)
class InsertionResult:
    candidate: InsertionCandidate | None
    reason: str  # "inserted", "new_route", "no_eligible_courier"
    rejection_summary: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class ExistingStop:
    lat: float
    lon: float
    earliest_s: int
    latest_s: int
    arrival_s: int
    service_s: int
    weight_delta_g: int
    volume_delta_cm3: int = 0
    package_delta: int = 0
    passenger_delta: int = 0


@dataclass(frozen=True, slots=True)
class NewOrder:
    order_id: str
    pickup_lat: float
    pickup_lon: float
    delivery_lat: float
    delivery_lon: float
    weight_grams: int
    volume_cm3: int = 0
    package_count: int = 1
    is_fragile: bool = False
    is_temp_sensitive: bool = False
    earliest_s: int = 0
    latest_s: int = 0
    pickup_earliest_s: int = 0
    pickup_latest_s: int = 0
    priority: OrderPriority = OrderPriority.NORMAL
    service_time_s: int = 0
    is_ride: bool = False
    passenger_count: int = 0


@dataclass(frozen=True, slots=True)
class EligibleCourier:
    courier_id: str
    vehicle_type: VehicleType
    capacity_grams: int
    max_weight_kg: float = 0.0
    capacity_volume_cm3: int = 0
    max_packages: int = 0
    current_load_volume_cm3: int = 0
    current_load_packages: int = 0
    depot_lat: float = 0.0
    depot_lon: float = 0.0
    start_time_s: int = 0
    end_time_s: int = 0
    route_id: str | None = None
    current_stops: list[ExistingStop] = field(default_factory=list)
    current_load_grams: int = 0
    allow_fragile: bool = True
    allow_temp_sensitive: bool = True
    max_passengers: int = 0
    current_load_passengers: int = 0
    # TODO(ev-battery): the batch VRP path caps each EV vehicle's
    # max_distance_m by current_battery_pct × max_battery_range_km
    # (dispatch_vrp_helpers.py:474-485, gated by
    # features.ev_battery_awareness_enabled). The insertion heuristic does
    # not yet enforce that — it tracks travel time but not distance. Fixing
    # this requires the TravelMatrixProvider contract to return distances
    # alongside durations and a running-distance check in
    # _build_baseline_state / _validate_candidate. Until then, late
    # on-demand orders can land on an EV that ends the route over its
    # battery range.


_PRIORITY_WEIGHTS: dict[OrderPriority, float] = {
    OrderPriority.URGENT: 0.5,
    OrderPriority.HIGH: 0.75,
    OrderPriority.NORMAL: 1.0,
    OrderPriority.LOW: 1.25,
}


async def find_best_insertion(
    order: NewOrder,
    couriers: list[EligibleCourier],
    *,
    matrix_provider: TravelMatrixProvider | None = None,
) -> InsertionResult:
    if not couriers:
        return InsertionResult(
            candidate=None,
            reason="no_eligible_courier",
            rejection_summary={},
        )

    eligible, rejection_summary = _filter_eligible(order, couriers)
    if not eligible:
        logger.info(
            "Insertion heuristic: order %s rejected by all %d couriers: %s",
            order.order_id, len(couriers), rejection_summary,
        )
        return InsertionResult(
            candidate=None,
            reason="no_eligible_courier",
            rejection_summary=rejection_summary,
        )

    priority_weight = _PRIORITY_WEIGHTS.get(order.priority, 1.0)
    best_existing: InsertionCandidate | None = None

    for courier in eligible:
        if not courier.current_stops:
            continue

        travel_s, matrix_source = await _build_travel_time_matrix(
            courier,
            courier.current_stops,
            order,
            matrix_provider=matrix_provider,
        )
        candidates = _evaluate_insertions(
            order,
            courier,
            priority_weight,
            travel_s=travel_s,
        )
        for candidate in candidates:
            if best_existing is None or candidate.weighted_cost < best_existing.weighted_cost:
                best_existing = candidate
        logger.debug(
            "Insertion evaluated courier=%s source=%s candidates=%d",
            courier.courier_id,
            matrix_source,
            len(candidates),
        )

    # A new-route candidate only makes sense on a courier that isn't already
    # committed to a route — a vehicle can't run two routes at once. Couriers
    # with current_stops are evaluated above via _evaluate_insertions.
    idle_couriers = [c for c in eligible if not c.current_stops]
    best_new_route = await _create_new_route_candidate(
        order,
        idle_couriers,
        priority_weight,
        matrix_provider=matrix_provider,
    ) if idle_couriers else None

    if best_existing is None and best_new_route is None:
        return InsertionResult(candidate=None, reason="no_eligible_courier")

    if best_existing is None:
        logger.info(
            "Insertion heuristic: order %s -> new route for courier %s (duration %ds)",
            order.order_id,
            best_new_route.courier_id,
            best_new_route.detour_cost_s,
        )
        return InsertionResult(candidate=best_new_route, reason="new_route")

    if best_new_route is None or best_existing.weighted_cost <= best_new_route.weighted_cost:
        logger.info(
            "Insertion heuristic: order %s -> courier %s route %s (detour +%ds, weighted %.1f)",
            order.order_id,
            best_existing.courier_id,
            best_existing.route_id,
            best_existing.detour_cost_s,
            best_existing.weighted_cost,
        )
        return InsertionResult(candidate=best_existing, reason="inserted")

    logger.info(
        "Insertion heuristic: order %s -> new route for courier %s (duration %ds)",
        order.order_id,
        best_new_route.courier_id,
        best_new_route.detour_cost_s,
    )
    return InsertionResult(candidate=best_new_route, reason="new_route")


def _filter_eligible(
    order: NewOrder, couriers: list[EligibleCourier]
) -> tuple[list[EligibleCourier], dict[str, int]]:
    filtered: list[EligibleCourier] = []
    rejection_summary: dict[str, int] = {
        "fragile": 0,
        "temp_sensitive": 0,
        "capacity_weight": 0,
        "capacity_volume": 0,
        "capacity_packages": 0,
        "capacity_passengers": 0,
        "time_window": 0,
    }

    for courier in couriers:
        if order.is_fragile and (
            not courier.allow_fragile or courier.vehicle_type == VehicleType.MOTORCYCLE
        ):
            rejection_summary["fragile"] += 1
            continue

        if order.is_temp_sensitive and not courier.allow_temp_sensitive:
            rejection_summary["temp_sensitive"] += 1
            continue

        if courier.current_load_grams + order.weight_grams > courier.capacity_grams:
            rejection_summary["capacity_weight"] += 1
            continue

        if courier.capacity_volume_cm3 > 0 and (
            courier.current_load_volume_cm3 + order.volume_cm3 > courier.capacity_volume_cm3
        ):
            rejection_summary["capacity_volume"] += 1
            continue

        if courier.max_packages > 0 and (
            courier.current_load_packages + order.package_count > courier.max_packages
        ):
            rejection_summary["capacity_packages"] += 1
            continue

        if order.is_ride and (
            courier.max_passengers <= 0
            or courier.current_load_passengers + order.passenger_count > courier.max_passengers
        ):
            rejection_summary["capacity_passengers"] += 1
            continue

        if order.latest_s < courier.start_time_s or order.earliest_s > courier.end_time_s:
            rejection_summary["time_window"] += 1
            continue

        filtered.append(courier)

    # Only return keys that actually had rejections
    rejection_summary = {k: v for k, v in rejection_summary.items() if v > 0}
    return filtered, rejection_summary


def _evaluate_insertions(
    order: NewOrder,
    courier: EligibleCourier,
    priority_weight: float,
    *,
    travel_s: list[list[int]],
) -> list[InsertionCandidate]:
    stops = courier.current_stops
    n = len(stops)
    if n == 0:
        return []

    baseline = _build_baseline_state(courier, stops, travel_s)
    if baseline is None:
        return []

    base_departures, base_loads, base_volumes, base_packages, base_passengers = baseline
    pickup_node = n + 1
    delivery_node = n + 2

    candidates: list[InsertionCandidate] = []

    for pickup_pos in range(n + 1):
        for delivery_pos in range(pickup_pos + 1, n + 2):
            detour = _compute_detour_seconds(
                travel_s=travel_s,
                n=n,
                pickup_pos=pickup_pos,
                delivery_pos=delivery_pos,
                pickup_node=pickup_node,
                delivery_node=delivery_node,
            )
            weighted = detour * priority_weight

            if not _validate_candidate(
                courier=courier,
                stops=stops,
                order=order,
                travel_s=travel_s,
                base_departures=base_departures,
                base_loads=base_loads,
                base_volumes=base_volumes,
                base_packages=base_packages,
                base_passengers=base_passengers,
                pickup_pos=pickup_pos,
                delivery_pos=delivery_pos,
                pickup_node=pickup_node,
                delivery_node=delivery_node,
            ):
                continue

            candidates.append(
                InsertionCandidate(
                    courier_id=courier.courier_id,
                    route_id=courier.route_id,
                    pickup_position=pickup_pos,
                    delivery_position=delivery_pos,
                    detour_cost_s=detour,
                    weighted_cost=weighted,
                )
            )

    return candidates


def _is_matrix_shape_valid(matrix: list[list[int]], expected_size: int) -> bool:
    return len(matrix) == expected_size and all(
        len(row) == expected_size for row in matrix
    )


async def _build_travel_time_matrix(
    courier: EligibleCourier,
    stops: list[ExistingStop],
    order: NewOrder,
    *,
    matrix_provider: TravelMatrixProvider | None = None,
) -> tuple[list[list[int]], str]:
    coords = [(courier.depot_lat, courier.depot_lon)]
    coords.extend((stop.lat, stop.lon) for stop in stops)
    coords.append((order.pickup_lat, order.pickup_lon))
    coords.append((order.delivery_lat, order.delivery_lon))

    if matrix_provider is None:
        raise RuntimeError(
            "matrix_provider is required — OSRM-backed provider must be supplied"
        )

    matrix, source = await matrix_provider(coords)
    if not _is_matrix_shape_valid(matrix, len(coords)):
        raise RuntimeError(
            f"matrix_provider returned invalid shape "
            f"(expected={len(coords)}, got rows={len(matrix)})"
        )
    return matrix, source


def _build_baseline_state(
    courier: EligibleCourier,
    stops: list[ExistingStop],
    travel_s: list[list[int]],
) -> tuple[list[int], list[int], list[int], list[int], list[int]] | None:
    departures: list[int] = []
    loads: list[int] = []
    volumes: list[int] = []
    packages: list[int] = []
    passengers: list[int] = []

    current_time = courier.start_time_s
    current_load = courier.current_load_grams
    current_volume = courier.current_load_volume_cm3
    current_packages = courier.current_load_packages
    current_passengers = courier.current_load_passengers
    prev_node = 0

    for stop_index, stop in enumerate(stops):
        node = stop_index + 1
        arrival = current_time + travel_s[prev_node][node]
        if arrival < stop.earliest_s:
            arrival = stop.earliest_s
        if arrival > stop.latest_s:
            return None

        departure = arrival + stop.service_s
        if departure > courier.end_time_s:
            return None

        current_load += stop.weight_delta_g
        if current_load < 0 or current_load > courier.capacity_grams:
            return None

        current_volume += stop.volume_delta_cm3
        if courier.capacity_volume_cm3 > 0 and (
            current_volume < 0 or current_volume > courier.capacity_volume_cm3
        ):
            return None

        current_packages += stop.package_delta
        if courier.max_packages > 0 and (
            current_packages < 0 or current_packages > courier.max_packages
        ):
            return None

        current_passengers += stop.passenger_delta
        if courier.max_passengers > 0 and (
            current_passengers < 0 or current_passengers > courier.max_passengers
        ):
            return None

        departures.append(departure)
        loads.append(current_load)
        volumes.append(current_volume)
        packages.append(current_packages)
        passengers.append(current_passengers)
        current_time = departure
        prev_node = node

    return departures, loads, volumes, packages, passengers


def _compute_detour_seconds(
    *,
    travel_s: list[list[int]],
    n: int,
    pickup_pos: int,
    delivery_pos: int,
    pickup_node: int,
    delivery_node: int,
) -> int:
    prev_pickup_node = 0 if pickup_pos == 0 else pickup_pos
    next_pickup_node = (pickup_pos + 1) if pickup_pos < n else None

    removed_pickup_edge = (
        travel_s[prev_pickup_node][next_pickup_node]
        if next_pickup_node is not None
        else 0
    )
    inserted_pickup_edges = (
        travel_s[prev_pickup_node][pickup_node]
        + (travel_s[pickup_node][next_pickup_node] if next_pickup_node is not None else 0)
    )

    prev_delivery_node = _node_at_intermediate_position(
        delivery_pos - 1,
        pickup_pos=pickup_pos,
        n=n,
        pickup_node=pickup_node,
    )
    next_delivery_node = (
        _node_at_intermediate_position(
            delivery_pos,
            pickup_pos=pickup_pos,
            n=n,
            pickup_node=pickup_node,
        )
        if delivery_pos < n + 1
        else None
    )

    removed_delivery_edge = (
        travel_s[prev_delivery_node][next_delivery_node]
        if next_delivery_node is not None
        else 0
    )
    inserted_delivery_edges = (
        travel_s[prev_delivery_node][delivery_node]
        + (travel_s[delivery_node][next_delivery_node] if next_delivery_node is not None else 0)
    )

    detour = (inserted_pickup_edges - removed_pickup_edge) + (
        inserted_delivery_edges - removed_delivery_edge
    )
    return detour if detour > 0 else 0


def _validate_candidate(
    *,
    courier: EligibleCourier,
    stops: list[ExistingStop],
    order: NewOrder,
    travel_s: list[list[int]],
    base_departures: list[int],
    base_loads: list[int],
    base_volumes: list[int],
    base_packages: list[int],
    base_passengers: list[int],
    pickup_pos: int,
    delivery_pos: int,
    pickup_node: int,
    delivery_node: int,
) -> bool:
    n = len(stops)
    order_passenger_count = order.passenger_count if order.is_ride else 0

    if pickup_pos == 0:
        current_time = courier.start_time_s
        current_load = courier.current_load_grams
        current_volume = courier.current_load_volume_cm3
        current_packages = courier.current_load_packages
        current_passengers = courier.current_load_passengers
        prev_node = 0
    else:
        current_time = base_departures[pickup_pos - 1]
        current_load = base_loads[pickup_pos - 1]
        current_volume = base_volumes[pickup_pos - 1]
        current_packages = base_packages[pickup_pos - 1]
        current_passengers = base_passengers[pickup_pos - 1]
        prev_node = pickup_pos

    for final_pos in range(pickup_pos, n + 2):
        node = _node_at_final_position(
            final_pos,
            pickup_pos=pickup_pos,
            delivery_pos=delivery_pos,
            n=n,
            pickup_node=pickup_node,
            delivery_node=delivery_node,
        )

        if node == pickup_node:
            earliest = order.pickup_earliest_s
            latest = order.pickup_latest_s
            service = order.service_time_s
            weight_delta = order.weight_grams
            volume_delta = order.volume_cm3
            package_delta = order.package_count
            passenger_delta = order_passenger_count
        elif node == delivery_node:
            earliest = order.earliest_s
            latest = order.latest_s
            service = order.service_time_s
            weight_delta = -order.weight_grams
            volume_delta = -order.volume_cm3
            package_delta = -order.package_count
            passenger_delta = -order_passenger_count
        else:
            stop = stops[node - 1]
            earliest = stop.earliest_s
            latest = stop.latest_s
            service = stop.service_s
            weight_delta = stop.weight_delta_g
            volume_delta = stop.volume_delta_cm3
            package_delta = stop.package_delta
            passenger_delta = stop.passenger_delta

        arrival = current_time + travel_s[prev_node][node]
        if arrival < earliest:
            arrival = earliest
        if arrival > latest:
            return False

        departure = arrival + service
        if departure > courier.end_time_s:
            return False

        current_load += weight_delta
        if current_load < 0 or current_load > courier.capacity_grams:
            return False

        current_volume += volume_delta
        if courier.capacity_volume_cm3 > 0 and (
            current_volume < 0 or current_volume > courier.capacity_volume_cm3
        ):
            return False

        current_packages += package_delta
        if courier.max_packages > 0 and (
            current_packages < 0 or current_packages > courier.max_packages
        ):
            return False

        current_passengers += passenger_delta
        if courier.max_passengers > 0 and (
            current_passengers < 0 or current_passengers > courier.max_passengers
        ):
            return False

        current_time = departure
        prev_node = node

    return True


def _node_at_intermediate_position(
    pos: int,
    *,
    pickup_pos: int,
    n: int,
    pickup_node: int,
) -> int:
    if pos < pickup_pos:
        return pos + 1
    if pos == pickup_pos:
        return pickup_node
    if pos <= n:
        return pos
    raise IndexError("intermediate position out of range")


def _node_at_final_position(
    pos: int,
    *,
    pickup_pos: int,
    delivery_pos: int,
    n: int,
    pickup_node: int,
    delivery_node: int,
) -> int:
    if pos < pickup_pos:
        return pos + 1
    if pos == pickup_pos:
        return pickup_node
    if pos < delivery_pos:
        return pos
    if pos == delivery_pos:
        return delivery_node
    if pos <= n + 1:
        return pos - 1
    raise IndexError("final position out of range")


async def _create_new_route_candidate(
    order: NewOrder,
    couriers: list[EligibleCourier],
    priority_weight: float,
    *,
    matrix_provider: TravelMatrixProvider | None = None,
) -> InsertionCandidate | None:
    """O(C) with one batched matrix call instead of O(C) separate calls."""
    best: InsertionCandidate | None = None
    best_weighted_cost = float("inf")

    # Batch: coords = [depot_0, depot_1, ..., depot_C, pickup, delivery]
    # One matrix call for all couriers instead of C calls.
    pickup_idx = len(couriers)
    delivery_idx = len(couriers) + 1
    coords = [(c.depot_lat, c.depot_lon) for c in couriers]
    coords.append((order.pickup_lat, order.pickup_lon))
    coords.append((order.delivery_lat, order.delivery_lon))

    if matrix_provider is None:
        raise RuntimeError(
            "matrix_provider is required — OSRM-backed provider must be supplied"
        )

    matrix, _ = await matrix_provider(coords)
    if not _is_matrix_shape_valid(matrix, len(coords)):
        raise RuntimeError(
            f"matrix_provider returned invalid shape "
            f"(expected={len(coords)}, got rows={len(matrix)})"
        )

    for i, courier in enumerate(couriers):
        to_pickup = matrix[i][pickup_idx]
        pickup_to_delivery = matrix[pickup_idx][delivery_idx]
        delivery_to_depot = matrix[delivery_idx][i]

        arrival_pickup = courier.start_time_s + to_pickup
        if arrival_pickup < order.pickup_earliest_s:
            arrival_pickup = order.pickup_earliest_s
        if arrival_pickup > order.pickup_latest_s:
            continue

        departure_pickup = arrival_pickup + order.service_time_s
        arrival_delivery = departure_pickup + pickup_to_delivery
        if arrival_delivery < order.earliest_s:
            arrival_delivery = order.earliest_s
        if arrival_delivery > order.latest_s:
            continue

        departure_delivery = arrival_delivery + order.service_time_s
        if departure_delivery + delivery_to_depot > courier.end_time_s:
            continue

        total_time = to_pickup + order.service_time_s + pickup_to_delivery + order.service_time_s + delivery_to_depot
        weighted = total_time * priority_weight
        if weighted >= best_weighted_cost:
            continue

        best_weighted_cost = weighted
        best = InsertionCandidate(
            courier_id=courier.courier_id,
            route_id=None,
            pickup_position=0,
            delivery_position=1,
            detour_cost_s=total_time,
            weighted_cost=weighted,
        )

    return best
