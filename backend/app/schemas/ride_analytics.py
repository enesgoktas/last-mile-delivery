"""Phase 2 ride analytics schemas — used by the Reports page.

Renders only when the tenant has `rides_enabled`; airport-specific
breakdowns also require `ride_airport_features_enabled`.
"""
from __future__ import annotations

from pydantic import BaseModel


class PassengerRideSummary(BaseModel):
    passenger_name: str
    ride_count: int
    completed_count: int
    cancelled_count: int


class RideStatusBreakdown(BaseModel):
    pending: int = 0
    assigned: int = 0
    in_transit: int = 0
    delivered: int = 0
    cancelled: int = 0
    failed: int = 0


class RideAnalyticsResponse(BaseModel):
    total_rides: int
    completed_rides: int
    cancelled_rides: int
    upcoming_rides: int
    by_status: RideStatusBreakdown
    top_passengers: list[PassengerRideSummary]
    airport_pickups: int = 0  # filled when airport features are on


class CourierUtilizationRow(BaseModel):
    courier_id: str
    route_count: int
    total_distance_km: float
    total_hours: float
    order_count: int


class FleetUtilizationResponse(BaseModel):
    rows: list[CourierUtilizationRow]
    total_route_count: int
    total_distance_km: float
    total_hours: float


class TerminalPunctualityRow(BaseModel):
    terminal: str
    total: int
    on_time: int
    on_time_rate: float


class AirportPunctualityResponse(BaseModel):
    rows: list[TerminalPunctualityRow]
    overall_total: int
    overall_on_time: int
    overall_on_time_rate: float
