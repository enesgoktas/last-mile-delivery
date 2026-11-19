"""Unit tests for the VRP solver.

These tests use small, deterministic inputs — no OSRM or DB required.
We construct synthetic distance/duration matrices inline and verify
that the solver handles constraints correctly.
"""
import pytest

from app.algorithms.vrp_solver import (
    OrderData,
    SolverStatus,
    VehicleData,
    build_vrp_input,
    solve,
)
from app.models.enums import OrderPriority, VehicleType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_matrix(n: int, default: int = 1000) -> list[list[int]]:
    """Create an N×N matrix with `default` everywhere and 0 on diagonal."""
    return [[0 if i == j else default for j in range(n)] for i in range(n)]


def _economic_order(
    *,
    order_id: str,
    pickup_index: int,
    delivery_index: int,
    weight_grams: int = 1_000,
    latest_time_s: int = 86_400,
    hard_latest_time_s: int | None = None,
    priority: OrderPriority = OrderPriority.NORMAL,
    priority_penalty_multiplier: float = 1.0,
    lateness_penalty_per_minute: int = 0,
    unserved_penalty: int = 0,
) -> OrderData:
    pickup_latest_time_s = (
        hard_latest_time_s
        if hard_latest_time_s is not None
        else (latest_time_s if latest_time_s > 0 else 86_400)
    )
    return OrderData(
        order_id=order_id,
        pickup_index=pickup_index,
        delivery_index=delivery_index,
        weight_grams=weight_grams,
        is_fragile=False,
        is_temp_sensitive=False,
        earliest_time_s=0,
        latest_time_s=latest_time_s,
        hard_latest_time_s=hard_latest_time_s,
        service_time_s=0,
        pickup_earliest_time_s=0,
        pickup_latest_time_s=pickup_latest_time_s,
        priority=priority,
        priority_penalty_multiplier=priority_penalty_multiplier,
        lateness_penalty_per_minute=lateness_penalty_per_minute,
        unserved_penalty=unserved_penalty,
    )


def _economic_vehicle(
    *,
    courier_id: str,
    vehicle_type: VehicleType = VehicleType.CAR_VAN,
    capacity_grams: int = 50_000,
    end_time_s: int = 86_400,
    fixed_route_cost: int = 0,
    distance_cost_per_km: int = 0,
    labor_cost_per_minute: int = 0,
    fuel_cost_per_km: int = 0,
    overtime_cost_per_minute: int = 0,
    overtime_penalty_per_minute: int = 0,
    trip_index: int = 0,
    physical_courier_id: str = "",
) -> VehicleData:
    return VehicleData(
        courier_id=courier_id,
        vehicle_type=vehicle_type,
        capacity_grams=capacity_grams,
        depot_index=0,
        start_time_s=0,
        end_time_s=end_time_s,
        fixed_route_cost=fixed_route_cost,
        distance_cost_per_km=distance_cost_per_km,
        labor_cost_per_minute=labor_cost_per_minute,
        fuel_cost_per_km=fuel_cost_per_km,
        overtime_cost_per_minute=overtime_cost_per_minute,
        overtime_penalty_per_minute=overtime_penalty_per_minute,
        trip_index=trip_index,
        physical_courier_id=physical_courier_id,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSolverBasic:
    """Basic solver behaviour."""

    def test_empty_orders(self):
        """No orders → NOT_FOUND, all empty."""
        vehicle = VehicleData(
            courier_id="c1", vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=100_000, depot_index=0,
            start_time_s=0, end_time_s=86400,
        )
        inp = build_vrp_input(
            locations=[(41.0, 29.0)],
            distance_matrix=[[0]],
            duration_matrix=[[0]],
            orders=[],
            vehicles=[vehicle],
        )
        sol = solve(inp)
        assert sol.solver_status == SolverStatus.NOT_FOUND
        assert sol.routes == []

    def test_no_vehicles(self):
        """No couriers → all orders unassigned."""
        order = OrderData(
            order_id="o1", pickup_index=1, delivery_index=2,
            weight_grams=1000, is_fragile=False, is_temp_sensitive=False,
            earliest_time_s=0, latest_time_s=86400, service_time_s=300,
        )
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3),
            duration_matrix=_simple_matrix(3, 600),
            orders=[order],
            vehicles=[],
        )
        sol = solve(inp)
        assert sol.solver_status == SolverStatus.NOT_FOUND
        assert "o1" in sol.unassigned_orders

    def test_single_order_single_courier(self):
        """Trivial case: 1 order, 1 courier → 1 route with 2 stops."""
        order = OrderData(
            order_id="o1", pickup_index=1, delivery_index=2,
            weight_grams=5000, is_fragile=False, is_temp_sensitive=False,
            earliest_time_s=0, latest_time_s=86400, service_time_s=300,
        )
        vehicle = VehicleData(
            courier_id="c1", vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=50_000, depot_index=0,
            start_time_s=0, end_time_s=86400,
        )
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3, 1000),
            duration_matrix=_simple_matrix(3, 600),
            orders=[order],
            vehicles=[vehicle],
        )
        sol = solve(inp)
        assert sol.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        assert len(sol.routes) == 1
        assert len(sol.routes[0].stops) == 2  # pickup + delivery
        assert sol.routes[0].stops[0].stop_type == "PICKUP"
        assert sol.routes[0].stops[1].stop_type == "DELIVERY"
        assert sol.unassigned_orders == []


