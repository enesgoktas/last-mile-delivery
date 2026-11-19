import uuid
from datetime import datetime
from datetime import time as dt_time

from pydantic import BaseModel, model_validator

from app.models.enums import AvailabilityStatus, PushProvider, VehicleType
from app.schemas.vehicle_spec import VehicleSpecResponse
from app.schemas.vehicle_tag import VehicleTagResponse


class CourierCreate(BaseModel):
    user_id: uuid.UUID
    vehicle_type: VehicleType
    max_weight_kg: float
    depot_lat: float
    depot_lon: float
    depot_id: uuid.UUID | None = None
    max_volume_cm3: float | None = None
    max_packages: int | None = None
    working_hours_start: dt_time
    working_hours_end: dt_time
    zone_polygon: dict | None = None
    fcm_token: str | None = None
    push_provider: PushProvider | None = None
    push_token: str | None = None
    vehicle_spec_id: uuid.UUID | None = None
    # Phase 1: passenger capacity + EV fields
    max_passengers: int | None = None
    max_battery_range_km: int | None = None
    current_battery_pct: int | None = None
    battery_low_threshold_pct: int | None = None
    # Multi-depot vehicle support. Both fields are rejected when the tenant
    # has `features.multi_depot_vehicles_enabled = False`.
    additional_depot_ids: list[uuid.UUID] | None = None
    end_depot_id: uuid.UUID | None = None


class CourierUpdate(BaseModel):
    vehicle_type: VehicleType | None = None
    max_weight_kg: float | None = None
    depot_lat: float | None = None
    depot_lon: float | None = None
    depot_id: uuid.UUID | None = None
    max_volume_cm3: float | None = None
    max_packages: int | None = None
    working_hours_start: dt_time | None = None
    working_hours_end: dt_time | None = None
    zone_polygon: dict | None = None
    fcm_token: str | None = None
    push_provider: PushProvider | None = None
    push_token: str | None = None
    vehicle_spec_id: uuid.UUID | None = None
    # Phase 1: passenger capacity + EV fields
    max_passengers: int | None = None
    max_battery_range_km: int | None = None
    current_battery_pct: int | None = None
    battery_low_threshold_pct: int | None = None
    # Multi-depot vehicle support. `None` leaves existing values untouched;
    # an empty list explicitly clears `additional_depot_ids`. Setting either
    # field requires `features.multi_depot_vehicles_enabled`.
    additional_depot_ids: list[uuid.UUID] | None = None
    end_depot_id: uuid.UUID | None = None


class BatteryTelemetryUpdate(BaseModel):
    """Driver-app + dispatcher payload to record current battery state."""

    pct: int

    @model_validator(mode="after")
    def _validate_pct(self) -> "BatteryTelemetryUpdate":
        if not 0 <= self.pct <= 100:
            raise ValueError("pct must be between 0 and 100")
        return self


class CourierResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    vehicle_type: VehicleType
    max_weight_kg: float
    availability_status: AvailabilityStatus
    zone_polygon: dict | None
    depot_lat: float
    depot_lon: float
    depot_id: uuid.UUID | None = None
    max_volume_cm3: float | None = None
    max_packages: int | None = None
    working_hours_start: dt_time
    working_hours_end: dt_time
    fcm_token: str | None
    push_provider: PushProvider | None = None
    push_token: str | None = None
    vehicle_spec_id: uuid.UUID | None = None
    vehicle_spec: VehicleSpecResponse | None = None
    vehicle_tags: list[VehicleTagResponse] = []
    # Phase 1: passenger capacity + EV fields
    max_passengers: int | None = None
    max_battery_range_km: int | None = None
    current_battery_pct: int | None = None
    battery_low_threshold_pct: int = 20
    # Multi-depot vehicle support. `additional_depot_ids` is empty when the
    # courier has no extra reload depots (single-depot, current behavior).
    # `end_depot_id` is NULL when the shift ends at the home depot.
    additional_depot_ids: list[uuid.UUID] = []
    end_depot_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


class LocationUpdate(BaseModel):
    lat: float
    lon: float
    heading: float | None = None
    speed_kmh: float | None = None
    accuracy_m: float


class StatusUpdate(BaseModel):
    status: AvailabilityStatus


class PushTokenUpdate(BaseModel):
    fcm_token: str | None = None
    push_provider: PushProvider | None = None
    push_token: str | None = None

    @model_validator(mode="after")
    def validate_provider_payload(self) -> "PushTokenUpdate":
        if self.push_provider is None and self.push_token is None:
            return self
        if self.push_provider is None or self.push_token is None:
            raise ValueError("push_provider and push_token must be provided together")
        return self


class CourierAnalyticsResponse(BaseModel):
    courier_id: uuid.UUID
    total_assigned_orders: int
    delivered_orders: int
    failed_orders: int
    active_routes: int


class CourierLocationResponse(BaseModel):
    courier_id: uuid.UUID
    vehicle_type: VehicleType
    availability_status: AvailabilityStatus
    lat: float
    lon: float
    heading: float | None = None
    speed_kmh: float | None = None
    updated_at: datetime | None = None
    source: str
