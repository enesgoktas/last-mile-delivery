"""Pydantic schemas for the recurring-order API."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, Field, model_validator

from app.models.enums import OrderPriority, OrderType
from app.models.recurring_order import RecurrenceCadence
from app.schemas.vehicle_tag import VehicleTagResponse


class RecurringOrderTemplateBase(BaseModel):
    """Shared fields between create / update / response schemas."""
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    is_active: bool = True

    cadence: RecurrenceCadence
    interval_days: int = Field(1, ge=1, le=365)
    day_of_week_mask: int = Field(0, ge=0, le=0b1111111)
    day_of_month: int | None = Field(None, ge=1, le=31)

    start_date: date
    end_date: date | None = None
    max_occurrences: int | None = Field(None, ge=1, le=10_000)

    delivery_window_start: time
    delivery_window_end: time
    pickup_window_start: time | None = None
    pickup_window_end: time | None = None

    lead_time_days: int = Field(7, ge=1, le=30)

    order_type: OrderType
    priority: OrderPriority = OrderPriority.NORMAL

    pickup_address: str = Field(..., max_length=500)
    pickup_lat: float
    pickup_lon: float
    delivery_address: str = Field(..., max_length=500)
    delivery_lat: float
    delivery_lon: float
    depot_id: uuid.UUID | None = None

    # NULL/0 for RIDE templates; required > 0 for non-RIDE.
    weight_kg: float | None = None
    is_fragile: bool = False
    is_temp_sensitive: bool = False
    required_vehicle_tag_ids: list[uuid.UUID] = []
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    volume_cm3: float | None = None

    customer_email: str = Field(..., max_length=255)
    customer_phone: str | None = Field(None, max_length=50)
    external_ref_prefix: str | None = Field(None, max_length=120)
    custom_fields: dict[str, object] | None = None
    notes: str | None = None

    # ── Ride-specific fields (only meaningful when order_type == RIDE) ──
    # Mirror OrderCreate's ride fields so a recurring RIDE template can be
    # materialised straight onto a new RIDE Order.
    passenger_count: int | None = Field(None, ge=1, le=100)
    passenger_name: str | None = Field(None, max_length=255)
    passenger_phone: str | None = Field(None, max_length=50)
    flight_info: dict[str, object] | None = None
    special_requirements: str | None = None
    pickup_buffer_minutes_override: int | None = Field(None, ge=0)
    allow_split: bool = False
    passengers_per_vehicle: int | None = Field(None, ge=1, le=20)

    @model_validator(mode="after")
    def _validate_windows(self):
        if self.delivery_window_end <= self.delivery_window_start:
            raise ValueError("delivery_window_end must be after delivery_window_start")
        pw_pair = (self.pickup_window_start, self.pickup_window_end)
        if (pw_pair[0] is None) != (pw_pair[1] is None):
            raise ValueError(
                "pickup_window_start and pickup_window_end must both be set or omitted"
            )
        if (
            pw_pair[0] is not None
            and pw_pair[1] is not None
            and pw_pair[1] <= pw_pair[0]
        ):
            raise ValueError("pickup_window_end must be after pickup_window_start")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.cadence == RecurrenceCadence.CUSTOM and self.interval_days < 1:
            raise ValueError("CUSTOM cadence requires interval_days >= 1")
        if self.order_type == OrderType.PICKUP_AND_DELIVERY and (
            self.pickup_window_start is None or self.pickup_window_end is None
        ):
            raise ValueError(
                "PICKUP_AND_DELIVERY templates require pickup_window_start and pickup_window_end"
            )
        return self

    @model_validator(mode="after")
    def _validate_ride_vs_delivery_shape(self):
        if self.order_type == OrderType.RIDE:
            if self.passenger_count is None or self.passenger_count < 1:
                raise ValueError("RIDE templates require passenger_count >= 1")
            # Pickup window is the passenger ready time for a ride.
            if self.pickup_window_start is None or self.pickup_window_end is None:
                raise ValueError(
                    "RIDE templates require pickup_window_start and pickup_window_end "
                    "(the passenger ready time window)"
                )
        else:
            # Package templates must have positive weight and no passenger fields.
            if self.weight_kg is None or self.weight_kg <= 0:
                raise ValueError("weight_kg must be > 0 for non-RIDE templates")
            passenger_fields = {
                "passenger_count": self.passenger_count,
                "passenger_name": self.passenger_name,
                "passenger_phone": self.passenger_phone,
                "flight_info": self.flight_info,
                "special_requirements": self.special_requirements,
                "pickup_buffer_minutes_override": self.pickup_buffer_minutes_override,
                "passengers_per_vehicle": self.passengers_per_vehicle,
            }
            stray = [k for k, v in passenger_fields.items() if v is not None]
            if stray:
                raise ValueError(
                    "Passenger-only fields are not valid on non-RIDE templates: "
                    + ", ".join(sorted(stray))
                )
            if self.allow_split:
                raise ValueError("allow_split is only valid on RIDE templates")
        return self


class RecurringOrderTemplateCreate(RecurringOrderTemplateBase):
    """Create payload; input-only fields that are not ORM columns belong here."""

    required_vehicle_tag_ids: list[uuid.UUID] = []


class RecurringOrderTemplateUpdate(BaseModel):
    """Partial update — every field optional. Same validators as Base apply
    only if both halves of a pair are provided."""
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    is_active: bool | None = None

    cadence: RecurrenceCadence | None = None
    interval_days: int | None = Field(None, ge=1, le=365)
    day_of_week_mask: int | None = Field(None, ge=0, le=0b1111111)
    day_of_month: int | None = Field(None, ge=1, le=31)

    start_date: date | None = None
    end_date: date | None = None
    max_occurrences: int | None = Field(None, ge=1, le=10_000)

    delivery_window_start: time | None = None
    delivery_window_end: time | None = None
    pickup_window_start: time | None = None
    pickup_window_end: time | None = None

    lead_time_days: int | None = Field(None, ge=1, le=30)

    order_type: OrderType | None = None
    priority: OrderPriority | None = None

    pickup_address: str | None = Field(None, max_length=500)
    pickup_lat: float | None = None
    pickup_lon: float | None = None
    delivery_address: str | None = Field(None, max_length=500)
    delivery_lat: float | None = None
    delivery_lon: float | None = None
    depot_id: uuid.UUID | None = None

    weight_kg: float | None = None
    is_fragile: bool | None = None
    is_temp_sensitive: bool | None = None
    required_vehicle_tag_ids: list[uuid.UUID] | None = None
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    volume_cm3: float | None = None

    customer_email: str | None = Field(None, max_length=255)
    customer_phone: str | None = Field(None, max_length=50)
    external_ref_prefix: str | None = Field(None, max_length=120)
    custom_fields: dict[str, object] | None = None
    notes: str | None = None

    # ── Ride-specific fields (only meaningful when order_type == RIDE) ──
    passenger_count: int | None = Field(None, ge=1, le=100)
    passenger_name: str | None = Field(None, max_length=255)
    passenger_phone: str | None = Field(None, max_length=50)
    flight_info: dict[str, object] | None = None
    special_requirements: str | None = None
    pickup_buffer_minutes_override: int | None = Field(None, ge=0)
    allow_split: bool | None = None
    passengers_per_vehicle: int | None = Field(None, ge=1, le=20)


class RecurringOrderTemplateResponse(RecurringOrderTemplateBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    occurrences_generated: int
    last_generated_for: date | None = None
    next_generation_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    required_vehicle_tags: list[VehicleTagResponse] = []

    model_config = {"from_attributes": True}


class RecurringOrderRunResponse(BaseModel):
    """Returned by `POST /recurring-orders/run` and `POST /{id}/run`."""
    templates_evaluated: int
    occurrences_created: int
    occurrences_skipped_duplicate: int
    occurrences_skipped_capped: int


class RecurringOrderPreviewResponse(BaseModel):
    """Returned by `GET /recurring-orders/{id}/preview`."""
    template_id: uuid.UUID
    occurrence_dates: list[date]


class RecurringOrderOccurrenceResponse(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    order_id: uuid.UUID | None
    occurrence_date: date
    generated_at: datetime

    model_config = {"from_attributes": True}
