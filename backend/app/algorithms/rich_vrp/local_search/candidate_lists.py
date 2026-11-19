"""Candidate neighbour lists — k-nearest nodes by travel duration per profile."""
from __future__ import annotations

from dataclasses import dataclass

from app.algorithms.rich_vrp.domain.models import Problem


@dataclass(frozen=True)
class CandidateLists:
    """Precomputed k-nearest neighbour lists for each node."""
    _lists: dict[int, list[int]]   # node -> sorted list of k nearest nodes (by duration)
    k: int

    def neighbours(self, node: int) -> list[int]:
        return self._lists.get(node, [])


def build_candidate_lists(problem: Problem, k: int = 24) -> CandidateLists:
    """Build k-nearest neighbour lists from the problem matrix.

    Uses the driving-profile duration matrix. Computed once per Problem
    at engine start and shared read-only across all moves.
    """
    matrix = problem.matrix
    n = len(problem.locations)
    lists: dict[int, list[int]] = {}
    for i in range(n):
        distances = [(matrix.duration(i, j, "driving"), j) for j in range(n) if j != i]
        distances.sort()
        lists[i] = [j for _, j in distances[:k]]
    return CandidateLists(_lists=lists, k=k)