class TestSolverCapacity:
    """Capacity constraint tests."""

    def test_weight_overflow_drops_order(self):
        """Order too heavy for the only courier → unassigned."""
        heavy_order = OrderData(
            order_id="o_heavy", pickup_index=1, delivery_index=2,
            weight_grams=999_000,  # 999 kg
            is_fragile=False, is_temp_sensitive=False,
            earliest_time_s=0, latest_time_s=86400, service_time_s=300,
        )
        small_vehicle = VehicleData(
            courier_id="c1", vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=10_000,  # 10 kg
            depot_index=0,
            start_time_s=0, end_time_s=86400,
        )
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3),
            duration_matrix=_simple_matrix(3, 600),
            orders=[heavy_order],
            vehicles=[small_vehicle],
        )
        sol = solve(inp)
        # The order should be dropped (unassigned) rather than crashing
        assert "o_heavy" in sol.unassigned_orders


class TestSolverTimeWindows:
    """Time window constraint tests."""

    def test_tight_window_unreachable(self):
        """Order with a time window already passed → unassigned."""
        past_order = OrderData(
            order_id="o_past", pickup_index=1, delivery_index=2,
            weight_grams=1000, is_fragile=False, is_temp_sensitive=False,
            # Window: 0-10 seconds — impossibly tight given travel time
            earliest_time_s=0, latest_time_s=10, service_time_s=300,
        )
        vehicle = VehicleData(
            courier_id="c1", vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=50_000, depot_index=0,
            start_time_s=0, end_time_s=86400,
        )
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3, 5000),
            duration_matrix=_simple_matrix(3, 3600),  # 1 hour between any two
            orders=[past_order],
            vehicles=[vehicle],
        )
        sol = solve(inp)
        assert "o_past" in sol.unassigned_orders

    def test_explicit_pickup_window_is_respected(self):
        """Pickup can be infeasible even when delivery window is wide."""
        order = OrderData(
            order_id="o_pickup_window",
            pickup_index=1,
            delivery_index=2,
            weight_grams=1000,
            is_fragile=False,
            is_temp_sensitive=False,
            earliest_time_s=0,
            latest_time_s=86400,
            service_time_s=300,
            pickup_earliest_time_s=0,
            pickup_latest_time_s=10,
        )
        vehicle = VehicleData(
            courier_id="c1",
            vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=50_000,
            depot_index=0,
            start_time_s=0,
            end_time_s=86400,
        )
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3, 5000),
            duration_matrix=_simple_matrix(3, 3600),
            orders=[order],
            vehicles=[vehicle],
        )
        sol = solve(inp)
        assert "o_pickup_window" in sol.unassigned_orders


