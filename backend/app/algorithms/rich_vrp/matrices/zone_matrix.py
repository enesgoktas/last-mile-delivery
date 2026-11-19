"""Zone-aware matrix view with portal-based cross-zone routing (M6).

For zone-local solves the MatrixView is used unchanged (node indices match the
full matrix).  For cross-zone travel estimates (border repair, global SP cost),
this view routes through portal nodes to avoid large matrix lookups.
"""
from __future__ import annotations

from app.algorithms.rich_vrp.decomposition.sparse_graph import SparseGraph
from app.algorithms.rich_vrp.domain.models import MatrixView, Problem


class ZoneMatrixView:
    """MatrixView that routes cross-zone arcs through portal nodes."""

    __slots__ = ("_base", "_graph", "_node_to_zone", "_default_zone")

    def __init__(
        self,
        base: MatrixView,
        graph: SparseGraph,
        node_to_zone: dict[int, int],
        default_zone: int = 0,
    ) -> None:
        self._base = base
        self._graph = graph
        self._node_to_zone = node_to_zone
        self._default_zone = default_zone

    def duration(self, i: int, j: int, profile: str = "driving") -> int:
        z_i = self._node_to_zone.get(i, self._default_zone)
        z_j = self._node_to_zone.get(j, self._default_zone)
        if z_i == z_j:
            return self._base.duration(i, j, profile)
        return self._graph.estimate_cross_zone_duration(i, j, z_i, z_j, _base_problem_stub(self._base))

    def distance(self, i: int, j: int, profile: str = "driving") -> int:
        return self._base.distance(i, j, profile)


def build_zone_matrix(problem: Problem, graph: SparseGraph, node_to_zone: dict[int, int]) -> ZoneMatrixView:
    return ZoneMatrixView(
        base=problem.matrix,
        graph=graph,
        node_to_zone=node_to_zone,
    )


def _base_problem_stub(matrix: MatrixView) -> object:
    """Minimal stub so SparseGraph.estimate_cross_zone_duration can call problem.matrix."""
    class _Stub:
        pass
    stub = _Stub()
    stub.matrix = matrix  # type: ignore[attr-defined]
    return stub
