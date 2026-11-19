from __future__ import annotations

import math

from app.algorithms.insertion_heuristic import EligibleCourier, ExistingStop, NewOrder
from app.models.enums import OrderPriority, VehicleType


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def mock_matrix_provider(
    coords: list[tuple[float, float]],
) -> tuple[list[list[int]], str]:
    """Build a travel-time matrix from haversine distances at 30 km/h."""
    node_count = len(coords)
    speed_ms = 30.0 * 1000.0 / 3600.0
    matrix = [[0] * node_count for _ in range(node_count)]
    for i in range(node_count):
        for j in range(i + 1, node_count):
            distance_m = haversine_meters(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
            travel_s = round(distance_m * 1.3 / speed_ms) if speed_ms > 0 else 0
            matrix[i][j] = travel_s
            matrix[j][i] = travel_s
    return matrix, "mock"


def make_courier(
    courier_id: str = "c1",
    vehicle_type: VehicleType = VehicleType.CAR_VAN,
    capacity_grams: int = 50_000,
    depot_lat: float = 41.0,
    depot_lon: float = 29.0,
    current_stops: list[ExistingStop] | None = None,
    current_load: int = 0,
    route_id: str | None = "route-1",
    allow_fragile: bool = True,
    allow_temp_sensitive: bool = True,
) -> EligibleCourier:
    return EligibleCourier(
        courier_id=courier_id,
        vehicle_type=vehicle_type,
        capacity_grams=capacity_grams,
        depot_lat=depot_lat,
        depot_lon=depot_lon,
        start_time_s=0,
        end_time_s=86_400,
        route_id=route_id,
        current_stops=current_stops or [],
        current_load_grams=current_load,
        allow_fragile=allow_fragile,
        allow_temp_sensitive=allow_temp_sensitive,
    )


def make_order(
    order_id: str = "o1",
    pickup_lat: float = 41.01,
    pickup_lon: float = 29.01,
    delivery_lat: float = 41.02,
    delivery_lon: float = 29.02,
    weight_grams: int = 2_000,
    is_fragile: bool = False,
    priority: OrderPriority = OrderPriority.NORMAL,
) -> NewOrder:
    return NewOrder(
        order_id=order_id,
        pickup_lat=pickup_lat,
        pickup_lon=pickup_lon,
        delivery_lat=delivery_lat,
        delivery_lon=delivery_lon,
        weight_grams=weight_grams,
        is_fragile=is_fragile,
        is_temp_sensitive=False,
        earliest_s=0,
        latest_s=86_400,
        pickup_earliest_s=0,
        pickup_latest_s=86_400,
        priority=priority,
        service_time_s=300,
    )


def make_existing_stop(
    lat: float = 41.005,
    lon: float = 29.005,
    weight_delta: int = 1_000,
) -> ExistingStop:
    return ExistingStop(
        lat=lat,
        lon=lon,
        earliest_s=0,
        latest_s=86_400,
        arrival_s=3_600,
        service_s=300,
        weight_delta_g=weight_delta,
    )
