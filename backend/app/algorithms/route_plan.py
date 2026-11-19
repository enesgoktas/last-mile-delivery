"""In-memory route plans and shared simulation for flexible depot reloads."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.algorithms.solution_validator import ValidationResult, Violation, ViolationType
from app.algorithms.vrp_solver import (
    EconomicCostBreakdown,
    OrderData,
    PlannedRoute,
    PlannedStop,
    VehicleData,
    VRPInput,
    _distance_matrix_for_vehicle,
    _duration_matrix_for_vehicle,
    build_route_cost_breakdown,
)

StopKind = Literal["PICKUP", "DELIVERY", "DEPOT_RETURN", "DEPOT_RELOAD"]


@dataclass(frozen=True, slots=True)
class RoutePlanStopSpec:
    order_id: str | None
    stop_type: StopKind
    location_index: int


@dataclass(frozen=True, slots=True)
class RoutePlanStop:
    order_id: str | None
    stop_type: StopKind
    location_index: int
    arrival_time_s: int
    departure_time_s: int
    load_delta_g: int
    volume_delta_cm3: int
    package_delta: int


@dataclass(slots=True)
class RoutePlan:
    courier_id: str
    vehicle: VehicleData
    stops: list[RoutePlanStop] = field(default_factory=list)
    stop_specs: list[RoutePlanStopSpec] = field(default_factory=list)
    total_distance_m: int = 0
    total_duration_s: int = 0
    route_start_time_s: int = 0
    route_end_time_s: int = 0
    cost_breakdown: EconomicCostBreakdown | None = None
    violations: list[Violation] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return all(v.severity != "error" for v in self.violations)

    def to_planned_route(self, locations: list[tuple[float, float]]) -> PlannedRoute:
        return route_plan_to_planned_route(self, locations)


def build_order_stop_specs(order: OrderData) -> list[RoutePlanStopSpec]:
    return [
        RoutePlanStopSpec(order.order_id, "PICKUP", order.pickup_index),
        RoutePlanStopSpec(order.order_id, "DELIVERY", order.delivery_index),
    ]


def build_depot_reload_specs(
    vehicle: VehicleData,
    location_index: int | None = None,
) -> list[RoutePlanStopSpec]:
    depot_index = vehicle.depot_index if location_index is None else location_index
    return [
        RoutePlanStopSpec(None, "DEPOT_RETURN", depot_index),
        RoutePlanStopSpec(None, "DEPOT_RELOAD", depot_index),
    ]


def simulate_route_plan(
    vrp_input: VRPInput,
    vehicle: VehicleData,
    stop_specs: list[RoutePlanStopSpec],
    *,
    reload_time_s: int = 900,
    orders_by_id: dict[str, OrderData] | None = None,
) -> RoutePlan:
    """Forward-simulate a single physical courier route.

    The simulator is intentionally stricter than a cost-only route rebuild:
    every candidate insertion, OR-Tools normalization, repair, and final
    persistence conversion uses the same feasibility accounting.
    """
    orders_by_id = orders_by_id or {order.order_id: order for order in vrp_input.orders}
    distance_matrix = _distance_matrix_for_vehicle(vrp_input, vehicle)
    duration_matrix = _duration_matrix_for_vehicle(vrp_input, vehicle)

    current_loc = vehicle.depot_index
    current_time = max(vehicle.start_time_s, 0)
    route_start_time = current_time
    running_load = 0
    running_volume = 0
    running_packages = 0
    segment_stop_count = 0
    total_distance = 0
    stops: list[RoutePlanStop] = []
    violations: list[Violation] = []
    picked: set[str] = set()
    delivered: set[str] = set()
    previous_stop_type: StopKind | None = None
    route_work_started = False
    initial_depot_load_charged = False

    for spec in stop_specs:
        travel_s = _matrix_value(duration_matrix, current_loc, spec.location_index)
        travel_m = _matrix_value(distance_matrix, current_loc, spec.location_index)
        current_time += travel_s
        total_distance += travel_m
        arrival = current_time
        service_s = 0
        load_delta = 0
        volume_delta = 0
        package_delta = 0

        if spec.stop_type == "DEPOT_RETURN":
            running_load = running_volume = running_packages = 0
            segment_stop_count = 0
        elif spec.stop_type == "DEPOT_RELOAD":
            running_load = running_volume = running_packages = 0
            segment_stop_count = 0
            service_s = service_time_for_spec(
                spec,
                None,
                vehicle,
                reload_time_s=reload_time_s,
            )
        else:
            order = orders_by_id.get(spec.order_id or "")
            if order is None:
                violations.append(_violation(vehicle.courier_id, spec.order_id, "Unknown order in route"))
            else:
                service_s = service_time_for_spec(
                    spec,
                    order,
                    vehicle,
                    reload_time_s=reload_time_s,
                )
                if (
                    spec.stop_type == "PICKUP"
                    and is_depot_pickup_spec(spec, order, vehicle)
                    and not route_work_started
                    and not initial_depot_load_charged
                ):
                    service_s = reload_time_s
                    initial_depot_load_charged = True
                segment_stop_count += visible_stop_count_for_spec(spec, order, vehicle)
                if spec.stop_type == "PICKUP":
                    depot_pickup = is_depot_pickup_spec(spec, order, vehicle)
                    if (
                        depot_pickup
                        and route_work_started
                        and previous_stop_type != "DEPOT_RELOAD"
                    ):
                        violations.append(
                            _violation(
                                vehicle.courier_id,
                                order.order_id,
                                "Depot pickup after route work requires an explicit depot reload",
                                ViolationType.MISSING_PICKUP_OR_DELIVERY,
                            )
                        )
                    pickup_earliest = order.pickup_earliest_time_s or 0
                    pickup_latest = order.pickup_latest_time_s
                    if not depot_pickup and current_time < pickup_earliest:
                        current_time = pickup_earliest
                        arrival = current_time
                    if (
                        pickup_latest is not None
                        and not depot_pickup
                        and arrival > pickup_latest
                    ):
                        violations.append(
                            _violation(
                                vehicle.courier_id,
                                order.order_id,
                                f"Pickup window violated for order {order.order_id[:8]}",
                                ViolationType.TIME_WINDOW_VIOLATED,
                            )
                        )
                    if order.order_id in picked:
                        violations.append(_violation(vehicle.courier_id, order.order_id, "Duplicate pickup"))
                    picked.add(order.order_id)
                    load_delta = order.weight_grams
                    volume_delta = order.volume_cm3
                    package_delta = order.package_count
                elif spec.stop_type == "DELIVERY":
                    route_work_started = True
                    if order.order_id not in picked:
                        violations.append(
                            _violation(
                                vehicle.courier_id,
                                order.order_id,
                                "Delivery occurs before pickup",
                                ViolationType.PICKUP_AFTER_DELIVERY,
                            )
                        )
                    if current_time < order.earliest_time_s:
                        current_time = order.earliest_time_s
                        arrival = current_time
                    hard_latest = order.hard_latest_time_s or order.latest_time_s
                    if hard_latest and arrival > hard_latest:
                        violations.append(
                            _violation(
                                vehicle.courier_id,
                                order.order_id,
                                f"Delivery hard window violated for order {order.order_id[:8]}",
                                ViolationType.TIME_WINDOW_VIOLATED,
                            )
                        )
                    delivered.add(order.order_id)
                    load_delta = -order.weight_grams
                    volume_delta = -order.volume_cm3
                    package_delta = -order.package_count

                running_load += load_delta
                running_volume += volume_delta
                running_packages += package_delta
                violations.extend(
                    _capacity_violations(
                        vehicle,
                        running_load=running_load,
                        running_volume=running_volume,
                        running_packages=running_packages,
                    )
                )
                if vehicle.max_stops > 0 and segment_stop_count > vehicle.max_stops:
                    violations.append(
                        _violation(
                            vehicle.courier_id,
                            None,
                            f"Route segment has {segment_stop_count} stops, exceeds max {vehicle.max_stops}",
                            ViolationType.MAX_STOPS_EXCEEDED,
                        )
                    )

        departure = current_time + service_s
        stops.append(
            RoutePlanStop(
                order_id=spec.order_id,
                stop_type=spec.stop_type,
                location_index=spec.location_index,
                arrival_time_s=arrival,
                departure_time_s=departure,
                load_delta_g=load_delta,
                volume_delta_cm3=volume_delta,
                package_delta=package_delta,
            )
        )
        current_time = departure
        current_loc = spec.location_index
        previous_stop_type = spec.stop_type

    if stop_specs:
        # Phase 5 multi-depot — the return leg targets `end_depot_index`
        # when set, otherwise the home `depot_index` (single-depot path).
        return_depot = (
            vehicle.end_depot_index
            if vehicle.end_depot_index is not None
            else vehicle.depot_index
        )
        total_distance += _matrix_value(distance_matrix, current_loc, return_depot)
        current_time += _matrix_value(duration_matrix, current_loc, return_depot)

    for order_id in picked | delivered:
        if order_id not in picked or order_id not in delivered:
            violations.append(
                _violation(
                    vehicle.courier_id,
                    order_id,
                    "Order is missing pickup or delivery",
                    ViolationType.MISSING_PICKUP_OR_DELIVERY,
                )
            )

    hard_shift_end = vehicle.hard_end_time_s or vehicle.end_time_s
    if hard_shift_end and current_time > hard_shift_end:
        violations.append(
            _violation(
                vehicle.courier_id,
                None,
                "Route exceeds hard shift end including return to depot",
                ViolationType.SHIFT_EXCEEDED,
            )
        )
    if vehicle.max_distance_m > 0 and total_distance > vehicle.max_distance_m:
        violations.append(
            _violation(
                vehicle.courier_id,
                None,
                f"Route distance {total_distance}m exceeds max {vehicle.max_distance_m}m",
                ViolationType.MAX_DISTANCE_EXCEEDED,
            )
        )

    planned_stops = [
        PlannedStop(
            order_id=stop.order_id or f"__{stop.stop_type}__",
            stop_type=stop.stop_type,
            location_index=stop.location_index,
            lat=vrp_input.locations[stop.location_index][0],
            lon=vrp_input.locations[stop.location_index][1],
            arrival_time_s=stop.arrival_time_s,
            departure_time_s=stop.departure_time_s,
        )
        for stop in stops
    ]
    cost_breakdown = None
    if stops:
        cost_breakdown = build_route_cost_breakdown(
            vehicle=vehicle,
            route_distance_m=total_distance,
            route_duration_s=max(current_time - route_start_time, 0),
            route_end_time_s=current_time,
            stops=planned_stops,
            orders_by_id=orders_by_id,
        )

    return RoutePlan(
        courier_id=vehicle.courier_id,
        vehicle=vehicle,
        stops=stops,
        stop_specs=list(stop_specs),
        total_distance_m=total_distance,
        total_duration_s=max(current_time - route_start_time, 0),
        route_start_time_s=route_start_time,
        route_end_time_s=current_time,
        cost_breakdown=cost_breakdown,
        violations=violations,
    )


def is_depot_pickup_spec(
    spec: RoutePlanStopSpec,
    order: OrderData | None,
    vehicle: VehicleData,
) -> bool:
    """True when the spec's location is one of the vehicle's allowed
    depots. Multi-depot couriers (Phase 1+) must treat pickups at any
    allowed depot as depot pickups, not just the home `depot_index`."""
    if spec.stop_type != "PICKUP" or order is None:
        return False
    if order.pickup_is_depot:
        return True
    allowed = vehicle.allowed_depot_indices or (vehicle.depot_index,)
    return spec.location_index in allowed


def visible_stop_count_for_spec(
    spec: RoutePlanStopSpec,
    order: OrderData | None,
    vehicle: VehicleData,
) -> int:
    if spec.stop_type in ("DEPOT_RETURN", "DEPOT_RELOAD"):
        return 0
    if is_depot_pickup_spec(spec, order, vehicle):
        return 0
    return 1


def service_time_for_spec(
    spec: RoutePlanStopSpec,
    order: OrderData | None,
    vehicle: VehicleData,
    *,
    reload_time_s: int,
) -> int:
    if spec.stop_type == "DEPOT_RELOAD":
        return reload_time_s
    if spec.stop_type == "DEPOT_RETURN" or order is None:
        return 0
    if is_depot_pickup_spec(spec, order, vehicle):
        return 0
    return order.service_time_s


def route_plan_to_planned_route(
    plan: RoutePlan,
    locations: list[tuple[float, float]],
) -> PlannedRoute:
    planned_stops = []
    for stop in plan.stops:
        lat, lon = locations[stop.location_index]
        planned_stops.append(
            PlannedStop(
                order_id=stop.order_id or f"__{stop.stop_type}__",
                stop_type=stop.stop_type,
                location_index=stop.location_index,
                lat=lat,
                lon=lon,
                arrival_time_s=stop.arrival_time_s,
                departure_time_s=stop.departure_time_s,
            )
        )
    return PlannedRoute(
        courier_id=plan.courier_id,
        stops=planned_stops,
        total_distance_m=plan.total_distance_m,
        total_duration_s=plan.total_duration_s,
        route_start_time_s=plan.route_start_time_s,
        route_end_time_s=plan.route_end_time_s,
        cost_breakdown=plan.cost_breakdown,
    )


def validation_from_route_plans(plans: list[RoutePlan]) -> ValidationResult:
    violations = [violation for plan in plans for violation in plan.violations]
    routes_with_violations = {
        violation.route_courier_id for violation in violations
    }
    return ValidationResult(
        is_valid=all(v.severity != "error" for v in violations),
        violations=violations,
        routes_checked=len(plans),
        routes_with_violations=len(routes_with_violations),
    )


def _matrix_value(matrix: list[list[int]], row: int, col: int) -> int:
    try:
        return matrix[row][col]
    except IndexError:
        return 10**9


def _capacity_violations(
    vehicle: VehicleData,
    *,
    running_load: int,
    running_volume: int,
    running_packages: int,
) -> list[Violation]:
    violations: list[Violation] = []
    if vehicle.capacity_grams > 0 and running_load > vehicle.capacity_grams:
        violations.append(_violation(vehicle.courier_id, None, "Weight capacity exceeded", ViolationType.CAPACITY_EXCEEDED))
    if vehicle.capacity_volume_cm3 > 0 and running_volume > vehicle.capacity_volume_cm3:
        violations.append(_violation(vehicle.courier_id, None, "Volume capacity exceeded", ViolationType.CAPACITY_EXCEEDED))
    if vehicle.max_packages > 0 and running_packages > vehicle.max_packages:
        violations.append(_violation(vehicle.courier_id, None, "Package capacity exceeded", ViolationType.MAX_PACKAGES_EXCEEDED))
    if running_load < 0 or running_volume < 0 or running_packages < 0:
        violations.append(_violation(vehicle.courier_id, None, "Route load went negative", ViolationType.PICKUP_AFTER_DELIVERY))
    return violations


def _violation(
    courier_id: str,
    order_id: str | None,
    message: str,
    violation_type: ViolationType = ViolationType.CAPACITY_EXCEEDED,
) -> Violation:
    return Violation(
        violation_type=violation_type,
        route_courier_id=courier_id,
        order_id=order_id,
        message=message,
    )
