"""
Geographic pre-clustering for large-scale VRP.

When order count exceeds a threshold, clusters orders by geographic
proximity to reduce the N×N matrix size. Each cluster is solved
independently, then results are merged.

Uses grid-based spatial hashing for O(n) clustering — no scipy/sklearn
dependency required.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

from app.algorithms.vehicle_assignment import assign_by_demand_share

logger = logging.getLogger(__name__)

# Orders below this count are solved as a single batch
CLUSTERING_THRESHOLD = 250

# Target orders per cluster (trades off solution quality vs. speed)
TARGET_CLUSTER_SIZE = 100

# Grid cell size in degrees (~5.5 km at equator)
GRID_CELL_DEGREES = 0.05


@dataclass
class GeoCluster:
    """A geographic cluster of orders and their nearby vehicles."""
    cluster_id: int
    order_indices: list[int] = field(default_factory=list)
    vehicle_indices: list[int] = field(default_factory=list)
    center_lat: float = 0.0
    center_lon: float = 0.0

    @property
    def order_count(self) -> int:
        return len(self.order_indices)

    @property
    def vehicle_count(self) -> int:
        return len(self.vehicle_indices)


def should_cluster(order_count: int) -> bool:
    """Check if the order count warrants pre-clustering."""
    return order_count > CLUSTERING_THRESHOLD


def cluster_orders(
    order_locations: list[tuple[float, float]],
    vehicle_locations: list[tuple[float, float]],
    *,
    target_size: int = TARGET_CLUSTER_SIZE,
    grid_cell_deg: float = GRID_CELL_DEGREES,
) -> list[GeoCluster]:
    """Cluster orders by geographic proximity using grid-based hashing.

    Args:
        order_locations: (lat, lon) for each order's delivery point.
        vehicle_locations: (lat, lon) for each vehicle's depot.
        target_size: Target number of orders per cluster.
        grid_cell_deg: Grid cell size in degrees.

    Returns:
        List of GeoClusters with order and vehicle assignments.
    """
    if not order_locations:
        return []

    n_orders = len(order_locations)
    if n_orders <= CLUSTERING_THRESHOLD:
        # Single cluster with all orders and vehicles
        return [GeoCluster(
            cluster_id=0,
            order_indices=list(range(n_orders)),
            vehicle_indices=list(range(len(vehicle_locations))),
            center_lat=sum(lat for lat, _ in order_locations) / n_orders,
            center_lon=sum(lon for _, lon in order_locations) / n_orders,
        )]

    # Adjust grid cell size to hit target cluster count
    target_clusters = max(2, n_orders // target_size)
    adjusted_cell = _estimate_grid_cell(order_locations, target_clusters, grid_cell_deg)

    # Assign orders to grid cells
    cell_to_orders: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (lat, lon) in enumerate(order_locations):
        cell = (int(lat / adjusted_cell), int(lon / adjusted_cell))
        cell_to_orders[cell].append(i)

    # Merge small adjacent cells to reach target size
    clusters = _merge_cells(cell_to_orders, adjusted_cell, target_size)

    # Assign vehicles to nearest cluster
    _assign_vehicles(clusters, vehicle_locations, order_locations)

    # Ensure every cluster has at least one vehicle
    _ensure_vehicle_coverage(clusters, vehicle_locations)

    # Remove clusters that became empty after coverage redistribution,
    # and merge tiny clusters (< 5 orders) into the nearest viable cluster
    MIN_CLUSTER_SIZE = 5
    _merge_tiny_clusters(clusters, MIN_CLUSTER_SIZE)
    clusters = [c for c in clusters if c.order_indices]

    logger.info(
        "Geographic clustering: %d orders -> %d clusters "
        "(sizes: %s, vehicles: %s)",
        n_orders,
        len(clusters),
        [c.order_count for c in clusters],
        [c.vehicle_count for c in clusters],
    )

    return clusters


def _estimate_grid_cell(
    locations: list[tuple[float, float]],
    target_clusters: int,
    base_cell: float,
) -> float:
    """Estimate grid cell size to approximate the target cluster count."""
    if not locations:
        return base_cell

    lat_min = min(lat for lat, _ in locations)
    lat_max = max(lat for lat, _ in locations)
    lon_min = min(lon for _, lon in locations)
    lon_max = max(lon for _, lon in locations)

    lat_range = max(lat_max - lat_min, 0.01)
    lon_range = max(lon_max - lon_min, 0.01)

    area = lat_range * lon_range
    cell_area = area / target_clusters
    cell_size = math.sqrt(cell_area)

    return max(cell_size, 0.005)  # minimum ~500m cells


def _merge_cells(
    cell_to_orders: dict[tuple[int, int], list[int]],
    cell_size: float,
    target_size: int,
) -> list[GeoCluster]:
    """Merge grid cells into clusters, combining small adjacent cells."""
    # Sort cells by position for spatial locality
    sorted_cells = sorted(cell_to_orders.keys())

    clusters: list[GeoCluster] = []
    used_cells: set[tuple[int, int]] = set()
    cluster_id = 0

    for cell in sorted_cells:
        if cell in used_cells:
            continue

        # Start a new cluster with this cell
        cluster_orders = list(cell_to_orders[cell])
        used_cells.add(cell)

        # Try to merge adjacent cells if current cluster is small
        if len(cluster_orders) < target_size:
            for adj in _adjacent_cells(cell):
                if adj in used_cells or adj not in cell_to_orders:
                    continue
                candidate = cell_to_orders[adj]
                if len(cluster_orders) + len(candidate) <= target_size * 1.5:
                    cluster_orders.extend(candidate)
                    used_cells.add(adj)

        clusters.append(GeoCluster(
            cluster_id=cluster_id,
            order_indices=cluster_orders,
        ))
        cluster_id += 1

    return clusters


def _adjacent_cells(cell: tuple[int, int]) -> list[tuple[int, int]]:
    """Return the 8 adjacent grid cells."""
    r, c = cell
    return [
        (r - 1, c - 1), (r - 1, c), (r - 1, c + 1),
        (r, c - 1),                  (r, c + 1),
        (r + 1, c - 1), (r + 1, c), (r + 1, c + 1),
    ]


def _assign_vehicles(
    clusters: list[GeoCluster],
    vehicle_locations: list[tuple[float, float]],
    order_locations: list[tuple[float, float]],
) -> None:
    """Distribute vehicles across clusters by demand share, then proximity.

    NOTE: Uses depot location (start-of-day position) for each vehicle.
    For intra-day re-optimizations, callers should substitute current
    location for depot when building vehicle_locations.
    """
    # Compute cluster centroids from order locations
    for cluster in clusters:
        if not cluster.order_indices:
            continue
        lats = [order_locations[i][0] for i in cluster.order_indices]
        lons = [order_locations[i][1] for i in cluster.order_indices]
        cluster.center_lat = sum(lats) / len(lats)
        cluster.center_lon = sum(lons) / len(lons)

    non_empty = [c for c in clusters if c.order_indices]
    if not non_empty or not vehicle_locations:
        return

    assignments = assign_by_demand_share(
        n_vehicles=len(vehicle_locations),
        demand_counts=[len(c.order_indices) for c in non_empty],
        centroids=[(c.center_lat, c.center_lon) for c in non_empty],
        vehicle_positions=vehicle_locations,
    )
    for i, cluster in enumerate(non_empty):
        cluster.vehicle_indices = assignments[i]


def _ensure_vehicle_coverage(
    clusters: list[GeoCluster],
    vehicle_locations: list[tuple[float, float]],
) -> None:
    """Ensure every cluster has at least one vehicle.

    For empty clusters, move the nearest vehicle from an over-populated
    cluster (one with 2+ vehicles) rather than duplicating it.  If no
    cluster can spare a vehicle, the empty cluster's orders are merged
    into the nearest non-empty cluster instead.
    """
    already_assigned: set[int] = set()
    for cluster in clusters:
        already_assigned.update(cluster.vehicle_indices)

    empty_clusters = [c for c in clusters if not c.vehicle_indices]
    if not empty_clusters:
        return

    for cluster in empty_clusters:
        # Try to steal a vehicle from a cluster that has more than one
        best_v: int | None = None
        best_dist = float("inf")
        donor_cluster: GeoCluster | None = None

        for other in clusters:
            if len(other.vehicle_indices) < 2:
                continue
            for v_idx in other.vehicle_indices:
                v_lat, v_lon = vehicle_locations[v_idx]
                dist = _haversine_approx(v_lat, v_lon, cluster.center_lat, cluster.center_lon)
                if dist < best_dist:
                    best_dist = dist
                    best_v = v_idx
                    donor_cluster = other

        if best_v is not None and donor_cluster is not None:
            donor_cluster.vehicle_indices.remove(best_v)
            cluster.vehicle_indices.append(best_v)
        else:
            # No donor available — merge this cluster's orders into the
            # nearest cluster that already has vehicles.
            nearest: GeoCluster | None = None
            nearest_dist = float("inf")
            for other in clusters:
                if other is cluster or not other.vehicle_indices:
                    continue
                dist = _haversine_approx(
                    cluster.center_lat, cluster.center_lon,
                    other.center_lat, other.center_lon,
                )
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest = other
            if nearest is not None:
                nearest.order_indices.extend(cluster.order_indices)
                cluster.order_indices.clear()


def _merge_tiny_clusters(clusters: list[GeoCluster], min_size: int) -> None:
    """Merge clusters below *min_size* into their nearest larger neighbour."""
    for cluster in list(clusters):
        if not cluster.order_indices or len(cluster.order_indices) >= min_size:
            continue
        nearest: GeoCluster | None = None
        nearest_dist = float("inf")
        for other in clusters:
            if other is cluster or not other.order_indices:
                continue
            if len(other.order_indices) < min_size and other.cluster_id > cluster.cluster_id:
                continue
            dist = _haversine_approx(
                cluster.center_lat, cluster.center_lon,
                other.center_lat, other.center_lon,
            )
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = other
        if nearest is not None:
            nearest.order_indices.extend(cluster.order_indices)
            nearest.vehicle_indices.extend(cluster.vehicle_indices)
            cluster.order_indices.clear()
            cluster.vehicle_indices.clear()


def _haversine_approx(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast approximate distance in km (no trig, good enough for clustering)."""
    dlat = (lat2 - lat1) * 111.0
    dlon = (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.sqrt(dlat * dlat + dlon * dlon)
