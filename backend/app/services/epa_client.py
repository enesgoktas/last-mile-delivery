"""
EPA fueleconomy.gov API client.

Provides unauthenticated access to the US EPA fuel economy database:
  https://www.fueleconomy.gov/feg/ws/

All conversions:
- MPG (US) → km/L: multiply by 0.425144
- CO2: EPA provides g/mile for tailpipe emissions; converted to g/km (* 0.621371⁻¹ = / 1.60934)

Vehicle class and fuel type mappings follow best-effort heuristics based on
EPA's vehicle class and fuel type fields.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.models.enums import FuelType, VehicleType
from app.schemas.vehicle_spec import EPAMenuEntry, EPASearchResult

logger = logging.getLogger(__name__)

_EPA_BASE_URL = "https://www.fueleconomy.gov/ws/rest"
_MPG_TO_KM_PER_LITER = 0.425144
_MILES_TO_KM = 1.60934

_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=_EPA_BASE_URL,
            headers={"Accept": "application/json"},
            timeout=10.0,
        )
    return _http_client


# ---------------------------------------------------------------------------
# Fuel type mapping
# ---------------------------------------------------------------------------

_EPA_FUEL_TYPE_MAP: dict[str, FuelType] = {
    "Regular": FuelType.GASOLINE,
    "Premium": FuelType.GASOLINE,
    "Midgrade": FuelType.GASOLINE,
    "Diesel": FuelType.DIESEL,
    "Diesel B20": FuelType.DIESEL,
    "CNG": FuelType.LPG,
    "LPG": FuelType.LPG,
    "Electricity": FuelType.ELECTRIC,
    "Natural Gas": FuelType.LPG,
    "Gasoline or E85": FuelType.GASOLINE,
    "Gasoline or natural gas": FuelType.GASOLINE,
    "Gasoline or propane": FuelType.LPG,
}

_EPA_FUEL_TYPE_HYBRID_KEYWORDS = ("hybrid", "phev", "plug-in")


def _map_fuel_type(epa_fuel_type: str) -> FuelType:
    lower = epa_fuel_type.lower()
    for keyword in _EPA_FUEL_TYPE_HYBRID_KEYWORDS:
        if keyword in lower:
            return FuelType.HYBRID
    return _EPA_FUEL_TYPE_MAP.get(epa_fuel_type, FuelType.GASOLINE)


# ---------------------------------------------------------------------------
# Vehicle class mapping
# ---------------------------------------------------------------------------

_EPA_CLASS_TRUCK_KEYWORDS = (
    "pickup", "truck", "van", "cargo van", "passenger van", "minivan",
)
_EPA_CLASS_MOTORCYCLE_KEYWORDS = ("motorcycle",)


def _map_vehicle_type(epa_vehicle_class: str) -> VehicleType:
    lower = epa_vehicle_class.lower()
    for keyword in _EPA_CLASS_MOTORCYCLE_KEYWORDS:
        if keyword in lower:
            return VehicleType.MOTORCYCLE
    for keyword in _EPA_CLASS_TRUCK_KEYWORDS:
        if keyword in lower:
            return VehicleType.TRUCK
    return VehicleType.CAR_VAN


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def mpg_to_km_per_liter(mpg: float) -> float:
    return mpg * _MPG_TO_KM_PER_LITER


def co2_g_per_mile_to_g_per_km(g_per_mile: float) -> float:
    return g_per_mile / _MILES_TO_KM


# ---------------------------------------------------------------------------
# API response parsing
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_vehicle_data(data: dict[str, Any]) -> EPASearchResult | None:
    """Parse a single EPA /vehicle/{id} response into an EPASearchResult."""
    vehicle_id = str(data.get("id", ""))
    if not vehicle_id:
        return None

    make = str(data.get("make", "")).strip()
    model = str(data.get("model", "")).strip()
    year_raw = _safe_float(data.get("year"))
    if not make or not model or year_raw is None:
        return None
    year = int(year_raw)

    epa_fuel_type = str(data.get("fuelType1", "Regular"))
    fuel_type = _map_fuel_type(epa_fuel_type)

    epa_class = str(data.get("VClass", ""))
    vehicle_type = _map_vehicle_type(epa_class)

    city_mpg = _safe_float(data.get("city08"))
    highway_mpg = _safe_float(data.get("highway08"))
    combined_mpg = _safe_float(data.get("comb08"))

    if combined_mpg is None or combined_mpg <= 0:
        if city_mpg and highway_mpg:
            combined_mpg = (city_mpg + highway_mpg) / 2
        else:
            return None

    fuel_efficiency_km_per_liter = mpg_to_km_per_liter(combined_mpg)

    co2_g_per_km: float | None = None
    co2_raw = _safe_float(data.get("co2TailpipeGpm"))
    if co2_raw is not None and co2_raw > 0:
        co2_g_per_km = co2_g_per_mile_to_g_per_km(co2_raw)

    return EPASearchResult(
        epa_vehicle_id=vehicle_id,
        make=make,
        model=model,
        year=year,
        vehicle_type=vehicle_type,
        fuel_type=fuel_type,
        fuel_efficiency_km_per_liter=round(fuel_efficiency_km_per_liter, 3),
        co2_emissions_g_per_km=round(co2_g_per_km, 1) if co2_g_per_km else None,
        city_mpg=city_mpg,
        highway_mpg=highway_mpg,
        combined_mpg=combined_mpg,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_years() -> list[EPAMenuEntry]:
    """Return the list of model years available in the EPA database."""
    client = _get_client()
    response = await client.get("/vehicle/menu/year")
    response.raise_for_status()
    data = response.json()
    menu_items = data.get("menuItem", [])
    if isinstance(menu_items, dict):
        menu_items = [menu_items]
    return [EPAMenuEntry(value=str(item["value"]), text=str(item["text"])) for item in menu_items]


async def get_makes(year: int) -> list[EPAMenuEntry]:
    """Return the vehicle makes available for a given model year."""
    client = _get_client()
    response = await client.get("/vehicle/menu/make", params={"year": year})
    response.raise_for_status()
    data = response.json()
    menu_items = data.get("menuItem", [])
    if isinstance(menu_items, dict):
        menu_items = [menu_items]
    return [EPAMenuEntry(value=str(item["value"]), text=str(item["text"])) for item in menu_items]


async def get_models(year: int, make: str) -> list[EPAMenuEntry]:
    """Return the vehicle models for a given year and make."""
    client = _get_client()
    response = await client.get("/vehicle/menu/model", params={"year": year, "make": make})
    response.raise_for_status()
    data = response.json()
    menu_items = data.get("menuItem", [])
    if isinstance(menu_items, dict):
        menu_items = [menu_items]
    return [EPAMenuEntry(value=str(item["value"]), text=str(item["text"])) for item in menu_items]


async def search_vehicles(year: int, make: str, model: str) -> list[EPASearchResult]:
    """Search for EPA vehicle records matching year/make/model.

    Returns a list of parsed EPASearchResult objects -- one per trim/option
    variant the EPA has on record for that year/make/model combination.
    """
    client = _get_client()
    response = await client.get(
        "/vehicle/menu/options",
        params={"year": year, "make": make, "model": model},
    )
    response.raise_for_status()
    data = response.json()
    menu_items = data.get("menuItem", [])
    if isinstance(menu_items, dict):
        menu_items = [menu_items]

    results: list[EPASearchResult] = []
    for item in menu_items:
        vehicle_id = str(item.get("value", ""))
        if not vehicle_id:
            continue
        try:
            vehicle_data = await get_vehicle_by_id(vehicle_id)
            if vehicle_data is not None:
                results.append(vehicle_data)
        except Exception:
            logger.warning("Failed to fetch EPA vehicle %s", vehicle_id, exc_info=True)
    return results


async def get_vehicle_by_id(epa_vehicle_id: str) -> EPASearchResult | None:
    """Fetch full specs for a single EPA vehicle record by its numeric ID."""
    client = _get_client()
    response = await client.get(f"/vehicle/{epa_vehicle_id}")
    response.raise_for_status()
    data = response.json()
    return _parse_vehicle_data(data)
