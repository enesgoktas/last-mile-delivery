"""Portal nodes for inter-zone connectivity (M6).

A portal node is a matrix node at or near a zone boundary — it is the
cheapest "gateway" for a vehicle leaving one zone and entering another.
Used by border_repair and the sparse_graph to estimate cross-zone arc costs.
"""
from __future__ import annotations

import math

from app.algorithms.rich_vrp.decomposition.clustering import ZoneCluster
from app.algorithms.rich_vrp.domain.models import Problem


def find_portals(
    clusters: list[ZoneCluster],
    problem: Problem,
    k: int = 3,
) -> dict[int, list[int]]:
    """Return, for each zone, up to k portal node indices near zone boundaries.

    A node is a portal candidate if it is among the k requests in this zone
    whose delivery node is closest to the centroid of *another* zone.

    Returns: {cluster_id: [node_index, ...]}
    """
    if len(clusters) <= 1:
        return {c.cluster_id: [] for c in clusters}

    locs = problem.locations
    portals: dict[int, list[int]] = {}

    for c in clusters:
        other_centroids = [
            (other.centroid_lat, other.centroid_lon)
            for other in clusters
            if other.cluster_id != c.cluster_id
        ]
        if not other_centroids or not c.request_ids:
            portals[c.cluster_id] = []
            continue

        # Score each node in this zone by its closeness to any other zone centroid
        scored: list[tuple[float, int]] = []
        for rid in c.request_ids:
            req = problem.request(rid)
            node = req.delivery_node
            lat, lon = locs[node]
            min_dist = min(
                math.sqrt((lat - clat) ** 2 + (lon - clon) ** 2)
                for clat, clon in other_centroids
            )
            scored.append((min_dist, node))

        scored.sort()
        seen: set[int] = set()
        portal_nodes: list[int] = []
        for _, node in scored:
            if node not in seen:
                portal_nodes.append(node)
                seen.add(node)
            if len(portal_nodes) >= k:
                break

        portals[c.cluster_id] = portal_nodes

    return portals
