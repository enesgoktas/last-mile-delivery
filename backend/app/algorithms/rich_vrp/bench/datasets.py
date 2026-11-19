"""Synthetic benchmark dataset fixtures for the rich VRP bench harness.

Each loader returns a VRPInput ready to pass to any SolverAdapter.
Datasets are deterministic (fixed seeds) so bench results are reproducible.
"""
from __future__ import annotations

import math
import random

from app.algorithms.vrp_solver import (
    OrderData,
    VehicleData,
    VRPInput,
)
from app.models.enums import OrderPriority, VehicleType

_DAY_START = 8 * 3600   # 08:00
_DAY_END = 18 * 3600    # 18:00
_DEPOT_LAT, _DEPOT_LON = 41.015, 28.979  # Istanbul-ish


def load_synthetic(name: str) -> VRPInput:
    """Return a named synthetic VRPInput fixture."""
    sizes = {
        "synthetic_30": 30,
        "synthetic_60": 60,
        "synthetic_120": 120,
        "synthetic_300": 300,
    }
    if name not in sizes:
        raise KeyError(f"Unknown dataset: {name!r}. Known: {sorted(sizes)}")
    return _build_synthetic(n_orders=sizes[name], seed=42)


def _build_synthetic(n_orders: int, seed: int) -> VRPInput:
    rng = random.Random(seed)

    n_vehicles = max(3, n_orders // 10)

    locations: list[tuple[float, float]] = [(_DEPOT_LAT, _DEPOT_LON)]  # index 0 = depot

    # Generate order pickup and delivery locations around the depot
    order_loc_start = 1
    for _ in range(n_orders * 2):
        lat = _DEPOT_LAT + rng.uniform(-0.08, 0.08)
        lon = _DEPOT_LON + rng.uniform(-0.10, 0.10)
        locations.append((lat, lon))

    n_locs = len(locations)

    # Euclidean distance matrix (in metres) and duration matrix (in seconds)
    dist_matrix: list[list[int]] = [[0] * n_locs for _ in range(n_locs)]
    dur_matrix: list[list[int]] = [[0] * n_locs for _ in range(n_locs)]
    for i in range(n_locs):
        for j in range(n_locs):
            if i == j:
                continue
            d = _haversine_m(locations[i], locations[j])
            dist_matrix[i][j] = int(d)
            dur_matrix[i][j] = int(d / 10)  # ~36 km/h average urban speed

    # Build orders
    orders: list[OrderData] = []
    for k in range(n_orders):
        pickup_idx = order_loc_start + k * 2
        delivery_idx = order_loc_start + k * 2 + 1
        earliest = _DAY_START + rng.randint(0, 4 * 3600)
        latest = min(earliest + rng.randint(3600, 4 * 3600), _DAY_END)
        priority = rng.choice([
            OrderPriority.NORMAL,
            OrderPriority.NORMAL,
            OrderPriority.HIGH,
            OrderPriority.LOW,
        ])
        orders.append(OrderData(
            order_id=f"ord-{k:04d}",
            pickup_index=pickup_idx,
            delivery_index=delivery_idx,
            earliest_time_s=earliest,
            latest_time_s=latest,
            hard_latest_time_s=latest + 1800,
            service_time_s=300,
            weight_grams=rng.randint(500, 5000),
            volume_cm3=rng.randint(1000, 30000),
            package_count=rng.randint(1, 5),
            priority=priority,
            unserved_penalty=10000 if priority == OrderPriority.URGENT else 1000,
            lateness_penalty_per_minute=10,
            priority_penalty_multiplier=1.0,
        ))

    # Build vehicles
    vehicles: list[VehicleData] = []
    for v in range(n_vehicles):
        vehicles.append(VehicleData(
            courier_id=f"veh-{v:03d}",
            vehicle_type=VehicleType.CAR_VAN,
            physical_courier_id=f"veh-{v:03d}",
            depot_index=0,
            start_time_s=_DAY_START,
            end_time_s=_DAY_END,
            hard_end_time_s=_DAY_END + 3600,
            capacity_grams=50000,
            capacity_volume_cm3=500000,
            max_packages=50,
            max_stops=0,
            max_distance_m=0,
            fixed_route_cost=0,
            distance_cost_per_km=10,
            labor_cost_per_minute=2,
            fuel_cost_per_km=5,
            overtime_penalty_per_minute=5,
            overtime_cost_per_minute=0,
            trip_index=0,
        ))

    return VRPInput(
        orders=orders,
        vehicles=vehicles,
        locations=locations,
        duration_matrix=dur_matrix,
        distance_matrix=dist_matrix,
    )


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Return great-circle distance in metres between two (lat, lon) points."""
    R = 6_371_000.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))
