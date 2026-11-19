"""Broken-pairs diversity metric for HGS population management.

The broken-pairs distance counts the number of consecutive delivery-pair
adjacencies that differ between two solutions, normalised by the total
number of adjacencies.  It is the standard diversity measure used in HGS
(Vidal et al., 2022).
"""
from __future__ import annotations

from app.algorithms.rich_vrp.domain.models import Solution, VisitKind

_DIV_K = 5  # nearest-neighbour count for diversity contribution


def adjacency_set(solution: Solution) -> frozenset[tuple[str, str]]:
    """Extract the set of consecutive delivery-request adjacency pairs."""
    pairs: set[tuple[str, str]] = set()
    for route in solution.routes:
        deliveries = [
            v.request_id
            for v in route.visits
            if v.kind == VisitKind.DELIVERY and v.request_id
        ]
        for i in range(len(deliveries) - 1):
            pairs.add((deliveries[i], deliveries[i + 1]))
    return frozenset(pairs)


def broken_pairs_distance(sol_a: Solution, sol_b: Solution) -> float:
    """Symmetric-difference broken-pairs distance in [0, 1]."""
    pa = adjacency_set(sol_a)
    pb = adjacency_set(sol_b)
    total = len(pa) + len(pb)
    if total == 0:
        return 0.0
    return len(pa.symmetric_difference(pb)) / total


def diversity_contribution(target: Solution, pool_solutions: list[Solution]) -> float:
    """Average broken-pairs distance to the k nearest individuals in the pool."""
    if not pool_solutions:
        return 1.0
    distances = sorted(broken_pairs_distance(target, other) for other in pool_solutions)
    k = min(_DIV_K, len(distances))
    return sum(distances[:k]) / k if k else 0.0
