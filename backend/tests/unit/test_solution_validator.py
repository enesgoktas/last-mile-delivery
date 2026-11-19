"""Tests for the solution validator."""

from app.algorithms.solution_validator import (
    RouteConstraints,
    ViolationType,
    validate_solution,
)
from app.algorithms.vrp_solver import (
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VRPSolution,
)


def _stop(order_id: str, stop_type: str, arrival: int, departure: int, lat: float = 0.0, lon: float = 0.0) -> PlannedStop:
    return PlannedStop(
        order_id=order_id,
        stop_type=stop_type,
        location_index=0,
        lat=lat,
        lon=lon,
        arrival_time_s=arrival,
        departure_time_s=departure,
    )


def _solution(routes: list[PlannedRoute]) -> VRPSolution:
    return VRPSolution(
        routes=routes,
        unassigned_orders=[],
        total_distance_m=0,
        total_duration_s=0,
        solver_status=SolverStatus.OPTIMAL,
    )


class TestValidRoute:
    def test_valid_solution_passes(self):
        route = PlannedRoute(
            courier_id="c1",
            stops=[
                _stop("o1", "PICKUP", 28800, 29100),
                _stop("o1", "DELIVERY", 29400, 29700),
            ],
            total_distance_m=5000,
            total_duration_s=900,
        )
        result = validate_solution(
            _solution([route]),
            order_weights={"o1": 2000},
            route_constraints={
                "c1": RouteConstraints(capacity_grams=50000, start_time_s=28800, end_time_s=64800),
            },
            order_time_windows={"o1": (28800, 36000)},
        )
        assert result.is_valid
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_empty_solution_is_valid(self):
        result = validate_solution(_solution([]))
        assert result.is_valid
        assert result.routes_checked == 0


class TestPickupDeliveryOrder:
    def test_pickup_after_delivery_is_violation(self):
        route = PlannedRoute(
            courier_id="c1",
            stops=[
                _stop("o1", "DELIVERY", 28800, 29100),
                _stop("o1", "PICKUP", 29400, 29700),
            ],
            total_distance_m=5000,
            total_duration_s=900,
        )
        result = validate_solution(_solution([route]))
        assert not result.is_valid
        violations = [v for v in result.violations if v.violation_type == ViolationType.PICKUP_AFTER_DELIVERY]
        assert len(violations) == 1

    def test_missing_delivery_is_violation(self):
        route = PlannedRoute(
            courier_id="c1",
            stops=[_stop("o1", "PICKUP", 28800, 29100)],
            total_distance_m=1000,
            total_duration_s=300,
        )
        result = validate_solution(_solution([route]))
        assert not result.is_valid
        violations = [v for v in result.violations if v.violation_type == ViolationType.MISSING_PICKUP_OR_DELIVERY]
        assert len(violations) == 1


class TestCapacity:
    def test_capacity_exceeded(self):
        route = PlannedRoute(
            courier_id="c1",
            stops=[
                _stop("o1", "PICKUP", 28800, 29100),
                _stop("o2", "PICKUP", 29400, 29700),
                _stop("o1", "DELIVERY", 30000, 30300),
                _stop("o2", "DELIVERY", 30600, 30900),
            ],
            total_distance_m=10000,
            total_duration_s=2100,
        )
        result = validate_solution(
            _solution([route]),
            order_weights={"o1": 30000, "o2": 30000},
            route_constraints={
                "c1": RouteConstraints(capacity_grams=50000),
            },
        )
        assert not result.is_valid
        violations = [v for v in result.violations if v.violation_type == ViolationType.CAPACITY_EXCEEDED]
        assert len(violations) == 1
        assert "60000g exceeds capacity 50000g" in violations[0].message

    def test_capacity_within_limit(self):
        route = PlannedRoute(
            courier_id="c1",
            stops=[
                _stop("o1", "PICKUP", 28800, 29100),
                _stop("o1", "DELIVERY", 29400, 29700),
                _stop("o2", "PICKUP", 30000, 30300),
                _stop("o2", "DELIVERY", 30600, 30900),
            ],
            total_distance_m=10000,
            total_duration_s=2100,
        )
        result = validate_solution(
            _solution([route]),
            order_weights={"o1": 30000, "o2": 30000},
            route_constraints={
                "c1": RouteConstraints(capacity_grams=50000),
            },
        )
        cap_violations = [v for v in result.violations if v.violation_type == ViolationType.CAPACITY_EXCEEDED]
        assert len(cap_violations) == 0


class TestTimeWindows:
    def test_late_delivery_is_error(self):
        route = PlannedRoute(
            courier_id="c1",
            stops=[
                _stop("o1", "PICKUP", 28800, 29100),
                _stop("o1", "DELIVERY", 40000, 40300),
            ],
            total_distance_m=5000,
            total_duration_s=11500,
        )
        result = validate_solution(
            _solution([route]),
            order_time_windows={"o1": (28800, 36000)},
        )
        tw_violations = [v for v in result.violations if v.violation_type == ViolationType.TIME_WINDOW_VIOLATED]
        assert len(tw_violations) == 1
        assert tw_violations[0].severity == "error"

    def test_marginal_late_is_warning(self):
        route = PlannedRoute(
            courier_id="c1",
            stops=[
                _stop("o1", "PICKUP", 28800, 29100),
                _stop("o1", "DELIVERY", 36200, 36500),
            ],
            total_distance_m=5000,
            total_duration_s=7700,
        )
        result = validate_solution(
            _solution([route]),
            order_time_windows={"o1": (28800, 36000)},
        )
        tw_violations = [v for v in result.violations if v.violation_type == ViolationType.TIME_WINDOW_VIOLATED]
        assert len(tw_violations) == 1
        assert tw_violations[0].severity == "warning"


