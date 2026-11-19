from app.algorithms.lexicographic_solver import _solution_key, lexicographic_solve_batch
from app.algorithms.solution_validator import ValidationResult
from app.algorithms.vrp_solver import (
    EconomicCostBreakdown,
    OrderData,
    PlannedRoute,
    SolverStatus,
    VRPInput,
    VRPSolution,
    VehicleData,
    build_vrp_input,
)
from app.models.enums import VehicleType


def _matrix(n: int, value: int = 60) -> list[list[int]]:
    return [[0 if i == j else value for i in range(n)] for j in range(n)]


def _vehicle(courier_id: str, *, capacity: int = 10_000) -> VehicleData:
    return VehicleData(
        courier_id=courier_id,
        vehicle_type=VehicleType.CAR_VAN,
        capacity_grams=capacity,
        depot_index=0,
        start_time_s=0,
        end_time_s=86_400,
        hard_end_time_s=86_400,
        max_stops=0,
    )


def _order(order_id: str, delivery_index: int, *, weight: int = 1_000) -> OrderData:
    return OrderData(
        order_id=order_id,
        pickup_index=0,
        delivery_index=delivery_index,
        weight_grams=weight,
        service_time_s=0,
        latest_time_s=86_400,
        hard_latest_time_s=86_400,
        pickup_earliest_time_s=0,
        pickup_latest_time_s=86_400,
    )


def test_depot_pickup_service_time_is_not_charged_per_order_pickup():
    orders = [
        OrderData(
            order_id=f"o{i}",
            pickup_index=0,
            delivery_index=i + 1,
            weight_grams=100,
            service_time_s=300,
            latest_time_s=3_600,
            hard_latest_time_s=3_600,
            pickup_earliest_time_s=0,
            pickup_latest_time_s=3_600,
        )
        for i in range(6)
    ]
    inp = build_vrp_input(
        locations=[(0, 0)] + [(0, i + 1) for i in range(6)],
        distance_matrix=_matrix(7, 10),
        duration_matrix=_matrix(7, 10),
        orders=orders,
        vehicles=[_vehicle("c1", capacity=10_000)],
    )

    result = lexicographic_solve_batch(inp, time_limit_seconds=1, reload_time_s=300, max_reload_count=None)

    assert result.validation.error_count == 0
    assert result.solution.unassigned_orders == []
    assert result.solution.routes[0].total_duration_s < 2_400


def test_merchant_pickup_service_time_is_still_charged():
    inp = build_vrp_input(
        locations=[(0, 0), (0, 1), (0, 2)],
        distance_matrix=_matrix(3, 10),
        duration_matrix=_matrix(3, 10),
        orders=[
            OrderData(
                order_id="merchant",
                pickup_index=1,
                delivery_index=2,
                weight_grams=100,
                service_time_s=300,
                latest_time_s=1_000,
                hard_latest_time_s=1_000,
                pickup_earliest_time_s=0,
                pickup_latest_time_s=1_000,
            )
        ],
        vehicles=[_vehicle("c1", capacity=10_000)],
    )

    result = lexicographic_solve_batch(inp, time_limit_seconds=1, reload_time_s=300, max_reload_count=None)

    assert result.validation.error_count == 0
    assert result.solution.unassigned_orders == []
    assert result.solution.routes[0].total_duration_s >= 620


def test_lexicographic_key_prefers_assignment_over_cheaper_cost():
    cheaper_drop = VRPSolution(
        routes=[],
        unassigned_orders=["o1"],
        total_distance_m=0,
        total_duration_s=0,
        solver_status=SolverStatus.FEASIBLE,
        cost_breakdown=EconomicCostBreakdown(total_generalized_cost=0),
    )
    expensive_assign = VRPSolution(
        routes=[PlannedRoute(courier_id="c1", stops=[], total_distance_m=10_000, total_duration_s=1000)],
        unassigned_orders=[],
        total_distance_m=10_000,
        total_duration_s=1000,
        solver_status=SolverStatus.FEASIBLE,
        cost_breakdown=EconomicCostBreakdown(total_generalized_cost=10_000),
    )

    valid = ValidationResult(is_valid=True)
    assert _solution_key(expensive_assign, valid, {"o1"}) < _solution_key(cheaper_drop, valid, {"o1"})


