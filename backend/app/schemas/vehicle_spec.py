import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import FuelType, VehicleType


class VehicleSpecCreate(BaseModel):
    make: str = Field(..., min_length=1, max_length=100)
    model: str = Field(..., min_length=1, max_length=100)
    year: int | None = Field(default=None, ge=1900, le=2100)
    vehicle_type: VehicleType
    fuel_type: FuelType
    fuel_efficiency_km_per_liter: float = Field(..., gt=0)
    co2_emissions_g_per_km: float | None = Field(default=None, ge=0)
    height_cm: int | None = Field(default=None, gt=0)
    width_cm: int | None = Field(default=None, gt=0)
    length_cm: int | None = Field(default=None, gt=0)
    gross_weight_kg: int | None = Field(default=None, gt=0)
    axle_count: int | None = Field(default=None, gt=0)
    emission_class: str | None = Field(default=None, max_length=20)
    hazmat_capable: bool = False
    osrm_profile: str = Field(default="driving", min_length=1, max_length=32)
    # EV fields. Both required for `gather_candidate_chargers_for_couriers`
    # to find compatible stations and for the charging post-processor to
    # estimate charge time. NULL is fine for ICE/hybrid specs.
    supported_connector_types: list[str] | None = Field(default=None)
    battery_kwh: float | None = Field(default=None, gt=0)


class VehicleSpecUpdate(BaseModel):
    make: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    year: int | None = Field(default=None, ge=1900, le=2100)
    vehicle_type: VehicleType | None = None
    fuel_type: FuelType | None = None
    fuel_efficiency_km_per_liter: float | None = Field(default=None, gt=0)
    co2_emissions_g_per_km: float | None = Field(default=None, ge=0)
    height_cm: int | None = Field(default=None, gt=0)
    width_cm: int | None = Field(default=None, gt=0)
    length_cm: int | None = Field(default=None, gt=0)
    gross_weight_kg: int | None = Field(default=None, gt=0)
    axle_count: int | None = Field(default=None, gt=0)
    emission_class: str | None = Field(default=None, max_length=20)
    hazmat_capable: bool | None = None
    osrm_profile: str | None = Field(default=None, min_length=1, max_length=32)
    supported_connector_types: list[str] | None = Field(default=None)
    battery_kwh: float | None = Field(default=None, gt=0)


class VehicleSpecResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    make: str
    model: str
    year: int | None
    vehicle_type: VehicleType
    fuel_type: FuelType
    fuel_efficiency_km_per_liter: float
    co2_emissions_g_per_km: float | None
    epa_vehicle_id: str | None
    source: str
    height_cm: int | None
    width_cm: int | None
    length_cm: int | None
    gross_weight_kg: int | None
    axle_count: int | None
    emission_class: str | None
    hazmat_capable: bool
    osrm_profile: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EPAMenuEntry(BaseModel):
    """A single entry from an EPA menu endpoint (year, make, or model list)."""
    value: str
    text: str


class EPASearchResult(BaseModel):
    """Structured result from the EPA vehicle lookup, ready for import."""
    epa_vehicle_id: str
    make: str
    model: str
    year: int
    vehicle_type: VehicleType
    fuel_type: FuelType
    fuel_efficiency_km_per_liter: float
    co2_emissions_g_per_km: float | None
    city_mpg: float | None
    highway_mpg: float | None
    combined_mpg: float | None


class EPAImportRequest(BaseModel):
    epa_vehicle_id: str
