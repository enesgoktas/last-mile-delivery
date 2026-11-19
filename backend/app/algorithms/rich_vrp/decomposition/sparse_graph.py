"""k-NN sparse graph with portal edges for cross-zone travel estimation (M6).

For large instances the full n×n matrix is too expensive to compute.  The
sparse graph keeps only k nearest-neighbour edges per node (intra-zone) plus
explicit portal edges between zones.  Cross-zone arc cost is approximated
via the cheapest portal path.

In M6 the full matrix is still pre-computed and passed in via VRPInput for
correctness.  The sparse graph is used for:
  - Candidate list pruning (limit LS neighbourhood to k neighbours)
  - Cross-zone travel estimation in the border-repair step
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.algorithms.rich_vrp.decomposition.clustering import ZoneCluster
from app.algorithms.rich_vrp.domain.models import Problem


@dataclass
class SparseGraph:
    """Sparse k-NN adjacency for a Problem."""
    k: int
    neighbours: dict[int, list[int]] = field(default_factory=dict)
    # (from_zone_id, to_zone_id) -> (from_portal_node, to_portal_node)
    portal_edges: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)

    def estimate_cross_zone_duration(
        self,
        from_node: int,
        to_node: int,
        from_zone: int,
        to_zone: int,
        problem: Problem,
    ) -> int:
        """Estimate duration from from_node (in from_zone) to to_node (in to_zone).

        Uses the portal edge if available; falls back to direct matrix lookup.
        """
        edge_key = (from_zone, to_zone)
        if edge_key in self.portal_edges:
            fp, tp = self.portal_edges[edge_key]
            return (
                problem.matrix.duration(from_node, fp)
                + problem.matrix.duration(fp, tp)
                + problem.matrix.duration(tp, to_node)
            )
        return problem.matrix.duration(from_node, to_node)


def build_sparse_graph(
    problem: Problem,
    clusters: list[ZoneCluster],
    k: int = 8,
) -> SparseGraph:
    """Build a sparse k-NN graph and portal edges from clusters."""
    graph = SparseGraph(k=k)

    # k-NN per delivery node (using matrix duration as distance)
    all_nodes = list({req.delivery_node for req in problem.requests})
    all_nodes += list({v.start_node for v in problem.vehicles})
    all_nodes = sorted(set(all_nodes))

    for node in all_nodes:
        costs = [(problem.matrix.duration(node, other), other) for other in all_nodes if other != node]
        costs.sort()
        graph.neighbours[node] = [n for _, n in costs[:k]]

    # Portal edges: for each pair of zones, find cheapest portal pair
    zone_nodes: dict[int, list[int]] = {}
    for c in clusters:
        zone_nodes[c.cluster_id] = [problem.request(rid).delivery_node for rid in c.request_ids]

    zone_ids = [c.cluster_id for c in clusters]
    for i, z_from in enumerate(zone_ids):
        for z_to in zone_ids[i + 1:]:
            from_nodes = zone_nodes.get(z_from, [])
            to_nodes = zone_nodes.get(z_to, [])
            if not from_nodes or not to_nodes:
                continue
            best_cost = None
            best_pair = None
            # Sample up to 5 nodes per zone to keep build time O(k²)
            for fn in from_nodes[:5]:
                for tn in to_nodes[:5]:
                    cost = problem.matrix.duration(fn, tn)
                    if best_cost is None or cost < best_cost:
                        best_cost = cost
                        best_pair = (fn, tn)
            if best_pair is not None:
                graph.portal_edges[(z_from, z_to)] = best_pair
                graph.portal_edges[(z_to, z_from)] = (best_pair[1], best_pair[0])

    return graph