class TestSolverFragile:
    """Fragile order + motorcycle exclusion."""

    def test_fragile_excludes_motorcycle(self):
        """Fragile order with only a motorcycle courier → unassigned."""
        fragile_order = OrderData(
            order_id="o_fragile", pickup_index=1, delivery_index=2,
            weight_grams=2000, is_fragile=True, is_temp_sensitive=False,
            earliest_time_s=0, latest_time_s=86400, service_time_s=300,
        )
        motorcycle = VehicleData(
            courier_id="c_moto", vehicle_type=VehicleType.MOTORCYCLE,
            capacity_grams=50_000, depot_index=0,
            start_time_s=0, end_time_s=86400,
        )
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3),
            duration_matrix=_simple_matrix(3, 600),
            orders=[fragile_order],
            vehicles=[motorcycle],
        )
        sol = solve(inp)
        assert "o_fragile" in sol.unassigned_orders

    def test_fragile_assigned_to_car(self):
        """Fragile order with a car courier → assigned successfully."""
        fragile_order = OrderData(
            order_id="o_fragile", pickup_index=1, delivery_index=2,
            weight_grams=2000, is_fragile=True, is_temp_sensitive=False,
            earliest_time_s=0, latest_time_s=86400, service_time_s=300,
        )
        car = VehicleData(
            courier_id="c_car", vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=50_000, depot_index=0,
            start_time_s=0, end_time_s=86400,
        )
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3),
            duration_matrix=_simple_matrix(3, 600),
            orders=[fragile_order],
            vehicles=[car],
        )
        sol = solve(inp)
        assert sol.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        assert "o_fragile" not in sol.unassigned_orders


class TestSolverMultipleOrders:
    """Multiple orders across multiple couriers."""

    def test_two_orders_two_couriers(self):
        """Each courier gets 1 order, no orders unassigned."""
        orders = [
            OrderData(
                order_id=f"o{i}", pickup_index=2 * i + 1,
                delivery_index=2 * i + 2,
                weight_grams=5000, is_fragile=False, is_temp_sensitive=False,
                earliest_time_s=0, latest_time_s=86400, service_time_s=300,
            )
            for i in range(2)
        ]
        vehicles = [
            VehicleData(
                courier_id=f"c{i}", vehicle_type=VehicleType.CAR_VAN,
                capacity_grams=50_000, depot_index=0,
                start_time_s=0, end_time_s=86400,
            )
            for i in range(2)
        ]
        n = 5  # depot + 2×(pickup + delivery)
        inp = build_vrp_input(
            locations=[(41.0 + i * 0.01, 29.0) for i in range(n)],
            distance_matrix=_simple_matrix(n, 1000),
            duration_matrix=_simple_matrix(n, 600),
            orders=orders,
            vehicles=vehicles,
        )
        sol = solve(inp)
        assert sol.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        assert sol.unassigned_orders == []
        total_stops = sum(len(r.stops) for r in sol.routes)
        assert total_stops == 4  # 2 pickups + 2 deliveries


class TestBuildVRPInput:
    """Input validation."""

    def test_matrix_size_mismatch(self):
        with pytest.raises(ValueError, match="Matrix size mismatch"):
            build_vrp_input(
                locations=[(41.0, 29.0), (41.1, 29.1)],
                distance_matrix=[[0, 1], [1, 0], [2, 2]],  # 3 rows, 2 locs
                duration_matrix=[[0, 1], [1, 0]],
                orders=[], vehicles=[],
            )

    def test_order_index_out_of_bounds(self):
        with pytest.raises(ValueError, match="out of bounds"):
            build_vrp_input(
                locations=[(41.0, 29.0)],
                distance_matrix=[[0]],
                duration_matrix=[[0]],
                orders=[OrderData(
                    order_id="x", pickup_index=5, delivery_index=0,
                    weight_grams=1, is_fragile=False, is_temp_sensitive=False,
                    earliest_time_s=0, latest_time_s=1, service_time_s=0,
                )],
                vehicles=[],
            )

    def test_invalid_pickup_window_raises(self):
        with pytest.raises(ValueError, match="pickup window is invalid"):
            build_vrp_input(
                locations=[(41.0, 29.0), (41.1, 29.1)],
                distance_matrix=[[0, 1], [1, 0]],
                duration_matrix=[[0, 1], [1, 0]],
                orders=[OrderData(
                    order_id="x",
                    pickup_index=0,
                    delivery_index=1,
                    weight_grams=1,
                    is_fragile=False,
                    is_temp_sensitive=False,
                    earliest_time_s=0,
                    latest_time_s=10,
                    service_time_s=0,
                    pickup_earliest_time_s=100,
                    pickup_latest_time_s=50,
                )],
                vehicles=[],
            )


