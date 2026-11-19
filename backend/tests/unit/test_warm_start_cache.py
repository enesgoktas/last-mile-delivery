"""Tests for warm-start cache."""

from app.algorithms.vrp_solver import (
    OrderData,
    PlannedRoute,
    PlannedStop,
    SolverStatus,
    VehicleData,
    VRPInput,
    VRPSolution,
)
from app.algorithms.warm_start_cache import (
    build_initial_routes,
    cache_solution,
    clear_cache,
    _InProcessCache,
)
from app.models.enums import VehicleType
from unittest.mock import patch, MagicMock
import json


def _stop(order_id, stop_type, arrival=28800, departure=29100):
    return PlannedStop(
        order_id=order_id,
        stop_type=stop_type,
        location_index=0,
        lat=0.0,
        lon=0.0,
        arrival_time_s=arrival,
        departure_time_s=departure,
    )


def _solution(routes):
    return VRPSolution(
        routes=routes,
        unassigned_orders=[],
        total_distance_m=0,
        total_duration_s=0,
        solver_status=SolverStatus.OPTIMAL,
    )


def _order(order_id, pickup_idx=1, delivery_idx=2):
    return OrderData(
        order_id=order_id,
        pickup_index=pickup_idx,
        delivery_index=delivery_idx,
        weight_grams=1000,
        earliest_time_s=28800,
        latest_time_s=64800,
        service_time_s=300,
    )


def _vehicle(courier_id="c1"):
    return VehicleData(
        courier_id=courier_id,
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=50000,
        depot_index=0,
        start_time_s=28800,
        end_time_s=64800,
    )


def _vrp_input(orders, vehicles, n_locs=10):
    locations = [(0.0, 0.0)] * n_locs
    dur = [[300] * n_locs for _ in range(n_locs)]
    dist = [[1000] * n_locs for _ in range(n_locs)]
    for i in range(n_locs):
        dur[i][i] = 0
        dist[i][i] = 0
    return VRPInput(
        locations=locations,
        distance_matrix=dist,
        duration_matrix=dur,
        orders=orders,
        vehicles=vehicles,
    )


class TestCacheSolution:
    def setup_method(self):
        clear_cache()

    def test_cache_stores_assignments(self):
        sol = _solution([
            PlannedRoute(
                courier_id="c1",
                stops=[_stop("o1", "PICKUP"), _stop("o1", "DELIVERY")],
                total_distance_m=5000,
                total_duration_s=900,
            )
        ])
        cache_solution(sol)
        # Build initial routes to verify cache works
        vrp = _vrp_input([_order("o1")], [_vehicle("c1")])
        routes = build_initial_routes(vrp)
        assert routes is not None
        assert len(routes) == 1

    def test_no_cache_returns_none(self):
        vrp = _vrp_input([_order("o1")], [_vehicle("c1")])
        routes = build_initial_routes(vrp)
        assert routes is None

    def test_empty_solution_not_cached(self):
        cache_solution(_solution([]))
        vrp = _vrp_input([_order("o1")], [_vehicle("c1")])
        routes = build_initial_routes(vrp)
        assert routes is None


class TestBuildInitialRoutes:
    def setup_method(self):
        clear_cache()

    def test_matching_orders_produce_routes(self):
        sol = _solution([
            PlannedRoute(
                courier_id="c1",
                stops=[
                    _stop("o1", "PICKUP"),
                    _stop("o1", "DELIVERY"),
                    _stop("o2", "PICKUP"),
                    _stop("o2", "DELIVERY"),
                ],
                total_distance_m=10000,
                total_duration_s=2000,
            )
        ])
        cache_solution(sol)

        vrp = _vrp_input(
            [_order("o1", 1, 2), _order("o2", 3, 4)],
            [_vehicle("c1")],
        )
        routes = build_initial_routes(vrp)
        assert routes is not None
        assert len(routes) == 1
        # 2 orders × 2 nodes each = 4 virtual nodes
        assert len(routes[0]) == 4

    def test_low_overlap_returns_none(self):
        sol = _solution([
            PlannedRoute(
                courier_id="c1",
                stops=[_stop("o1", "PICKUP"), _stop("o1", "DELIVERY")],
                total_distance_m=5000,
                total_duration_s=900,
            )
        ])
        cache_solution(sol)

        # New problem has completely different orders
        vrp = _vrp_input(
            [_order("o99", 1, 2), _order("o98", 3, 4), _order("o97", 5, 6), _order("o96", 7, 8)],
            [_vehicle("c1")],
        )
        routes = build_initial_routes(vrp)
        assert routes is None  # < 30% overlap

    def test_clear_cache_works(self):
        sol = _solution([
            PlannedRoute(
                courier_id="c1",
                stops=[_stop("o1", "PICKUP"), _stop("o1", "DELIVERY")],
                total_distance_m=5000,
                total_duration_s=900,
            )
        ])
        cache_solution(sol)
        clear_cache()
        vrp = _vrp_input([_order("o1")], [_vehicle("c1")])
        routes = build_initial_routes(vrp)
        assert routes is None


class TestRedisCache:
    def setup_method(self):
        clear_cache()
        _InProcessCache._cache.clear()  # Ensure pristine state

    @patch("app.algorithms.warm_start_cache._get_redis")
    def test_redis_serialization_roundtrip(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        
        # Test cache set
        sol = _solution([
            PlannedRoute(
                courier_id="c1",
                stops=[_stop("o1", "PICKUP"), _stop("o1", "DELIVERY")],
                total_distance_m=5000,
                total_duration_s=900,
            )
        ])
        cache_solution(sol)
        
        assert mock_redis.set.call_count == 1
        call_args = mock_redis.set.call_args[0]
        assert call_args[0] == "warm_start:v2:global"
        
        saved_data = json.loads(call_args[1])
        assert saved_data["order_to_courier"] == {"o1": "c1"}
        assert saved_data["courier_routes"] == {"c1": ["o1"]}
        assert "created_at" in saved_data
        
        # Test cache get
        mock_redis.get.return_value = json.dumps(saved_data)
        vrp = _vrp_input([_order("o1")], [_vehicle("c1")])
        routes = build_initial_routes(vrp)
        
        assert mock_redis.get.call_count == 1
        assert routes is not None
        assert len(routes) == 1

    @patch("app.algorithms.warm_start_cache._get_redis")
    def test_redis_unavailable_fallback(self, mock_get_redis):
        # Redis is None
        mock_get_redis.return_value = None
        
        sol = _solution([
            PlannedRoute(
                courier_id="c1",
                stops=[_stop("o1", "PICKUP"), _stop("o1", "DELIVERY")],
                total_distance_m=5000,
                total_duration_s=900,
            )
        ])
        cache_solution(sol)
        
        # Should be in process cache now
        assert _InProcessCache.get("warm_start:v2:global") is not None
        
        vrp = _vrp_input([_order("o1")], [_vehicle("c1")])
        routes = build_initial_routes(vrp)
        
        assert routes is not None
        assert len(routes) == 1
