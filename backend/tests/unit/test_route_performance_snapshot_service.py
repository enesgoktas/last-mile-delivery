"""Layer 1 — verify the per-route fuel + CO₂ + cost math.

The `_compute_fuel_and_cost` helper is a pure function: no DB, no async.
We assert the headliner KPIs come out right for the three vehicle types
that matter at pilot time (diesel, gasoline, electric).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.enums import FuelType
from app.services.route_performance_snapshot_service import _compute_fuel_and_cost


def _spec(
    *,
    fuel_type: FuelType,
    km_per_liter: float = 12.0,
    co2_g_per_km: float | None = None,
):
    return SimpleNamespace(
        fuel_type=fuel_type,
        fuel_efficiency_km_per_liter=km_per_liter,
        co2_emissions_g_per_km=co2_g_per_km,
    )


def test_diesel_50km_route_burns_expected_liters_and_co2():
    result = _compute_fuel_and_cost(
        actual_distance_m=50_000,
        operational_date=None,
        vehicle_spec=_spec(fuel_type=FuelType.DIESEL, km_per_liter=10.0),
        depot=None,
    )
    # 50 km / 10 km/L = 5 L; diesel CO2 factor 2.68 → 13.4 kg
    assert result["fuel_liters"] == pytest.approx(5.0)
    assert result["co2_kg"] == pytest.approx(5.0 * 2.68, rel=1e-3)


def test_gasoline_route_uses_gasoline_co2_factor_when_spec_lacks_per_km():
    result = _compute_fuel_and_cost(
        actual_distance_m=30_000,
        operational_date=None,
        vehicle_spec=_spec(fuel_type=FuelType.GASOLINE, km_per_liter=12.0),
        depot=None,
    )
    # 30 / 12 = 2.5 L; gasoline factor 2.31 → 5.775 kg
    assert result["fuel_liters"] == pytest.approx(2.5)
    assert result["co2_kg"] == pytest.approx(2.5 * 2.31, rel=1e-3)


def test_electric_vehicle_reports_zero_co2_and_no_fuel_liters():
    result = _compute_fuel_and_cost(
        actual_distance_m=80_000,
        operational_date=None,
        vehicle_spec=_spec(fuel_type=FuelType.ELECTRIC, km_per_liter=0.0),
        depot=None,
    )
    # No combustion fuel, no CO2 from fuel — co2 stays None unless the
    # spec carries an explicit per-km value (electricity emissions
    # vary by grid; we don't guess).
    assert result.get("fuel_liters") is None
    assert result.get("co2_kg") is None


def test_per_km_co2_overrides_fuel_volume_calc_when_set():
    """If VehicleSpec.co2_emissions_g_per_km is set, that wins over the
    fuel-volume × factor estimate — it's the manufacturer's number and
    more accurate than our default."""
    result = _compute_fuel_and_cost(
        actual_distance_m=100_000,
        operational_date=None,
        vehicle_spec=_spec(
            fuel_type=FuelType.GASOLINE, km_per_liter=10.0, co2_g_per_km=140.0
        ),
        depot=None,
    )
    # 100 km × 140 g/km = 14,000 g = 14 kg, regardless of what the
    # default factor (2.31 × 10 L = 23.1 kg) would say.
    assert result["co2_kg"] == pytest.approx(14.0, rel=1e-3)


def test_zero_distance_returns_empty_dict():
    result = _compute_fuel_and_cost(
        actual_distance_m=0,
        operational_date=None,
        vehicle_spec=_spec(fuel_type=FuelType.DIESEL),
        depot=None,
    )
    assert result == {}


def test_missing_vehicle_spec_returns_empty_dict():
    result = _compute_fuel_and_cost(
        actual_distance_m=10_000,
        operational_date=None,
        vehicle_spec=None,
        depot=None,
    )
    assert result == {}
