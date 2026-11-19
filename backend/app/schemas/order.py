import uuid
from datetime import date, datetime

from pydantic import BaseModel, model_validator

from app.models.enums import (
    FailedOrderCustomerResponseStatus,
    OrderPriority,
    OrderStatus,
    OrderType,
)
from app.schemas.vehicle_tag import VehicleTagResponse


class OrderCreate(BaseModel):
    type: OrderType
    priority: OrderPriority = OrderPriority.NORMAL

    # Addresses
    pickup_address: str
    pickup_lat: float
    pickup_lon: float
    delivery_address: str
    delivery_lat: float
    delivery_lon: float

    # Depot
    depot_id: uuid.UUID | None = None

    # Package — for RIDE orders these default to 0; for everything else
    # `weight_kg > 0` is required (enforced below).
    weight_kg: float = 0.0
    is_fragile: bool = False
    is_temp_sensitive: bool = False
    required_vehicle_tag_ids: list[uuid.UUID] = []
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    volume_cm3: float | None = None

    # Time windows
    earliest_pickup: datetime | None = None
    latest_pickup: datetime | None = None
    earliest_delivery: datetime
    latest_delivery: datetime

    # Customer
    customer_email: str
    customer_phone: str | None = None

    # Integration
    external_ref: str | None = None
    custom_fields: dict[str, object] | None = None

    # Notes
    notes: str | None = None

    # ── Phase 1: passenger / ride-specific fields ──
    # Validation below enforces the type-conditional shape: RIDE requires
    # passenger_count, deliveries require weight_kg, etc. The feature-flag
    # gates (rides_enabled, ride_airport_features_enabled) live in the
    # API layer in `app/api/orders.py` since the schema doesn't know the
    # tenant's policy.
    passenger_count: int | None = None
    passenger_name: str | None = None
    passenger_phone: str | None = None
    flight_info: dict[str, object] | None = None
    special_requirements: str | None = None
    pickup_buffer_minutes_override: int | None = None
    service_time_pickup_seconds_override: int | None = None
    # When True on a large RIDE (passenger_count > per-vehicle ceiling),
    # the order_service splits it into N sibling orders, each carrying
    # a slice of the passengers. All siblings share `ride_group_id`.
    # Solver routes them independently → 3 sedans for a 10-pax pickup,
    # for example.
    allow_split: bool = False
    # Per-vehicle passenger ceiling for splits. None falls back to
    # `ride_settings.default_passengers_per_vehicle` (default 4).
    passengers_per_vehicle: int | None = None

    @model_validator(mode="after")
    def _validate_pdp_windows(self):
        if self.type == OrderType.PICKUP_AND_DELIVERY:
            missing = [
                field
                for field, value in [
                    ("earliest_pickup", self.earliest_pickup),
                    ("latest_pickup", self.latest_pickup),
                ]
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"PICKUP_AND_DELIVERY orders require {' and '.join(missing)}"
                )
            if (
                self.earliest_pickup is not None
                and self.latest_pickup is not None
                and self.earliest_pickup >= self.latest_pickup
            ):
                raise ValueError("earliest_pickup must be before latest_pickup")
        return self

    @model_validator(mode="after")
    def _validate_ride_vs_delivery_shape(self):
        if self.type == OrderType.RIDE:
            # Passenger orders must have at least one passenger and a pickup window.
            if self.passenger_count is None or self.passenger_count < 1:
                raise ValueError("RIDE orders require passenger_count >= 1")
            if self.earliest_pickup is None or self.latest_pickup is None:
                raise ValueError(
                    "RIDE orders require earliest_pickup and latest_pickup "
                    "(the passenger ready time window)"
                )
            if (
                self.pickup_buffer_minutes_override is not None
                and self.pickup_buffer_minutes_override < 0
            ):
                raise ValueError("pickup_buffer_minutes_override must be >= 0")
        else:
            # Package orders must have positive weight and no passenger fields.
            if self.weight_kg <= 0:
                raise ValueError("weight_kg must be > 0 for non-RIDE orders")
            passenger_fields = {
                "passenger_count": self.passenger_count,
                "passenger_name": self.passenger_name,
                "passenger_phone": self.passenger_phone,
                "flight_info": self.flight_info,
                "pickup_buffer_minutes_override": self.pickup_buffer_minutes_override,
                "service_time_pickup_seconds_override": (
                    self.service_time_pickup_seconds_override
                ),
                "passengers_per_vehicle": self.passengers_per_vehicle,
            }
            stray = [k for k, v in passenger_fields.items() if v is not None]
            if stray:
                raise ValueError(
                    "Passenger-only fields are not valid on non-RIDE orders: "
                    + ", ".join(sorted(stray))
                )
            if self.allow_split:
                raise ValueError("allow_split is only valid on RIDE orders")
        return self


