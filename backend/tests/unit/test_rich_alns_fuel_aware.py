"""Regression tests for fuel-awareness in the rich_alns engine."""
from __future__ import annotations

import pytest

from app.algorithms.rich_vrp.domain.builders import _build_vehicle, _internal_cost_to_economic
from app.algorithms.rich_vrp.domain.models import (
    CostBreakdown,
    MatrixView,
    Problem,
    Request,
    Route,
    Vehicle,
    VehicleCost,
    VisitKind,
    Visit,
)
from app.algorithms.rich_vrp.objective.cost import compute_route_cost
from app.algorithms.vrp_solver import VehicleData, VehicleType
from app.models.enums import FuelType, OrderPriority


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vehicle(
    fuel_per_km: int = 0,
    fuel_efficiency_km_per_liter: float | None = None,
    co2_g_per_km: float | None = None,
) -> Vehicle:
    return Vehicle(
        id="v1", physical_id="v1", index=0, trip_index=0,
        start_node=0, end_node=0,
        shift_window=(0, 86400), hard_shift_end=None,
        capacity={"weight_g": 100_000, "volume_cm3": 100_000, "package_count": 100},
        tags=frozenset(),
        cost=VehicleCost(fuel_per_km=fuel_per_km),
        profile="driving",
        max_distance_m=1_000_000, max_stops=100,
        break_window=None, break_duration_s=0,
        allow_fragile=True, allow_temp_sensitive=True,
        fuel_efficiency_km_per_liter=fuel_efficiency_km_per_liter,
        co2_g_per_km=co2_g_per_km,
    )


def _make_problem_with_route(vehicle: Vehicle, distance_one_way_m: int = 50_000):
    """Return a (Problem, Route) pair with a single delivery 50km from the depot."""
    dist = [[0, distance_one_way_m], [distance_one_way_m, 0]]
    dur = [[0, 1800], [1800, 0]]
    matrix = MatrixView(dur, dist)

    request = Request(
        id="r1", pickup_node=0, delivery_node=1,
        pickup_window=(0, 86400), delivery_window=(0, 86400),
        hard_delivery_deadline=None,
        service_time_pickup_s=0, service_time_delivery_s=300,
        load={"weight_g": 1000, "volume_cm3": 1000, "package_count": 1},
        priority=OrderPriority.NORMAL,
        unserved_penalty=0, lateness_penalty_per_min=0, priority_multiplier=1.0,
        required_tags=frozenset(), allowed_vehicle_indices=None, optional=True,
    )

    problem = Problem(
        requests=[request], vehicles=[vehicle], matrix=matrix,
        locations=[(41.0, 29.0), (41.05, 29.05)],
    )

    visits = [
        Visit(request_id=None, kind=VisitKind.DEPOT_START, node=0, arrival_s=0, departure_s=0),
        Visit(request_id="r1", kind=VisitKind.DELIVERY, node=1, arrival_s=1800, departure_s=2100),
        Visit(request_id=None, kind=VisitKind.DEPOT_END, node=0, arrival_s=3900, departure_s=3900),
    ]
    route = Route(vehicle=vehicle, visits=visits, total_distance_m=distance_one_way_m * 2)

    return problem, route


# ---------------------------------------------------------------------------
# Test 1 — fuel_cost reflects vehicle fuel_per_km
# ---------------------------------------------------------------------------

def test_fuel_cost_nonzero_when_fuel_per_km_set():
    """fuel_cost in CostBreakdown is positive when vehicle.cost.fuel_per_km > 0."""
    vehicle = _make_vehicle(fuel_per_km=10)
    problem, route = _make_problem_with_route(vehicle)
    breakdown = compute_route_cost(route, problem)
    # 100 km round trip × 10 TL/km = 1000 TL
    assert breakdown.fuel_cost == 1000
    assert breakdown.total_generalized_cost >= breakdown.fuel_cost


# ---------------------------------------------------------------------------
# Test 2 — fuel-blind mode produces fuel_cost == 0
# ---------------------------------------------------------------------------

def test_fuel_cost_zero_when_fuel_per_km_zero():
    """fuel_cost is 0 when fuel_per_km=0 (SERVICE_FIRST / fuel-blind mode)."""
    vehicle = _make_vehicle(fuel_per_km=0)
    problem, route = _make_problem_with_route(vehicle)
    breakdown = compute_route_cost(route, problem)
    assert breakdown.fuel_cost == 0


# ---------------------------------------------------------------------------
# Test 3 — _build_vehicle propagates all three fuel fields
# ---------------------------------------------------------------------------

def test_build_vehicle_propagates_fuel_fields():
    """_build_vehicle must copy fuel_type, efficiency, and CO2 from VehicleData."""
    vd = VehicleData(
        courier_id="c1",
        vehicle_type=VehicleType.MOTORCYCLE,
        capacity_grams=20_000,
        fuel_cost_per_km=4,
        fuel_type=FuelType.GASOLINE,
        fuel_efficiency_km_per_liter=30.0,
        co2_g_per_km=85.0,
    )
    v = _build_vehicle(vd, 0)
    assert v.fuel_type == FuelType.GASOLINE
    assert v.fuel_efficiency_km_per_liter == 30.0
    assert v.co2_g_per_km == 85.0
    assert v.cost.fuel_per_km == 4


# ---------------------------------------------------------------------------
# Test 4 — estimated_fuel_liters and estimated_co2_g are computed correctly
# ---------------------------------------------------------------------------

def test_fuel_liters_and_co2_reporting():
    """For a 100km route, fuel_liters = dist/eff and co2_g = dist * co2_rate."""
    # 50km one-way → 100km round trip
    vehicle = _make_vehicle(
        fuel_per_km=10,
        fuel_efficiency_km_per_liter=10.0,  # → 100km / 10 = 10 liters
        co2_g_per_km=120.0,                  # → 100km × 120 = 12000 g
    )
    problem, route = _make_problem_with_route(vehicle, distance_one_way_m=50_000)
    breakdown = compute_route_cost(route, problem)

    assert breakdown.estimated_fuel_liters == pytest.approx(10.0, abs=0.01)
    assert breakdown.estimated_co2_g == pytest.approx(12000.0, abs=1.0)

    # Verify the values survive the economic conversion
    economic = _internal_cost_to_economic(breakdown)
    assert economic.estimated_fuel_liters == pytest.approx(10.0, abs=0.01)
    assert economic.estimated_co2_g == pytest.approx(12000.0, abs=1.0)


def test_fuel_reporting_none_when_no_efficiency_spec():
    """estimated_fuel_liters and estimated_co2_g are None when spec is absent."""
    vehicle = _make_vehicle(fuel_per_km=10)  # no efficiency/CO2 spec
    problem, route = _make_problem_with_route(vehicle)
    breakdown = compute_route_cost(route, problem)
    assert breakdown.estimated_fuel_liters is None
    assert breakdown.estimated_co2_g is None