def test_flexible_reload_assigns_orders_that_exceed_single_load_capacity():
    inp = build_vrp_input(
        locations=[(0, 0), (0, 1), (0, 2)],
        distance_matrix=_matrix(3, 1000),
        duration_matrix=_matrix(3, 60),
        orders=[_order("o1", 1, weight=1_000), _order("o2", 2, weight=1_000)],
        vehicles=[_vehicle("c1", capacity=1_000)],
    )

    result = lexicographic_solve_batch(inp, time_limit_seconds=1, reload_time_s=300, max_reload_count=1)

    assert result.validation.error_count == 0
    assert result.solution.unassigned_orders == []
    stop_types = [stop.stop_type for stop in result.solution.routes[0].stops]
    assert "DEPOT_RETURN" in stop_types
    assert "DEPOT_RELOAD" in stop_types


def test_solver_unassigned_despite_global_feasibility_is_classified_separately():
    inp = build_vrp_input(
        locations=[(0, 0), (0, 1), (0, 2)],
        distance_matrix=_matrix(3, 1000),
        duration_matrix=_matrix(3, 60),
        orders=[_order("o1", 1, weight=1_000), _order("o2", 2, weight=1_000)],
        vehicles=[_vehicle("c1", capacity=1_000)],
    )

    result = lexicographic_solve_batch(inp, time_limit_seconds=1, reload_time_s=300, max_reload_count=0)

    assert result.diagnostics["global_feasibility_summary"]["globally_feasible"] == 2
    assert result.diagnostics["unassigned_classification_summary"]["route_interaction_or_solver_budget"] == 1


def test_625_order_seed_assigns_all_globally_feasible_orders():
    order_count = 625
    vehicle_count = 25
    locations = [(0.0, 0.0)] + [(0.0, float(i + 1)) for i in range(order_count)]
    orders = [
        OrderData(
            order_id=f"o{i}",
            pickup_index=0,
            delivery_index=i + 1,
            weight_grams=1_000,
            service_time_s=0,
            latest_time_s=86_400,
            hard_latest_time_s=86_400,
            pickup_earliest_time_s=0,
            pickup_latest_time_s=86_400,
            allowed_vehicle_indices=(i % vehicle_count,),
        )
        for i in range(order_count)
    ]
    vehicles = [_vehicle(f"c{i}", capacity=100_000) for i in range(vehicle_count)]
    inp = build_vrp_input(
        locations=locations,
        distance_matrix=_matrix(len(locations), 100),
        duration_matrix=_matrix(len(locations), 10),
        orders=orders,
        vehicles=vehicles,
    )

    result = lexicographic_solve_batch(inp, time_limit_seconds=1, max_reload_count=0)

    assert result.validation.error_count == 0
    assert result.solution.unassigned_orders == []
    assert result.diagnostics["assignment_count_status"] == "all_globally_feasible_assigned"


def test_625_order_default_max_stops_with_realistic_delivery_service():
    order_count = 625
    vehicle_count = 25
    locations = [(0.0, 0.0)] + [(0.0, float(i + 1)) for i in range(order_count)]
    orders = [
        OrderData(
            order_id=f"o{i}",
            pickup_index=0,
            delivery_index=i + 1,
            weight_grams=1_000,
            service_time_s=300,
            latest_time_s=86_400,
            hard_latest_time_s=86_400,
            pickup_earliest_time_s=0,
            pickup_latest_time_s=86_400,
            allowed_vehicle_indices=(i % vehicle_count,),
        )
        for i in range(order_count)
    ]
    vehicles = [
        VehicleData(
            courier_id=f"c{i}",
            vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=100_000,
            depot_index=0,
            start_time_s=0,
            end_time_s=86_400,
            hard_end_time_s=86_400,
            max_stops=50,
        )
        for i in range(vehicle_count)
    ]
    inp = build_vrp_input(
        locations=locations,
        distance_matrix=_matrix(len(locations), 10),
        duration_matrix=_matrix(len(locations), 10),
        orders=orders,
        vehicles=vehicles,
    )

    result = lexicographic_solve_batch(inp, time_limit_seconds=1, max_reload_count=None)

    assert result.validation.error_count == 0
    assert result.solution.unassigned_orders == []
    assert result.diagnostics["stage_assignment_counts"]["final_assigned"] == 625
