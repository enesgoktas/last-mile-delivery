"""
Solution Validator — validates VRP solutions before persistence.

Checks planned routes against all constraints to catch infeasible
solutions that the solver may produce under timeout or numerical issues.

Validation is advisory: violations are reported but don't block persistence
by default. The dispatch service decides whether to reject or flag.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.algorithms.utils import fmt_time as _fmt_time
from app.algorithms.vrp_solver import PlannedRoute, VRPSolution
from app.utils.compat import StrEnum

logger = logging.getLogger(__name__)


class ViolationType(StrEnum):
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    TIME_WINDOW_VIOLATED = "TIME_WINDOW_VIOLATED"
    SHIFT_EXCEEDED = "SHIFT_EXCEEDED"
    PICKUP_AFTER_DELIVERY = "PICKUP_AFTER_DELIVERY"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    MAX_STOPS_EXCEEDED = "MAX_STOPS_EXCEEDED"
    MAX_PACKAGES_EXCEEDED = "MAX_PACKAGES_EXCEEDED"
    MAX_DISTANCE_EXCEEDED = "MAX_DISTANCE_EXCEEDED"
    MISSING_PICKUP_OR_DELIVERY = "MISSING_PICKUP_OR_DELIVERY"


@dataclass(frozen=True, slots=True)
class Violation:
    """A single constraint violation found in a route."""
    violation_type: ViolationType
    route_courier_id: str
    order_id: str | None
    message: str
    severity: str = "error"  # "error" or "warning"


@dataclass
class ValidationResult:
    """Complete validation output for a VRP solution."""
    is_valid: bool
    violations: list[Violation] = field(default_factory=list)
    routes_checked: int = 0
    routes_with_violations: int = 0

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")

    def summary(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "routes_checked": self.routes_checked,
            "routes_with_violations": self.routes_with_violations,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "violations": [
                {
                    "type": v.violation_type.value,
                    "courier_id": v.route_courier_id,
                    "order_id": v.order_id,
                    "message": v.message,
                    "severity": v.severity,
                }
                for v in self.violations
            ],
        }


@dataclass(frozen=True, slots=True)
class RouteConstraints:
    """Constraints to validate a single route against.

    Multi-depot (Phase 3): `allowed_depot_indices` is the canonical
    field; the legacy `depot_index` keyword still works for callers that
    pass exactly one depot and is exposed via the `home_depot_index`
    property. Empty tuple is the sentinel for "no depot restriction"
    (validator skips depot-based checks entirely).
    """
    capacity_grams: int = 0
    capacity_volume_cm3: int = 0
    max_packages: int = 0
    start_time_s: int = 0
    end_time_s: int = 86400
    max_stops: int = 0
    max_distance_m: int = 0
    # `allowed_depot_indices` is the canonical multi-depot field. The
    # legacy `depot_index` kwarg is kept for back-compat — when only
    # `depot_index` is supplied, `home_depot_index` returns it; when
    # `allowed_depot_indices` is supplied, both work.
    allowed_depot_indices: tuple[int, ...] = ()
    end_depot_index: int | None = None
    depot_index: int | None = None

    @property
    def home_depot_index(self) -> int | None:
        """The vehicle's start/home depot, regardless of which field
        the caller used."""
        if self.allowed_depot_indices:
            return self.allowed_depot_indices[0]
        return self.depot_index

    @property
    def depot_indices(self) -> tuple[int, ...]:
        """Every depot location-index this route may visit."""
        if self.allowed_depot_indices:
            return self.allowed_depot_indices
        if self.depot_index is not None:
            return (self.depot_index,)
        return ()


def validate_solution(
    solution: VRPSolution,
    *,
    order_weights: dict[str, int] | None = None,
    order_volumes: dict[str, int] | None = None,
    order_package_counts: dict[str, int] | None = None,
    route_constraints: dict[str, RouteConstraints] | None = None,
    order_time_windows: dict[str, tuple[int, int]] | None = None,
    order_pickup_windows: dict[str, tuple[int, int]] | None = None,
    depot_pickup_order_ids: set[str] | None = None,
    lateness_grace_by_order_id: dict[str, int] | None = None,
) -> ValidationResult:
    """Validate a complete VRP solution.

    Args:
        solution: The VRP solution to validate.
        order_weights: Map of order_id -> weight in grams.
        order_volumes: Map of order_id -> volume in cm3.
        order_package_counts: Map of order_id -> package count.
        route_constraints: Map of courier_id -> constraints for that route.
        order_time_windows: Map of order_id -> (earliest_delivery_s, latest_delivery_s).
        order_pickup_windows: Map of order_id -> (earliest_pickup_s, latest_pickup_s).

    Returns:
        ValidationResult with all violations found.
    """
    order_weights = order_weights or {}
    order_volumes = order_volumes or {}
    order_package_counts = order_package_counts or {}
    route_constraints = route_constraints or {}
    order_time_windows = order_time_windows or {}
    order_pickup_windows = order_pickup_windows or {}
    depot_pickup_order_ids = depot_pickup_order_ids or set()
    lateness_grace_by_order_id = lateness_grace_by_order_id or {}

    all_violations: list[Violation] = []
    couriers_with_issues: set[str] = set()

    for route in solution.routes:
        route_violations = _validate_route(
            route,
            order_weights=order_weights,
            order_volumes=order_volumes,
            order_package_counts=order_package_counts,
            constraints=route_constraints.get(route.courier_id),
            order_time_windows=order_time_windows,
            order_pickup_windows=order_pickup_windows,
            depot_pickup_order_ids=depot_pickup_order_ids,
            lateness_grace_by_order_id=lateness_grace_by_order_id,
        )
        all_violations.extend(route_violations)
        if route_violations:
            couriers_with_issues.add(route.courier_id)

    # Check for orders assigned to multiple routes
    order_route_assignments: dict[str, list[str]] = {}
    for route in solution.routes:
        for stop in route.stops:
            if stop.stop_type in ("DEPOT_RETURN", "DEPOT_RELOAD"):
                continue
            if stop.order_id not in order_route_assignments:
                order_route_assignments[stop.order_id] = []
            if route.courier_id not in order_route_assignments[stop.order_id]:
                order_route_assignments[stop.order_id].append(route.courier_id)

    for order_id, courier_ids in order_route_assignments.items():
        if len(courier_ids) > 1:
            all_violations.append(Violation(
                violation_type=ViolationType.DUPLICATE_ORDER,
                route_courier_id=courier_ids[0],
                order_id=order_id,
                message=(
                    f"Order {order_id[:8]} assigned to multiple couriers: "
                    f"{', '.join(cid[:8] for cid in courier_ids)}"
                ),
            ))
            couriers_with_issues.update(courier_ids)

    is_valid = all(v.severity != "error" for v in all_violations)

    result = ValidationResult(
        is_valid=is_valid,
        violations=all_violations,
        routes_checked=len(solution.routes),
        routes_with_violations=len(couriers_with_issues),
    )

    if all_violations:
        logger.warning(
            "Solution validation: %d violations (%d errors, %d warnings) "
            "across %d/%d routes",
            len(all_violations),
            result.error_count,
            result.warning_count,
            len(couriers_with_issues),
            len(solution.routes),
        )
    else:
        logger.info(
            "Solution validation passed: %d routes, 0 violations",
            len(solution.routes),
        )

    return result


def _validate_route(
    route: PlannedRoute,
    *,
    order_weights: dict[str, int],
    order_volumes: dict[str, int],
    order_package_counts: dict[str, int],
    constraints: RouteConstraints | None,
    order_time_windows: dict[str, tuple[int, int]],
    order_pickup_windows: dict[str, tuple[int, int]],
    depot_pickup_order_ids: set[str],
    lateness_grace_by_order_id: dict[str, int] | None = None,
) -> list[Violation]:
    """Validate a single route for all constraint types."""
    violations: list[Violation] = []

    order_pickup_idx: dict[str, int] = {}
    order_delivery_idx: dict[str, int] = {}
    for i, stop in enumerate(route.stops):
        if stop.stop_type == "PICKUP":
            order_pickup_idx[stop.order_id] = i
        elif stop.stop_type == "DELIVERY":
            order_delivery_idx[stop.order_id] = i

    violations.extend(_check_pickup_before_delivery(route.courier_id, order_pickup_idx, order_delivery_idx))
    violations.extend(_check_missing_pickup_or_delivery(route.courier_id, order_pickup_idx, order_delivery_idx))
    violations.extend(_check_capacity_limits(route, order_weights, order_volumes, order_package_counts, constraints))
    violations.extend(_check_time_windows(
        route,
        order_pickup_windows,
        order_time_windows,
        depot_pickup_order_ids,
        lateness_grace_by_order_id=lateness_grace_by_order_id or {},
    ))
    violations.extend(_check_shift_hours(route, constraints))
    violations.extend(_check_max_stops(route, constraints, depot_pickup_order_ids))
    violations.extend(_check_max_distance(route, constraints))

    return violations


def _check_pickup_before_delivery(courier_id: str, order_pickup_idx: dict[str, int], order_delivery_idx: dict[str, int]) -> list[Violation]:
    violations = []
    for order_id, pickup_i in order_pickup_idx.items():
        delivery_i = order_delivery_idx.get(order_id)
        if delivery_i is not None and pickup_i >= delivery_i:
            violations.append(Violation(
                violation_type=ViolationType.PICKUP_AFTER_DELIVERY,
                route_courier_id=courier_id,
                order_id=order_id,
                message=(
                    f"Pickup at position {pickup_i} comes after delivery "
                    f"at position {delivery_i} for order {order_id[:8]}"
                ),
            ))
    return violations


def _check_missing_pickup_or_delivery(courier_id: str, order_pickup_idx: dict[str, int], order_delivery_idx: dict[str, int]) -> list[Violation]:
    violations = []
    all_order_ids = set(order_pickup_idx.keys()) | set(order_delivery_idx.keys())
    for order_id in all_order_ids:
        has_pickup = order_id in order_pickup_idx
        has_delivery = order_id in order_delivery_idx
        if has_pickup and not has_delivery:
            violations.append(Violation(
                violation_type=ViolationType.MISSING_PICKUP_OR_DELIVERY,
                route_courier_id=courier_id,
                order_id=order_id,
                message=f"Order {order_id[:8]} has pickup but no delivery",
            ))
        elif has_delivery and not has_pickup:
            violations.append(Violation(
                violation_type=ViolationType.MISSING_PICKUP_OR_DELIVERY,
                route_courier_id=courier_id,
                order_id=order_id,
                message=f"Order {order_id[:8]} has delivery but no pickup",
            ))
    return violations


def _check_capacity_limits(
    route: PlannedRoute,
    order_weights: dict[str, int],
    order_volumes: dict[str, int],
    order_package_counts: dict[str, int],
    constraints: RouteConstraints | None
) -> list[Violation]:
    violations = []
    if not constraints:
        return violations

    running_load = 0
    peak_load = 0
    running_vol = 0
    peak_vol = 0
    running_pkgs = 0
    peak_pkgs = 0

    for stop in route.stops:
        if stop.stop_type in ("DEPOT_RETURN", "DEPOT_RELOAD"):
            running_load = running_vol = running_pkgs = 0
            continue

        weight = order_weights.get(stop.order_id, 0) if order_weights else 0
        vol = order_volumes.get(stop.order_id, 0) if order_volumes else 0
        pkgs = order_package_counts.get(stop.order_id, 0) if order_package_counts else 0

        if stop.stop_type == "PICKUP":
            running_load += weight
            running_vol += vol
            running_pkgs += pkgs
        elif stop.stop_type == "DELIVERY":
            running_load -= weight
            running_vol -= vol
            running_pkgs -= pkgs

        peak_load = max(peak_load, running_load)
        peak_vol = max(peak_vol, running_vol)
        peak_pkgs = max(peak_pkgs, running_pkgs)

    if constraints.capacity_grams > 0 and peak_load > constraints.capacity_grams:
        violations.append(Violation(
            violation_type=ViolationType.CAPACITY_EXCEEDED,
            route_courier_id=route.courier_id,
            order_id=None,
            message=(
                f"Peak load {peak_load}g exceeds capacity "
                f"{constraints.capacity_grams}g "
                f"(over by {peak_load - constraints.capacity_grams}g)"
            ),
        ))

    if constraints.capacity_volume_cm3 > 0 and peak_vol > constraints.capacity_volume_cm3:
        violations.append(Violation(
            violation_type=ViolationType.CAPACITY_EXCEEDED,
            route_courier_id=route.courier_id,
            order_id=None,
            message=(
                f"Peak volume {peak_vol}cm³ exceeds capacity "
                f"{constraints.capacity_volume_cm3}cm³ "
                f"(over by {peak_vol - constraints.capacity_volume_cm3}cm³)"
            ),
        ))

    if constraints.max_packages > 0 and peak_pkgs > constraints.max_packages:
        violations.append(Violation(
            violation_type=ViolationType.MAX_PACKAGES_EXCEEDED,
            route_courier_id=route.courier_id,
            order_id=None,
            message=(
                f"Peak package count {peak_pkgs} exceeds max "
                f"{constraints.max_packages}"
            ),
        ))

    return violations


_DEFAULT_LATENESS_GRACE_S = 300


def _check_time_windows(
    route: PlannedRoute,
    order_pickup_windows: dict[str, tuple[int, int]],
    order_time_windows: dict[str, tuple[int, int]],
    depot_pickup_order_ids: set[str] | None = None,
    lateness_grace_by_order_id: dict[str, int] | None = None,
) -> list[Violation]:
    violations = []
    depot_pickup_order_ids = depot_pickup_order_ids or set()
    lateness_grace_by_order_id = lateness_grace_by_order_id or {}

    for stop in route.stops:
        if stop.stop_type == "PICKUP":
            if stop.order_id in depot_pickup_order_ids:
                continue
            ptw = order_pickup_windows.get(stop.order_id)
            if ptw:
                _, latest_s = ptw
                if stop.arrival_time_s > latest_s:
                    delay = stop.arrival_time_s - latest_s
                    grace = lateness_grace_by_order_id.get(
                        stop.order_id, _DEFAULT_LATENESS_GRACE_S
                    )
                    violations.append(Violation(
                        violation_type=ViolationType.TIME_WINDOW_VIOLATED,
                        route_courier_id=route.courier_id,
                        order_id=stop.order_id,
                        message=(
                            f"Pickup for order {stop.order_id[:8]} arrives at "
                            f"{_fmt_time(stop.arrival_time_s)}, "
                            f"window closes at {_fmt_time(latest_s)} "
                            f"({delay}s late)"
                        ),
                        severity="error" if delay > grace else "warning",
                    ))
        elif stop.stop_type == "DELIVERY":
            tw = order_time_windows.get(stop.order_id)
            if tw:
                _, latest_s = tw
                if stop.arrival_time_s > latest_s:
                    delay = stop.arrival_time_s - latest_s
                    grace = lateness_grace_by_order_id.get(
                        stop.order_id, _DEFAULT_LATENESS_GRACE_S
                    )
                    msg = (f"Delivery for order {stop.order_id[:8]} arrives at "
                           f"{_fmt_time(stop.arrival_time_s)}, "
                           f"window closes at {_fmt_time(latest_s)} "
                           f"({delay}s late)")
                    if delay > grace:
                        violations.append(Violation(
                            violation_type=ViolationType.TIME_WINDOW_VIOLATED,
                            route_courier_id=route.courier_id,
                            order_id=stop.order_id,
                            message=msg,
                        ))
                    else:
                        violations.append(Violation(
                            violation_type=ViolationType.TIME_WINDOW_VIOLATED,
                            route_courier_id=route.courier_id,
                            order_id=stop.order_id,
                            message=f"Delivery for order {stop.order_id[:8]} is {delay}s past window (marginal)",
                            severity="warning",
                        ))
    return violations


def _check_shift_hours(route: PlannedRoute, constraints: RouteConstraints | None) -> list[Violation]:
    violations = []
    if constraints and route.stops:
        first_arrival = route.stops[0].arrival_time_s
        last_departure = route.route_end_time_s or route.stops[-1].departure_time_s

        if first_arrival < constraints.start_time_s:
            violations.append(Violation(
                violation_type=ViolationType.SHIFT_EXCEEDED,
                route_courier_id=route.courier_id,
                order_id=None,
                message=(
                    f"Route starts at {_fmt_time(first_arrival)}, "
                    f"before shift start {_fmt_time(constraints.start_time_s)}"
                ),
                severity="warning",
            ))

        if last_departure > constraints.end_time_s:
            violations.append(Violation(
                violation_type=ViolationType.SHIFT_EXCEEDED,
                route_courier_id=route.courier_id,
                order_id=None,
                message=(
                    f"Route ends at {_fmt_time(last_departure)}, "
                    f"after shift end {_fmt_time(constraints.end_time_s)}"
                ),
            ))
    return violations


def _check_max_stops(
    route: PlannedRoute,
    constraints: RouteConstraints | None,
    depot_pickup_order_ids: set[str] | None = None,
) -> list[Violation]:
    violations = []
    depot_pickup_order_ids = depot_pickup_order_ids or set()
    if constraints and constraints.max_stops > 0:
        segment_stop_count = 0
        max_segment_stop_count = 0
        for stop in route.stops:
            if stop.stop_type in ("DEPOT_RETURN", "DEPOT_RELOAD"):
                max_segment_stop_count = max(max_segment_stop_count, segment_stop_count)
                segment_stop_count = 0
                continue
            # Phase 3 multi-depot — a stop counts as a depot pickup if it
            # lands at ANY of the vehicle's allowed depots, not just the
            # home depot. Empty tuple → only the legacy `depot_index`.
            depot_locations = constraints.depot_indices
            if (
                stop.stop_type == "PICKUP"
                and (
                    stop.order_id in depot_pickup_order_ids
                    or (
                        depot_locations
                        and stop.location_index in depot_locations
                    )
                )
            ):
                continue
            segment_stop_count += 1
        max_segment_stop_count = max(max_segment_stop_count, segment_stop_count)
        if max_segment_stop_count > constraints.max_stops:
            violations.append(Violation(
                violation_type=ViolationType.MAX_STOPS_EXCEEDED,
                route_courier_id=route.courier_id,
                order_id=None,
                message=(
                    f"Route segment has {max_segment_stop_count} stops, "
                    f"exceeds max {constraints.max_stops}"
                ),
            ))
    return violations


def _check_max_distance(route: PlannedRoute, constraints: RouteConstraints | None) -> list[Violation]:
    violations = []
    if constraints and constraints.max_distance_m > 0 and route.total_distance_m > constraints.max_distance_m:
        violations.append(Violation(
            violation_type=ViolationType.MAX_DISTANCE_EXCEEDED,
            route_courier_id=route.courier_id,
            order_id=None,
            message=(
                f"Route distance {route.total_distance_m}m exceeds "
                f"max {constraints.max_distance_m}m"
            ),
        ))
    return violations
