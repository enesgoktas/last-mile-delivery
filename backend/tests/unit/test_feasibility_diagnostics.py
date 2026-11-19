from app.algorithms.feasibility_diagnostics import (
    diagnose_global_feasibility,
    summarize_cluster_feasibility,
    summarize_global_feasibility,
)
from app.algorithms.vrp_solver import OrderData, VehicleData, build_vrp_input
from app.models.enums import VehicleType


def _matrix(n: int, value: int = 60) -> list[list[int]]:
    return [[0 if i == j else value for j in range(n)] for i in range(n)]


def test_global_infeasible_due_to_weight_is_reported():
    inp = build_vrp_input(
        locations=[(0, 0), (0, 1), (0, 2)],
        distance_matrix=_matrix(3, 1000),
        duration_matrix=_matrix(3, 60),
        orders=[
            OrderData(
                order_id="heavy",
                pickup_index=1,
                delivery_index=2,
                weight_grams=50_000,
                latest_time_s=86_400,
            )
        ],
        vehicles=[
            VehicleData(
                courier_id="c1",
                vehicle_type=VehicleType.CAR_VAN,
                capacity_grams=10_000,
                depot_index=0,
                end_time_s=86_400,
            )
        ],
    )

    diagnostics = diagnose_global_feasibility(inp)
    assert diagnostics[0].globally_feasible is False
    assert "weight_capacity" in diagnostics[0].blocking_reasons
    assert summarize_global_feasibility(diagnostics)["globally_infeasible"] == 1


def test_cluster_ineligible_is_separate_from_global_feasibility():
    inp = build_vrp_input(
        locations=[(0, 0), (0, 1), (0, 2)],
        distance_matrix=_matrix(3, 1000),
        duration_matrix=_matrix(3, 60),
        orders=[
            OrderData(
                order_id="tagged",
                pickup_index=1,
                delivery_index=2,
                weight_grams=1_000,
                required_vehicle_tags=frozenset({"cold"}),
                latest_time_s=86_400,
            )
        ],
        vehicles=[
            VehicleData(
                courier_id="cold-van",
                vehicle_type=VehicleType.CAR_VAN,
                capacity_grams=10_000,
                depot_index=0,
                end_time_s=86_400,
                vehicle_tags=frozenset({"cold"}),
            ),
            VehicleData(
                courier_id="plain-van",
                vehicle_type=VehicleType.CAR_VAN,
                capacity_grams=10_000,
                depot_index=0,
                end_time_s=86_400,
            ),
        ],
    )

    diagnostics = diagnose_global_feasibility(inp)
    cluster_summary = summarize_cluster_feasibility(
        inp,
        diagnostics,
        clusters=[{"cluster_id": 7, "order_indices": [0], "vehicle_indices": [1]}],
    )

    assert diagnostics[0].globally_feasible is True
    assert cluster_summary[0]["globally_feasible_but_cluster_ineligible"] == 1
