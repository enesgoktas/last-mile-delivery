"""Demand-proportional vehicle assignment shared between clustering modules.

Both geo_clustering.py (pre-clustering for the OR-Tools / ortools_cvrptw path)
and rich_vrp/decomposition/clustering.py (zone decomposition for rich_alns)
used to contain nearly-identical demand-share assignment algorithms.  This
module provides the single canonical implementation that both call.
"""
from __future__ import annotations

import math


def assign_by_demand_share(
    n_vehicles: int,
    demand_counts: list[int],
    centroids: list[tuple[float, float]],
    vehicle_positions: list[tuple[float, float]],
) -> list[list[int]]:
    """Distribute vehicles across zones proportionally to demand.

    Args:
        n_vehicles:        Total number of vehicles to assign.
        demand_counts:     Number of orders/requests in each zone (parallel to centroids).
        centroids:         (lat, lon) centroid of each zone.
        vehicle_positions: (lat, lon) start position of each vehicle.

    Returns:
        A list of vehicle-index lists, one per zone (same order as demand_counts).

    Pure depot-to-centroid nearest-assignment fails when many vehicles share
    one depot: every vehicle picks the same nearest zone and the rest get
    nothing.  Instead, each non-empty zone gets a target headcount
    proportional to its order count (largest-remainder rounding, min 1), then
    targets are filled by walking sorted (distance, vehicle, zone) pairs.
    """
    n_zones = len(demand_counts)
    assignments: list[list[int]] = [[] for _ in range(n_zones)]

    if n_zones == 0 or n_vehicles == 0:
        return assignments

    non_empty_idx = [i for i, d in enumerate(demand_counts) if d > 0]
    if not non_empty_idx:
        return assignments

    total = sum(demand_counts[i] for i in non_empty_idx)
    raw = [(demand_counts[i] / total) * n_vehicles for i in non_empty_idx]
    targets = [max(1, int(r)) for r in raw]

    # Largest-remainder rounding
    leftover = n_vehicles - sum(targets)
    if leftover > 0:
        order = sorted(range(len(non_empty_idx)), key=lambda k: -(raw[k] - int(raw[k])))
        for k in range(leftover):
            targets[order[k % len(order)]] += 1
    elif leftover < 0:
        order = sorted(range(len(non_empty_idx)), key=lambda k: raw[k])
        k = 0
        while leftover < 0:
            if targets[order[k]] > 1:
                targets[order[k]] -= 1
                leftover += 1
            k = (k + 1) % len(order)

    remaining = {non_empty_idx[j]: targets[j] for j in range(len(non_empty_idx))}

    # Build sorted (distance, vehicle, zone) triples and assign greedily
    pairs: list[tuple[float, int, int]] = []
    for v_idx, (v_lat, v_lon) in enumerate(vehicle_positions):
        for zone_idx in non_empty_idx:
            c_lat, c_lon = centroids[zone_idx]
            d = _haversine(v_lat, v_lon, c_lat, c_lon)
            pairs.append((d, v_idx, zone_idx))
    pairs.sort()

    assigned: set[int] = set()
    for _, v_idx, zone_idx in pairs:
        if v_idx in assigned:
            continue
        if remaining.get(zone_idx, 0) <= 0:
            continue
        assignments[zone_idx].append(v_idx)
        assigned.add(v_idx)
        remaining[zone_idx] -= 1
        if len(assigned) == n_vehicles:
            break

    # Any vehicles still unassigned (rounding edge case) → put in nearest zone
    for v_idx in range(n_vehicles):
        if v_idx in assigned:
            continue
        v_lat, v_lon = vehicle_positions[v_idx]
        nearest_idx = min(
            non_empty_idx,
            key=lambda i: _haversine(v_lat, v_lon, centroids[i][0], centroids[i][1]),
        )
        assignments[nearest_idx].append(v_idx)
        assigned.add(v_idx)

    return assignments


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast approximate distance in km (flat-earth, good enough for clustering)."""
    dlat = (lat2 - lat1) * 111.0
    dlon = (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2 + 1e-9))
    return math.sqrt(dlat * dlat + dlon * dlon)