class TestShiftConstraints:
    def test_route_exceeds_shift_end(self):
        route = PlannedRoute(
            courier_id="c1",
            stops=[
                _stop("o1", "PICKUP", 28800, 29100),
                _stop("o1", "DELIVERY", 65000, 65300),
            ],
            total_distance_m=5000,
            total_duration_s=36500,
        )
        result = validate_solution(
            _solution([route]),
            route_constraints={
                "c1": RouteConstraints(start_time_s=28800, end_time_s=64800),
            },
        )
        shift_violations = [v for v in result.violations if v.violation_type == ViolationType.SHIFT_EXCEEDED]
        assert len(shift_violations) == 1


class TestMaxStopsAndDistance:
    def test_max_stops_exceeded(self):
        stops = []
        for i in range(6):
            stops.append(_stop(f"o{i}", "PICKUP", 28800 + i * 600, 28800 + i * 600 + 300))
            stops.append(_stop(f"o{i}", "DELIVERY", 32400 + i * 600, 32400 + i * 600 + 300))
        route = PlannedRoute(courier_id="c1", stops=stops, total_distance_m=20000, total_duration_s=7200)
        result = validate_solution(
            _solution([route]),
            route_constraints={"c1": RouteConstraints(max_stops=10)},
        )
        violations = [v for v in result.violations if v.violation_type == ViolationType.MAX_STOPS_EXCEEDED]
        assert len(violations) == 1

    def test_max_stops_resets_after_depot_reload(self):
        first_trip = [
            _stop(f"a{i}", "PICKUP", 28800 + i * 600, 28800 + i * 600 + 300)
            for i in range(5)
        ] + [
            _stop(f"a{i}", "DELIVERY", 32400 + i * 600, 32400 + i * 600 + 300)
            for i in range(5)
        ]
        second_trip = [
            _stop(f"b{i}", "PICKUP", 43200 + i * 600, 43200 + i * 600 + 300)
            for i in range(5)
        ] + [
            _stop(f"b{i}", "DELIVERY", 46800 + i * 600, 46800 + i * 600 + 300)
            for i in range(5)
        ]
        route = PlannedRoute(
            courier_id="c1",
            stops=[
                *first_trip,
                _stop("__DEPOT_RETURN__", "DEPOT_RETURN", 39600, 39600),
                _stop("__DEPOT_RELOAD__", "DEPOT_RELOAD", 39600, 40500),
                *second_trip,
            ],
            total_distance_m=20000,
            total_duration_s=21600,
        )

        result = validate_solution(
            _solution([route]),
            route_constraints={"c1": RouteConstraints(max_stops=10)},
        )

        violations = [v for v in result.violations if v.violation_type == ViolationType.MAX_STOPS_EXCEEDED]
        assert violations == []

    def test_hidden_depot_pickups_do_not_count_toward_max_stops(self):
        stops = []
        for i in range(12):
            stops.append(_stop(f"o{i}", "PICKUP", 28800 + i, 28800 + i))
        for i in range(12):
            stops.append(_stop(f"o{i}", "DELIVERY", 32400 + i * 60, 32400 + i * 60 + 300))
        route = PlannedRoute(
            courier_id="c1",
            stops=stops,
            total_distance_m=10_000,
            total_duration_s=6_000,
        )

        result = validate_solution(
            _solution([route]),
            route_constraints={"c1": RouteConstraints(max_stops=12, depot_index=0)},
        )

        violations = [v for v in result.violations if v.violation_type == ViolationType.MAX_STOPS_EXCEEDED]
        assert violations == []

    def test_max_distance_exceeded(self):
        route = PlannedRoute(
            courier_id="c1",
            stops=[
                _stop("o1", "PICKUP", 28800, 29100),
                _stop("o1", "DELIVERY", 32400, 32700),
            ],
            total_distance_m=60000,
            total_duration_s=3900,
        )
        result = validate_solution(
            _solution([route]),
            route_constraints={"c1": RouteConstraints(max_distance_m=50000)},
        )
        violations = [v for v in result.violations if v.violation_type == ViolationType.MAX_DISTANCE_EXCEEDED]
        assert len(violations) == 1


class TestDuplicateOrders:
    def test_order_on_multiple_routes(self):
        route1 = PlannedRoute(
            courier_id="c1",
            stops=[
                _stop("o1", "PICKUP", 28800, 29100),
                _stop("o1", "DELIVERY", 29400, 29700),
            ],
            total_distance_m=5000,
            total_duration_s=900,
        )
        route2 = PlannedRoute(
            courier_id="c2",
            stops=[
                _stop("o1", "PICKUP", 28800, 29100),
                _stop("o1", "DELIVERY", 29400, 29700),
            ],
            total_distance_m=5000,
            total_duration_s=900,
        )
        result = validate_solution(_solution([route1, route2]))
        violations = [v for v in result.violations if v.violation_type == ViolationType.DUPLICATE_ORDER]
        assert len(violations) == 1


class TestSummaryOutput:
    def test_summary_format(self):
        route = PlannedRoute(
            courier_id="c1",
            stops=[
                _stop("o1", "DELIVERY", 28800, 29100),
                _stop("o1", "PICKUP", 29400, 29700),
            ],
            total_distance_m=5000,
            total_duration_s=900,
        )
        result = validate_solution(_solution([route]))
        summary = result.summary()
        assert "is_valid" in summary
        assert "violations" in summary
        assert "error_count" in summary
        assert "warning_count" in summary
        assert isinstance(summary["violations"], list)
        assert summary["is_valid"] is False
