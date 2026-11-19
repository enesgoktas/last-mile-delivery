"""Layer 2a — verify the counterfactual aggregator math without a DB.

The greedy itself is well-tested elsewhere via the human_dispatcher
baseline test suite; here we exercise the pieces this service owns:

  * `_aggregate_solution` collapses a `VRPSolution` to the fuel/CO₂/cost
    totals that get persisted on the counterfactual row.
  * `_strip_day_suffix` recovers the physical courier id used by both
    the range-dispatch day-vehicle expansion and the daily flow.

Source-level guard tests pin the dispatch hooks so neither the daily
nor the range path can regress to "no counterfactual ever runs."
"""
from __future__ import annotations

import inspect
import uuid

from app.algorithms.vrp_solver import PlannedRoute, PlannedStop, SolverStatus, VRPSolution
from app.models.enums import FuelType
from app.services.counterfactual_service import (
    _aggregate_solution,
    _strip_day_suffix,
)


def _delivery(order_id: str = "o-1") -> PlannedStop:
    return PlannedStop(
        order_id=order_id,
        stop_type="DELIVERY",
        location_index=1,
        lat=41.015,
        lon=28.979,
        arrival_time_s=10 * 3600,
        departure_time_s=10 * 3600 + 300,
    )


def _route(courier_id: str, *, distance_m: float, deliveries: int = 1) -> PlannedRoute:
    return PlannedRoute(
        courier_id=courier_id,
        stops=[_delivery(order_id=f"o-{i}") for i in range(deliveries)],
        total_distance_m=distance_m,
        total_duration_s=int(distance_m / 10),  # synthetic
    )


def _solution(routes: list[PlannedRoute]) -> VRPSolution:
    return VRPSolution(
        routes=routes,
        unassigned_orders=[],
        total_distance_m=sum(r.total_distance_m for r in routes),
        total_duration_s=sum(r.total_duration_s for r in routes),
        solver_status=SolverStatus.FEASIBLE,
    )


def test_strip_day_suffix_recovers_physical_courier_id():
    base = "11111111-1111-1111-1111-111111111111"
    assert _strip_day_suffix(f"{base}__day3") == base
    assert _strip_day_suffix(base) == base


def test_aggregate_solution_sums_distance_and_fuel_cost_per_courier():
    courier_a = uuid.uuid4()
    courier_b = uuid.uuid4()
    specs = {
        courier_a: {
            "fuel_type": FuelType.GASOLINE,
            "km_per_liter": 10.0,
            "co2_g_per_km": None,
            "province_code": "34",
        },
        courier_b: {
            "fuel_type": FuelType.DIESEL,
            "km_per_liter": 15.0,
            "co2_g_per_km": None,
            "province_code": "34",
        },
    }
    routes = [
        _route(str(courier_a), distance_m=20_000, deliveries=3),
        _route(str(courier_b), distance_m=30_000, deliveries=2),
    ]
    totals = _aggregate_solution(
        solution=_solution(routes),
        courier_specs=specs,
        fuel_price_try=44.0,
    )
    # Courier A: 20 km / 10 km/L = 2 L
    # Courier B: 30 km / 15 km/L = 2 L → 4 L total
    assert totals["fuel_liters"] == 4.0
    # Total liters × 44 TRY/L = 176 TRY.
    assert totals["fuel_cost_try"] == 176.0
    # Distance sums: 50 km total.
    assert totals["distance_m"] == 50_000
    # 3 + 2 deliveries assigned.
    assert totals["assigned_count"] == 5


def test_aggregate_solution_with_electric_courier_skips_fuel_cost():
    courier_ev = uuid.uuid4()
    specs = {
        courier_ev: {
            "fuel_type": FuelType.ELECTRIC,
            "km_per_liter": 0.0,
            "co2_g_per_km": None,
            "province_code": "34",
        }
    }
    totals = _aggregate_solution(
        solution=_solution([_route(str(courier_ev), distance_m=80_000)]),
        courier_specs=specs,
        fuel_price_try=44.0,
    )
    # EV burns no fuel — no fuel volume, no fuel cost.
    assert totals["fuel_liters"] is None
    assert totals["fuel_cost_try"] is None
    # Distance still aggregates.
    assert totals["distance_m"] == 80_000


def test_aggregate_solution_handles_missing_fuel_price_gracefully():
    courier = uuid.uuid4()
    specs = {
        courier: {
            "fuel_type": FuelType.GASOLINE,
            "km_per_liter": 10.0,
            "co2_g_per_km": None,
            "province_code": "34",
        }
    }
    totals = _aggregate_solution(
        solution=_solution([_route(str(courier), distance_m=10_000)]),
        courier_specs=specs,
        fuel_price_try=None,
    )
    # We still know liters burned even without a price — only cost is None.
    assert totals["fuel_liters"] == 1.0
    assert totals["fuel_cost_try"] is None


def test_dispatch_range_calls_counterfactual_at_persist_time():
    """Pin the integration — dispatch_range_service must invoke the
    counterfactual service before commit so a future refactor can't
    silently regress the savings KPI."""
    from app.services import dispatch_range_service

    src = inspect.getsource(dispatch_range_service.dispatch_range)
    assert "run_and_persist_counterfactual" in src
    assert "promote_solver_metrics" in src


def test_dispatch_batch_calls_counterfactual_at_persist_time():
    from app.services import dispatch_service

    src = inspect.getsource(dispatch_service)
    assert "run_and_persist_counterfactual" in src
    assert "promote_solver_metrics" in src
