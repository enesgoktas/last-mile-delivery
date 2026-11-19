"""Sub-matrix extraction for zone-local solves (M6).

The full distance/duration matrix may be large (k×k for k locations across
all zones).  When solving a single zone we only need the rows/columns for
nodes actually used by that zone.

In M6 the full matrix is already pre-computed in VRPInput so extraction is
a simple slice operation — no additional OSRM calls are needed.  A future
optimisation (M7+) could request only the zone-relevant rows from OSRM at
construction time, reducing both memory and OSRM query time.
"""
from __future__ import annotations

from app.algorithms.rich_vrp.decomposition.clustering import ZoneCluster
from app.algorithms.rich_vrp.domain.models import Problem


def extract_zone_nodes(problem: Problem, cluster: ZoneCluster) -> list[int]:
    """Return all unique matrix nodes needed to solve this zone.

    Includes: delivery nodes, pickup nodes, vehicle start/end nodes.
    """
    nodes: set[int] = set()
    for rid in cluster.request_ids:
        req = problem.request(rid)
        nodes.add(req.pickup_node)
        nodes.add(req.delivery_node)
    for v_idx in cluster.vehicle_indices:
        vehicle = problem.vehicles[v_idx]
        nodes.add(vehicle.start_node)
        nodes.add(vehicle.end_node)
    return sorted(nodes)


def build_zone_problem(problem: Problem, cluster: ZoneCluster) -> Problem:
    """Build a Problem containing only the requests and vehicles for one zone.

    The full matrix is reused (node indices remain valid — no re-indexing).
    """
    from app.algorithms.rich_vrp.domain.models import Problem as _Problem  # noqa: PLC0415

    zone_requests = [problem.request(rid) for rid in cluster.request_ids]
    zone_vehicles = [problem.vehicles[i] for i in cluster.vehicle_indices]

    return _Problem(
        requests=zone_requests,
        vehicles=zone_vehicles,
        matrix=problem.matrix,   # share full matrix — indices still valid
        locations=problem.locations,
        service_date_anchor=problem.service_date_anchor,
        spread_fleet=problem.spread_fleet,
    )
