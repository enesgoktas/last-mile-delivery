"""Tests for the 2-opt post-solver improvement pass."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.algorithms.solution_validator import RouteConstraints
from app.algorithms.two_opt import (
    _check_pickup_delivery_order,
    _compute_total_distance,
    _reverse_segment,
    _simulate_timing,
    two_opt_improve,
)
from app.algorithms.vrp_solver import (
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VRPSolution,
)


def _stop(order_id: str, stop_type: str, loc: int, arrival: int, service: int = 60) -> PlannedStop:
    return PlannedStop(
        order_id=order_id,
        stop_type=stop_type,
        location_index=loc,
        lat=0.0,
        lon=0.0,
        arrival_time_s=arrival,
        departure_time_s=arrival + service,
    )


def _make_solution(routes: list[PlannedRoute]) -> VRPSolution:
    return VRPSolution(
        routes=routes,
        unassigned_orders=[],
        total_distance_m=sum(r.total_distance_m for r in routes),
        total_duration_s=sum(r.total_duration_s for r in routes),
        solver_status=SolverStatus.FEASIBLE,
    )


# 4 locations: 0=depot, 1, 2, 3
# Distances designed so 0→1→2→3 is shortest path but solver gave 0→2→1→3
_DISTANCE_MATRIX = [
    [0, 10, 50, 100],
    [10, 0, 10, 50],
    [50, 10, 0, 10],
    [100, 50, 10, 0],
]

_DURATION_MATRIX = [
    [0, 60, 300, 600],
    [60, 0, 60, 300],
    [300, 60, 0, 60],
    [600, 300, 60, 0],
]


class TestReverseSegment:
    def test_reverse_middle(self):
        stops = [_stop("a", "PICKUP", 0, 0), _stop("b", "PICKUP", 1, 100),
                 _stop("c", "PICKUP", 2, 200), _stop("d", "PICKUP", 3, 300)]
        result = _reverse_segment(stops, 0, 2)
        assert [s.location_index for s in result] == [0, 2, 1, 3]

    def test_reverse_full(self):
        stops = [_stop("a", "PICKUP", 0, 0), _stop("b", "PICKUP", 1, 100),
                 _stop("c", "PICKUP", 2, 200)]
        result = _reverse_segment(stops, -1, 2)
        assert [s.location_index for s in result] == [2, 1, 0]


class TestPickupDeliveryOrder:
    def test_valid_order(self):
        stops = [
            _stop("a", "PICKUP", 0, 0),
            _stop("a", "DELIVERY", 1, 100),
        ]
        assert _check_pickup_delivery_order(stops, {"a": 0}, {"a": 1})

    def test_invalid_order(self):
        stops = [
            _stop("a", "DELIVERY", 1, 0),
            _stop("a", "PICKUP", 0, 100),
        ]
        assert not _check_pickup_delivery_order(stops, {"a": 1}, {"a": 0})


class TestSimulateTiming:
    def test_basic_timing(self):
        stops = [
            _stop("a", "PICKUP", 0, 3600, service=60),
            _stop("a", "DELIVERY", 1, 3720, service=60),
        ]
        result = _simulate_timing(stops, _DURATION_MATRIX)
        assert result is not None
        timed_stops, duration = result
        assert len(timed_stops) == 2
        assert timed_stops[0].arrival_time_s == 3600
        # travel from loc 0 to loc 1 = 60s
        assert timed_stops[1].arrival_time_s == 3600 + 60 + 60  # arrival + service + travel

    def test_empty_stops(self):
        result = _simulate_timing([], _DURATION_MATRIX)
        assert result is not None
        assert result == ([], 0)


class TestComputeDistance:
    def test_basic(self):
        stops = [
            _stop("a", "PICKUP", 0, 0),
            _stop("a", "DELIVERY", 2, 100),
            _stop("b", "PICKUP", 1, 200),
        ]
        # 0→2 = 50, 2→1 = 10 => total 60
        assert _compute_total_distance(stops, _DISTANCE_MATRIX) == 60


class TestTwoOptImprove:
    def test_suboptimal_route_improved(self):
        """Route 0→2→1→3 should be improved to 0→1→2→3 (shorter)."""
        stops = [
            _stop("a", "PICKUP", 0, 3600, service=60),
            _stop("b", "PICKUP", 2, 3960, service=60),
            _stop("a", "DELIVERY", 1, 4080, service=60),
            _stop("b", "DELIVERY", 3, 4200, service=60),
        ]
        # Original distance: 0→2(50) + 2→1(10) + 1→3(50) = 110
        original_route = PlannedRoute(
            courier_id="c1",
            stops=stops,
            total_distance_m=110,
            total_duration_s=660,
        )
        solution = _make_solution([original_route])
        result = two_opt_improve(
            solution, _DURATION_MATRIX, _DISTANCE_MATRIX, time_budget_s=1.0,
        )
        # Should find 0→1→2→3: 0→1(10) + 1→2(10) + 2→3(10) = 30
        assert result.total_distance_m < solution.total_distance_m

    def test_already_optimal(self):
        """Route already at local optimum — should be unchanged."""
        stops = [
            _stop("a", "PICKUP", 0, 3600, service=60),
            _stop("a", "DELIVERY", 1, 3720, service=60),
        ]
        original_route = PlannedRoute(
            courier_id="c1",
            stops=stops,
            total_distance_m=10,
            total_duration_s=180,
        )
        solution = _make_solution([original_route])
        result = two_opt_improve(
            solution, _DURATION_MATRIX, _DISTANCE_MATRIX, time_budget_s=1.0,
        )
        # Too few stops — skipped
        assert result is solution

    def test_empty_solution(self):
        solution = _make_solution([])
        result = two_opt_improve(
            solution, _DURATION_MATRIX, _DISTANCE_MATRIX, time_budget_s=1.0,
        )
        assert result is solution

    def test_time_budget_respected(self):
        """With zero time budget, no improvement should be attempted."""
        stops = [
            _stop("a", "PICKUP", 0, 3600, service=60),
            _stop("b", "PICKUP", 2, 3960, service=60),
            _stop("a", "DELIVERY", 1, 4080, service=60),
            _stop("b", "DELIVERY", 3, 4200, service=60),
        ]
        original_route = PlannedRoute(
            courier_id="c1",
            stops=stops,
            total_distance_m=110,
            total_duration_s=660,
        )
        solution = _make_solution([original_route])
        result = two_opt_improve(
            solution, _DURATION_MATRIX, _DISTANCE_MATRIX, time_budget_s=0.0,
        )
        # Zero budget — routes unchanged
        assert result.routes[0].total_distance_m == 110

    def test_pickup_delivery_preservation(self):
        """Swap must not place delivery before pickup."""
        # Build a route where the only improving swap would violate P→D order
        stops = [
            _stop("a", "PICKUP", 0, 3600, service=60),
            _stop("a", "DELIVERY", 1, 3720, service=60),
            _stop("b", "PICKUP", 2, 3840, service=60),
            _stop("b", "DELIVERY", 3, 3960, service=60),
        ]
        original_route = PlannedRoute(
            courier_id="c1",
            stops=stops,
            total_distance_m=30,
            total_duration_s=420,
        )
        solution = _make_solution([original_route])
        result = two_opt_improve(
            solution, _DURATION_MATRIX, _DISTANCE_MATRIX, time_budget_s=1.0,
        )
        # Verify all orders still have pickup before delivery
        for route in result.routes:
            pickup_pos = {}
            delivery_pos = {}
            for i, stop in enumerate(route.stops):
                if stop.stop_type == "PICKUP":
                    pickup_pos[stop.order_id] = i
                elif stop.stop_type == "DELIVERY":
                    delivery_pos[stop.order_id] = i
            for oid, p in pickup_pos.items():
                d = delivery_pos.get(oid)
                if d is not None:
                    assert p < d, f"Pickup after delivery for order {oid}"

    def test_route_validator_can_reject_lower_distance_reversal(self):
        stops = [
            _stop("a", "PICKUP", 0, 3600, service=60),
            _stop("b", "PICKUP", 2, 3960, service=60),
            _stop("a", "DELIVERY", 1, 4080, service=60),
            _stop("b", "DELIVERY", 3, 4200, service=60),
        ]
        original_route = PlannedRoute(
            courier_id="c1",
            stops=stops,
            total_distance_m=110,
            total_duration_s=660,
        )
        solution = _make_solution([original_route])

        result = two_opt_improve(
            solution,
            _DURATION_MATRIX,
            _DISTANCE_MATRIX,
            time_budget_s=1.0,
            route_validator=lambda route: False,
        )

        assert result.routes[0].total_distance_m == 110

    def test_constraint_maps_reject_window_violating_reversal(self):
        stops = [
            _stop("a", "PICKUP", 0, 3600, service=60),
            _stop("b", "PICKUP", 2, 3960, service=60),
            _stop("a", "DELIVERY", 1, 4080, service=60),
            _stop("b", "DELIVERY", 3, 4200, service=60),
        ]
        original_route = PlannedRoute(
            courier_id="c1",
            stops=stops,
            total_distance_m=110,
            total_duration_s=660,
        )
        solution = _make_solution([original_route])

        result = two_opt_improve(
            solution,
            _DURATION_MATRIX,
            _DISTANCE_MATRIX,
            time_budget_s=1.0,
            route_constraints={"c1": RouteConstraints(capacity_grams=1_000, end_time_s=86_400)},
            order_weights={"a": 1_000, "b": 1_000},
            order_pickup_windows={"b": (0, 3000)},
        )

        assert result.routes[0].total_distance_m == 110
