"""Unit tests for vehicle spec fuel cost derivation and EPA client parsing."""
import pytest

from app.models.enums import FuelType, VehicleType
from app.schemas.vehicle_spec import EPASearchResult
from app.services import epa_client as epa
from app.services.vehicle_spec_service import (
    derive_default_vehicle_type_fuel_cost_per_km,
    derive_fuel_cost_per_km,
    get_default_vehicle_type_fuel_profile,
)


# ---------------------------------------------------------------------------
# derive_fuel_cost_per_km
# ---------------------------------------------------------------------------

class TestDeriveFuelCostPerKm:
    def test_basic_gasoline_vehicle(self):
        # 10 km/L, price 30 units/L → 3 units/km
        result = derive_fuel_cost_per_km(
            fuel_efficiency_km_per_liter=10.0,
            fuel_price_per_liter=30.0,
        )
        assert result == 3

    def test_high_efficiency_motorcycle(self):
        # 40 km/L, price 35 units/L → ~1 unit/km
        result = derive_fuel_cost_per_km(
            fuel_efficiency_km_per_liter=40.0,
            fuel_price_per_liter=35.0,
        )
        assert result == 1

    def test_low_efficiency_truck(self):
        # 5 km/L, price 38 units/L → 8 units/km (rounded)
        result = derive_fuel_cost_per_km(
            fuel_efficiency_km_per_liter=5.0,
            fuel_price_per_liter=38.0,
        )
        assert result == 8

    def test_rounds_correctly(self):
        # 7.0 km/L, price 35.0 → 35/7 = 5 exact
        result = derive_fuel_cost_per_km(
            fuel_efficiency_km_per_liter=7.0,
            fuel_price_per_liter=35.0,
        )
        assert result == 5

    def test_zero_efficiency_returns_zero(self):
        result = derive_fuel_cost_per_km(
            fuel_efficiency_km_per_liter=0.0,
            fuel_price_per_liter=35.0,
        )
        assert result == 0

    def test_negative_efficiency_returns_zero(self):
        result = derive_fuel_cost_per_km(
            fuel_efficiency_km_per_liter=-5.0,
            fuel_price_per_liter=35.0,
        )
        assert result == 0

    def test_spec_derived_is_more_accurate_than_flat_preset(self):
        # Flat preset for CAR_VAN in BALANCED = 10
        # A Toyota Corolla Hybrid at 22 km/L, fuel price 30 → 30/22 ≈ 1
        # A Ford Transit at 11.5 km/L, fuel price 38 → 38/11.5 ≈ 3
        corolla_cost = derive_fuel_cost_per_km(22.0, 30.0)
        transit_cost = derive_fuel_cost_per_km(11.5, 38.0)
        assert corolla_cost < transit_cost  # more efficient = lower cost


class TestVehicleTypeFuelDefaults:
    def test_default_profiles_are_delivery_fleet_averages(self):
        motorcycle = get_default_vehicle_type_fuel_profile(VehicleType.MOTORCYCLE)
        car_van = get_default_vehicle_type_fuel_profile(VehicleType.CAR_VAN)
        truck = get_default_vehicle_type_fuel_profile(VehicleType.TRUCK)

        assert motorcycle is not None
        assert motorcycle.fuel_type == FuelType.GASOLINE
        assert motorcycle.fuel_efficiency_km_per_liter == 36.0
        assert car_van is not None
        assert car_van.fuel_type == FuelType.DIESEL
        assert car_van.fuel_efficiency_km_per_liter == 16.0
        assert truck is not None
        assert truck.fuel_type == FuelType.DIESEL
        assert truck.fuel_efficiency_km_per_liter == 5.0

    def test_default_fuel_costs_use_configured_fuel_prices(self):
        fuel_prices = {
            FuelType.GASOLINE: 35.0,
            FuelType.DIESEL: 38.0,
        }

        assert derive_default_vehicle_type_fuel_cost_per_km(
            VehicleType.MOTORCYCLE,
            fuel_prices,
        ) == 1
        assert derive_default_vehicle_type_fuel_cost_per_km(
            VehicleType.CAR_VAN,
            fuel_prices,
        ) == 2
        assert derive_default_vehicle_type_fuel_cost_per_km(
            VehicleType.TRUCK,
            fuel_prices,
        ) == 8

    def test_unknown_vehicle_type_returns_no_default_cost(self):
        assert get_default_vehicle_type_fuel_profile("BICYCLE") is None
        assert derive_default_vehicle_type_fuel_cost_per_km("BICYCLE", {}) is None


# ---------------------------------------------------------------------------
# EPA client unit conversions
# ---------------------------------------------------------------------------

