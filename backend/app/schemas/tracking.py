from datetime import datetime

from pydantic import BaseModel

from app.models.enums import OrderStatus, OrderType


class TenantBranding(BaseModel):
    """Subset of tenant branding fields exposed to the public tracking page."""
    logo_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None


class TrackingResponse(BaseModel):
    order_id: str
    order_type: OrderType = OrderType.ON_DEMAND
    status: OrderStatus
    status_label: str | None = None
    delivery_address: str
    delivery_lat: float
    delivery_lon: float
    pickup_address: str | None = None
    route_id: str | None = None
    courier_lat: float | None = None
    courier_lon: float | None = None
    estimated_arrival: datetime | None = None
    last_updated: datetime | None = None
    eta_source: str | None = None
    total_stops: int | None = None
    completed_stops: int | None = None
    remaining_stops: int | None = None
    next_stop_sequence: int | None = None
    target_stop_sequence: int | None = None
    can_reschedule: bool = False
    can_update_instructions: bool = False
    show_live_map: bool = True
    eta_visibility: str = "LIVE_MAP"
    customer_instructions: str | None = None
    # Tenant branding injected so the tracking page can apply white-label styles
    branding: TenantBranding = TenantBranding()
    # Phase 1: passenger-facing fields (only set for RIDE orders)
    passenger_name: str | None = None
    flight_info: dict | None = None


class TrackingRescheduleRequest(BaseModel):
    earliest_delivery: datetime
    latest_delivery: datetime
    customer_instructions: str | None = None
