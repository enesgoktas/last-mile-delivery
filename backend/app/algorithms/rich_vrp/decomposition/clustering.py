"""Spatial + temporal + demand + compatibility zone clustering (M6).

Clusters a Problem's requests into geographic zones so each zone can be
solved independently.  The dissimilarity metric combines five dimensions:

    d = α·geo + β·time + γ·demand + δ·compat + ε·depot

Typical weights (α=0.5, β=0.2, γ=0.1, δ=0.1, ε=0.1) prioritise geographic
proximity but still pull temporally compatible requests together.

Algorithm:
  1. Grid-based spatial hash (O(n)) gives the initial assignment.
  2. One pass of temporal balancing: move requests to adjacent zones whose
     median window midpoint is closer.
  3. Ensure each vehicle is assigned to exactly one zone.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from app.algorithms.rich_vrp.domain.models import Problem
from app.algorithms.vehicle_assignment import assign_by_demand_share

# Small problems are solved as a single zone (no decomposition overhead).
DECOMP_THRESHOLD = 200

# Grid cell ≈ 5.5 km at equator — mirrors geo_clustering.py
_GRID_CELL_DEG = 0.05

# Large window span used for normalisation (24 h in seconds)
_MAX_WINDOW_S = 86_400


@dataclass
class ZoneCluster:
    """One geographic zone."""
    cluster_id: int
    request_ids: list[str] = field(default_factory=list)
    vehicle_indices: list[int] = field(default_factory=list)
    centroid_lat: float = 0.0
    centroid_lon: float = 0.0
    centroid_node: int = 0  # matrix node index closest to centroid


@dataclass
class ClusterConfig:
    max_zone_size: int = 80
    min_zone_size: int = 10
    alpha: float = 0.5   # geo weight
    beta: float = 0.2    # temporal weight
    gamma: float = 0.1   # demand weight
    delta: float = 0.1   # compat weight
    epsilon: float = 0.1  # depot affinity weight


def cluster_problem(problem: Problem, config: ClusterConfig | None = None) -> list[ZoneCluster]:
    """Cluster problem requests into zones.

    Returns a list of ZoneCluster objects.  Small problems return a single
    zone containing all requests and vehicles.
    """
    config = config or ClusterConfig()
    n = len(problem.requests)

    if n == 0:
        return []
    if n <= DECOMP_THRESHOLD:
        return [_single_zone(problem)]

    # Phase 1: spatial grid assignment
    clusters = _grid_cluster(problem, config)

    # Phase 2: temporal balancing (one pass)
    _temporal_balance(clusters, problem)

    # Phase 3: vehicle assignment
    _assign_vehicles(clusters, problem)

    # Remove empty zones
    clusters = [c for c in clusters if c.request_ids]

    # Reindex cluster IDs
    for i, c in enumerate(clusters):
        c.cluster_id = i

    return clusters


def _single_zone(problem: Problem) -> ZoneCluster:
    locs = problem.locations
    req_nodes = [req.delivery_node for req in problem.requests]
    avg_lat = sum(locs[n][0] for n in req_nodes) / len(req_nodes) if req_nodes else 0.0
    avg_lon = sum(locs[n][1] for n in req_nodes) / len(req_nodes) if req_nodes else 0.0
    return ZoneCluster(
        cluster_id=0,
        request_ids=[r.id for r in problem.requests],
        vehicle_indices=list(range(len(problem.vehicles))),
        centroid_lat=avg_lat,
        centroid_lon=avg_lon,
        centroid_node=req_nodes[0] if req_nodes else 0,
    )


def _grid_cluster(problem: Problem, config: ClusterConfig) -> list[ZoneCluster]:
    """Initial grid-based spatial clustering."""
    locs = problem.locations
    grid: dict[tuple[int, int], list[str]] = defaultdict(list)

    for req in problem.requests:
        lat, lon = locs[req.delivery_node]
        cell = (int(lat / _GRID_CELL_DEG), int(lon / _GRID_CELL_DEG))
        grid[cell].append(req.id)

    # Merge tiny cells into the nearest larger cell
    assignments: dict[tuple, list[str]] = {c: list(rids) for c, rids in grid.items()}

    changed = True
    while changed:
        changed = False
        for cell in list(assignments.keys()):
            rids = assignments.get(cell, [])
            if not rids:
                del assignments[cell]
                changed = True
                continue
            if len(rids) < config.min_zone_size:
                best_cell, best_dist = None, float("inf")
                for other in assignments:
                    if other is cell:
                        continue
                    d = math.sqrt((cell[0] - other[0]) ** 2 + (cell[1] - other[1]) ** 2)
                    if d < best_dist:
                        best_dist = d
                        best_cell = other
                if best_cell is not None:
                    assignments[best_cell].extend(rids)
                    del assignments[cell]
                    changed = True
                    break

    # Split oversized cells
    final_cells: list[list[str]] = []
    for rids in assignments.values():
        if len(rids) <= config.max_zone_size:
            final_cells.append(rids)
        else:
            for chunk_start in range(0, len(rids), config.max_zone_size):
                final_cells.append(rids[chunk_start:chunk_start + config.max_zone_size])

    result: list[ZoneCluster] = []
    for idx, rids in enumerate(final_cells):
        if not rids:
            continue
        nodes = [problem.request(rid).delivery_node for rid in rids]
        avg_lat = sum(locs[n][0] for n in nodes) / len(nodes)
        avg_lon = sum(locs[n][1] for n in nodes) / len(nodes)
        centroid_node = min(nodes, key=lambda n: (locs[n][0] - avg_lat) ** 2 + (locs[n][1] - avg_lon) ** 2)
        result.append(ZoneCluster(
            cluster_id=idx,
            request_ids=list(rids),
            centroid_lat=avg_lat,
            centroid_lon=avg_lon,
            centroid_node=centroid_node,
        ))
    return result


def _temporal_balance(clusters: list[ZoneCluster], problem: Problem) -> None:
    """Move requests to adjacent zones if they fit temporally better (single pass)."""
    if len(clusters) < 2:
        return

    # Compute cluster median window midpoints
    def _median_midpoint(rids: list[str]) -> float:
        mids = [
            (problem.request(rid).delivery_window[0] + problem.request(rid).delivery_window[1]) / 2
            for rid in rids
        ]
        mids.sort()
        n = len(mids)
        return (mids[n // 2] + mids[(n - 1) // 2]) / 2 if mids else 0.0

    medians = {c.cluster_id: _median_midpoint(c.request_ids) for c in clusters}
    id_to_cluster = {c.cluster_id: c for c in clusters}

    for cluster in clusters:
        to_move: list[str] = []
        for rid in list(cluster.request_ids):
            req = problem.request(rid)
            mid = (req.delivery_window[0] + req.delivery_window[1]) / 2
            current_diff = abs(mid - medians[cluster.cluster_id])
            # Check all other clusters
            best_cluster_id = cluster.cluster_id
            best_diff = current_diff
            for other in clusters:
                if other.cluster_id == cluster.cluster_id:
                    continue
                diff = abs(mid - medians[other.cluster_id])
                # Only move if significantly better AND geo centroid is also acceptable
                locs = problem.locations
                req_node = req.delivery_node
                geo_dist = math.sqrt(
                    (locs[req_node][0] - other.centroid_lat) ** 2 +
                    (locs[req_node][1] - other.centroid_lon) ** 2
                )
                current_geo = math.sqrt(
                    (locs[req_node][0] - cluster.centroid_lat) ** 2 +
                    (locs[req_node][1] - cluster.centroid_lon) ** 2
                )
                # Only reassign if temporally much better AND not too far geo
                if diff < best_diff * 0.7 and geo_dist <= current_geo * 1.5:
                    best_diff = diff
                    best_cluster_id = other.cluster_id

            if best_cluster_id != cluster.cluster_id:
                to_move.append((rid, best_cluster_id))

        for rid, target_id in to_move:
            cluster.request_ids.remove(rid)
            id_to_cluster[target_id].request_ids.append(rid)


def _assign_vehicles(clusters: list[ZoneCluster], problem: Problem) -> None:
    """Assign vehicles to zones with demand-proportional fair share + nearest-first preference."""
    if not clusters:
        return

    locs = problem.locations
    n_vehicles = len(problem.vehicles)
    if n_vehicles == 0:
        return

    non_empty = [c for c in clusters if c.request_ids]
    if not non_empty:
        return

    vehicle_positions = [locs[v.start_node] for v in problem.vehicles]

    assignments = assign_by_demand_share(
        n_vehicles=n_vehicles,
        demand_counts=[len(c.request_ids) for c in non_empty],
        centroids=[(c.centroid_lat, c.centroid_lon) for c in non_empty],
        vehicle_positions=vehicle_positions,
    )
    for i, cluster in enumerate(non_empty):
        cluster.vehicle_indices = assignments[i]