class OrderUpdate(BaseModel):
    priority: OrderPriority | None = None
    pickup_address: str | None = None
    pickup_lat: float | None = None
    pickup_lon: float | None = None
    delivery_address: str | None = None
    delivery_lat: float | None = None
    delivery_lon: float | None = None
    weight_kg: float | None = None
    is_fragile: bool | None = None
    is_temp_sensitive: bool | None = None
    required_vehicle_tag_ids: list[uuid.UUID] | None = None
    earliest_pickup: datetime | None = None
    latest_pickup: datetime | None = None
    earliest_delivery: datetime | None = None
    latest_delivery: datetime | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    external_ref: str | None = None
    custom_fields: dict[str, object] | None = None
    notes: str | None = None


class OrderResponse(BaseModel):
    id: uuid.UUID
    type: OrderType
    status: OrderStatus
    priority: OrderPriority
    pickup_address: str
    pickup_lat: float
    pickup_lon: float
    delivery_address: str
    delivery_lat: float
    delivery_lon: float
    weight_kg: float
    is_fragile: bool
    is_temp_sensitive: bool
    required_vehicle_tags: list[VehicleTagResponse] = []
    earliest_pickup: datetime | None
    latest_pickup: datetime | None
    earliest_delivery: datetime
    latest_delivery: datetime
    scheduled_for_date: date | None = None
    depot_id: uuid.UUID | None = None
    return_of_order_id: uuid.UUID | None = None
    assigned_courier_id: uuid.UUID | None
    preferred_courier_id: uuid.UUID | None = None
    route_id: uuid.UUID | None
    customer_email: str
    customer_phone: str | None
    tracking_token: str
    delivery_otp_code: str | None = None
    external_ref: str | None
    custom_fields: dict[str, object] | None = None
    attempt_count: int
    max_attempts: int
    failed_at: datetime | None
    customer_response_status: FailedOrderCustomerResponseStatus | None
    customer_response_due_at: datetime | None
    escalated_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    # ── Phase 1: passenger / ride-specific fields (only set for RIDE orders) ──
    passenger_count: int = 1
    passenger_name: str | None = None
    passenger_phone: str | None = None
    flight_info: dict[str, object] | None = None
    special_requirements: str | None = None
    pickup_buffer_minutes_override: int | None = None
    service_time_pickup_seconds_override: int | None = None
    # NULL for standalone orders; for split rides, equals the first
    # sibling's id (shared across the whole group).
    ride_group_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


class OrderAssign(BaseModel):
    courier_id: uuid.UUID


class OrderPreferredCourierUpdate(BaseModel):
    courier_id: uuid.UUID | None = None


class OrderReschedule(BaseModel):
    earliest_delivery: datetime
    latest_delivery: datetime


class OrderReturnCreate(BaseModel):
    earliest_pickup: datetime | None = None
    latest_pickup: datetime | None = None
    reason_code: str | None = None


class OrderStatusHistoryResponse(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    from_status: OrderStatus | None
    to_status: OrderStatus
    actor_user_id: uuid.UUID | None
    reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