class TestEPAConversions:
    def test_mpg_to_km_per_liter(self):
        # 25 MPG should be ~10.629 km/L
        result = epa.mpg_to_km_per_liter(25.0)
        assert abs(result - 10.6286) < 0.01

    def test_city_highway_mpg_to_km_per_liter(self):
        result = epa.mpg_to_km_per_liter(40.0)
        assert abs(result - 17.0058) < 0.01

    def test_co2_conversion(self):
        # 300 g/mile should be ~186.4 g/km
        result = epa.co2_g_per_mile_to_g_per_km(300.0)
        assert abs(result - 186.4) < 0.5

    def test_fuel_type_gasoline(self):
        assert epa._map_fuel_type("Regular") == FuelType.GASOLINE
        assert epa._map_fuel_type("Premium") == FuelType.GASOLINE

    def test_fuel_type_diesel(self):
        assert epa._map_fuel_type("Diesel") == FuelType.DIESEL
        assert epa._map_fuel_type("Diesel B20") == FuelType.DIESEL

    def test_fuel_type_electric(self):
        assert epa._map_fuel_type("Electricity") == FuelType.ELECTRIC

    def test_fuel_type_hybrid_keyword(self):
        assert epa._map_fuel_type("Gasoline Hybrid") == FuelType.HYBRID
        assert epa._map_fuel_type("Plug-In Hybrid") == FuelType.HYBRID
        assert epa._map_fuel_type("PHEV") == FuelType.HYBRID

    def test_vehicle_type_motorcycle(self):
        assert epa._map_vehicle_type("Motorcycle") == VehicleType.MOTORCYCLE

    def test_vehicle_type_van(self):
        assert epa._map_vehicle_type("Cargo Van") == VehicleType.TRUCK
        assert epa._map_vehicle_type("Passenger Van") == VehicleType.TRUCK
        assert epa._map_vehicle_type("Minivan") == VehicleType.TRUCK

    def test_vehicle_type_car(self):
        assert epa._map_vehicle_type("Compact Cars") == VehicleType.CAR_VAN
        assert epa._map_vehicle_type("Midsize Sedan") == VehicleType.CAR_VAN

    def test_vehicle_type_pickup_truck(self):
        assert epa._map_vehicle_type("Standard Pickup Trucks") == VehicleType.TRUCK


class TestEPAParseVehicleData:
    def _valid_epa_record(self, **overrides) -> dict:
        base = {
            "id": "12345",
            "make": "Toyota",
            "model": "Corolla",
            "year": 2023,
            "fuelType1": "Regular",
            "VClass": "Small Cars",
            "city08": 30.0,
            "highway08": 40.0,
            "comb08": 35.0,
            "co2TailpipeGpm": 254.0,
        }
        base.update(overrides)
        return base

    def test_parse_valid_record(self):
        record = self._valid_epa_record()
        result = epa._parse_vehicle_data(record)
        assert result is not None
        assert result.epa_vehicle_id == "12345"
        assert result.make == "Toyota"
        assert result.model == "Corolla"
        assert result.year == 2023
        assert result.fuel_type == FuelType.GASOLINE
        assert result.vehicle_type == VehicleType.CAR_VAN
        # 35 MPG * 0.425144 ≈ 14.88
        assert abs(result.fuel_efficiency_km_per_liter - 14.88) < 0.01
        assert result.combined_mpg == 35.0

    def test_parse_returns_none_for_missing_id(self):
        record = self._valid_epa_record(id="")
        result = epa._parse_vehicle_data(record)
        assert result is None

    def test_parse_returns_none_for_zero_mpg(self):
        record = self._valid_epa_record(comb08=0, city08=0, highway08=0)
        result = epa._parse_vehicle_data(record)
        assert result is None

    def test_parse_falls_back_to_city_highway_average(self):
        record = self._valid_epa_record(comb08=None, city08=30.0, highway08=40.0)
        result = epa._parse_vehicle_data(record)
        assert result is not None
        assert abs(result.combined_mpg - 35.0) < 0.01

    def test_parse_co2_none_when_not_provided(self):
        record = self._valid_epa_record()
        del record["co2TailpipeGpm"]
        result = epa._parse_vehicle_data(record)
        assert result is not None
        assert result.co2_emissions_g_per_km is None

    def test_parse_co2_converted_from_g_per_mile(self):
        record = self._valid_epa_record(co2TailpipeGpm=322.0)
        result = epa._parse_vehicle_data(record)
        assert result is not None
        # 322 g/mile / 1.60934 ≈ 200.1 g/km
        assert result.co2_emissions_g_per_km is not None
        assert abs(result.co2_emissions_g_per_km - 200.1) < 1.0


# ---------------------------------------------------------------------------
# VehicleData fuel spec fields
# ---------------------------------------------------------------------------

class TestVehicleDataFuelSpecFields:
    def test_vehicle_data_accepts_spec_fields(self):
        from app.algorithms.vrp_solver import VehicleData

        v = VehicleData(
            courier_id="c1",
            vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=10_000,
            fuel_cost_per_km=3,
            fuel_efficiency_km_per_liter=22.0,
            co2_g_per_km=96.0,
        )
        assert v.fuel_efficiency_km_per_liter == 22.0
        assert v.co2_g_per_km == 96.0

    def test_vehicle_data_defaults_to_none(self):
        from app.algorithms.vrp_solver import VehicleData

        v = VehicleData(
            courier_id="c1",
            vehicle_type=VehicleType.CAR_VAN,
            capacity_grams=10_000,
        )
        assert v.fuel_efficiency_km_per_liter is None
        assert v.co2_g_per_km is None


# ---------------------------------------------------------------------------
# EconomicCostBreakdown fuel estimates
# ---------------------------------------------------------------------------

class TestEconomicCostBreakdownFuelEstimates:
    def test_breakdown_includes_fuel_estimates(self):
        from app.algorithms.vrp_solver import EconomicCostBreakdown

        breakdown = EconomicCostBreakdown(
            total_generalized_cost=100,
            fuel_cost=10,
            estimated_fuel_liters=2.5,
            estimated_co2_g=240.0,
        )
        assert breakdown.estimated_fuel_liters == 2.5
        assert breakdown.estimated_co2_g == 240.0

    def test_breakdown_defaults_none(self):
        from app.algorithms.vrp_solver import EconomicCostBreakdown

        breakdown = EconomicCostBreakdown(total_generalized_cost=50)
        assert breakdown.estimated_fuel_liters is None
        assert breakdown.estimated_co2_g is None