class TestSolverEconomicObjective:
    """Economic objective and bounded soft-window behavior."""

    def test_virtual_trip_activation_allows_multiple_trips_per_courier(self):
        order = _economic_order(order_id="o1", pickup_index=1, delivery_index=2)
        vehicles = [
            _economic_vehicle(
                courier_id=f"courier-1-trip-{trip_index}",
                fixed_route_cost=100,
                trip_index=trip_index,
                physical_courier_id="courier-1",
            )
            for trip_index in range(3)
        ]
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3, 1_000),
            duration_matrix=_simple_matrix(3, 300),
            orders=[order],
            vehicles=vehicles,
        )

        sol = solve(inp, time_limit_seconds=1)

        assert sol.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        assert sol.unassigned_orders == []
        assert [route.courier_id for route in sol.routes] == ["courier-1-trip-0"]

    def test_chooses_cheaper_vehicle_when_feasible_options_match(self):
        order = _economic_order(order_id="o1", pickup_index=1, delivery_index=2)
        cheap_vehicle = _economic_vehicle(courier_id="cheap", fixed_route_cost=100)
        expensive_vehicle = _economic_vehicle(courier_id="expensive", fixed_route_cost=5_000)
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3, 1_000),
            duration_matrix=_simple_matrix(3, 300),
            orders=[order],
            vehicles=[cheap_vehicle, expensive_vehicle],
        )

        sol = solve(inp, time_limit_seconds=1)

        assert sol.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        assert sol.unassigned_orders == []
        assert [route.courier_id for route in sol.routes] == ["cheap"]

    def test_fuel_cost_prefers_motorcycle_for_small_long_distance_order(self):
        order = _economic_order(order_id="o1", pickup_index=1, delivery_index=2)
        motorcycle = _economic_vehicle(
            courier_id="motorcycle",
            vehicle_type=VehicleType.MOTORCYCLE,
            fuel_cost_per_km=1,
        )
        truck = _economic_vehicle(
            courier_id="truck",
            vehicle_type=VehicleType.TRUCK,
            fuel_cost_per_km=8,
        )
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (42.0, 30.0)],
            distance_matrix=[
                [0, 1_000, 100_000],
                [1_000, 0, 100_000],
                [100_000, 100_000, 0],
            ],
            duration_matrix=_simple_matrix(3, 300),
            orders=[order],
            vehicles=[motorcycle, truck],
        )

        sol = solve(inp, time_limit_seconds=1)

        assert sol.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        assert sol.unassigned_orders == []
        assert [route.courier_id for route in sol.routes] == ["motorcycle"]

    def test_capacity_still_blocks_motorcycle_even_when_fuel_is_cheaper(self):
        order = _economic_order(
            order_id="heavy",
            pickup_index=1,
            delivery_index=2,
            weight_grams=9_000,
        )
        motorcycle = _economic_vehicle(
            courier_id="motorcycle",
            vehicle_type=VehicleType.MOTORCYCLE,
            capacity_grams=8_000,
            fuel_cost_per_km=1,
        )
        truck = _economic_vehicle(
            courier_id="truck",
            vehicle_type=VehicleType.TRUCK,
            capacity_grams=50_000,
            fuel_cost_per_km=8,
        )
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (42.0, 30.0)],
            distance_matrix=[
                [0, 1_000, 100_000],
                [1_000, 0, 100_000],
                [100_000, 100_000, 0],
            ],
            duration_matrix=_simple_matrix(3, 300),
            orders=[order],
            vehicles=[motorcycle, truck],
        )

        sol = solve(inp, time_limit_seconds=1)

        assert sol.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        assert sol.unassigned_orders == []
        assert [route.courier_id for route in sol.routes] == ["truck"]

    def test_drops_order_when_lateness_cost_outweighs_unserved_penalty(self):
        late_order = _economic_order(
            order_id="o_late",
            pickup_index=1,
            delivery_index=2,
            latest_time_s=0,
            hard_latest_time_s=7_200,
            lateness_penalty_per_minute=600,
            unserved_penalty=100,
        )
        vehicle = _economic_vehicle(courier_id="c1")
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3, 1_000),
            duration_matrix=_simple_matrix(3, 1_800),
            orders=[late_order],
            vehicles=[vehicle],
        )

        sol = solve(inp, time_limit_seconds=1)

        assert "o_late" in sol.unassigned_orders

    def test_keeps_late_order_when_unserved_penalty_is_more_expensive(self):
        late_order = _economic_order(
            order_id="o_late",
            pickup_index=1,
            delivery_index=2,
            latest_time_s=0,
            hard_latest_time_s=7_200,
            lateness_penalty_per_minute=1,
            unserved_penalty=100_000,
        )
        vehicle = _economic_vehicle(courier_id="c1")
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3, 1_000),
            duration_matrix=_simple_matrix(3, 1_800),
            orders=[late_order],
            vehicles=[vehicle],
        )

        sol = solve(inp, time_limit_seconds=1)

        assert sol.unassigned_orders == []
        assert len(sol.routes) == 1
        assert [stop.order_id for stop in sol.routes[0].stops] == ["o_late", "o_late"]

    def test_hard_latest_cap_still_forces_drop_even_with_large_unserved_penalty(self):
        capped_order = _economic_order(
            order_id="o_capped",
            pickup_index=1,
            delivery_index=2,
            latest_time_s=0,
            hard_latest_time_s=1_800,
            lateness_penalty_per_minute=1,
            unserved_penalty=1_000_000,
        )
        vehicle = _economic_vehicle(courier_id="c1")
        inp = build_vrp_input(
            locations=[(41.0, 29.0), (41.01, 29.01), (41.02, 29.02)],
            distance_matrix=_simple_matrix(3, 1_000),
            duration_matrix=_simple_matrix(3, 1_800),
            orders=[capped_order],
            vehicles=[vehicle],
        )

        sol = solve(inp, time_limit_seconds=1)

        assert "o_capped" in sol.unassigned_orders

    def test_priority_weighting_protects_urgent_order_when_only_one_can_fit(self):
        locations = [
            (41.0, 29.0),
            (41.01, 29.01),
            (41.02, 29.02),
            (41.03, 29.03),
            (41.04, 29.04),
        ]
        distance_matrix = _simple_matrix(5, 10_000)
        duration_matrix = _simple_matrix(5, 3_000)

        for depot_idx, pickup_idx, delivery_idx in ((0, 1, 2), (0, 3, 4)):
            distance_matrix[depot_idx][pickup_idx] = 300
            distance_matrix[pickup_idx][delivery_idx] = 300
            distance_matrix[delivery_idx][depot_idx] = 300
            duration_matrix[depot_idx][pickup_idx] = 300
            duration_matrix[pickup_idx][delivery_idx] = 300
            duration_matrix[delivery_idx][depot_idx] = 300

        urgent_order = _economic_order(
            order_id="urgent",
            pickup_index=1,
            delivery_index=2,
            latest_time_s=1_200,
            hard_latest_time_s=1_200,
            priority=OrderPriority.URGENT,
            priority_penalty_multiplier=2.0,
            unserved_penalty=10_000,
        )
        low_order = _economic_order(
            order_id="low",
            pickup_index=3,
            delivery_index=4,
            latest_time_s=1_200,
            hard_latest_time_s=1_200,
            priority=OrderPriority.LOW,
            priority_penalty_multiplier=0.75,
            unserved_penalty=10_000,
        )
        vehicle = _economic_vehicle(courier_id="c1", end_time_s=1_200)
        inp = build_vrp_input(
            locations=locations,
            distance_matrix=distance_matrix,
            duration_matrix=duration_matrix,
            orders=[urgent_order, low_order],
            vehicles=[vehicle],
        )

        sol = solve(inp, time_limit_seconds=1)
        served_order_ids = {stop.order_id for route in sol.routes for stop in route.stops}

        assert "urgent" in served_order_ids
        assert "low" in sol.unassigned_orders
