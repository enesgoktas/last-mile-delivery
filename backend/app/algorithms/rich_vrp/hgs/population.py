"""HGS elite population — two pools (feasible / infeasible) with diversity-aware selection.

Each pool keeps at most `max_size` individuals.  When the pool is over capacity,
the individual with the worst combined (cost rank + diversity rank) score is evicted.
Diversity contribution is the average broken-pairs distance to the k nearest peers.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.algorithms.rich_vrp.domain.models import Solution
from app.algorithms.rich_vrp.hgs.diversity import diversity_contribution
from app.algorithms.rich_vrp.objective.lexicographic import LexicographicObjective

_DEFAULT_POOL_SIZE = 25
_DIVERSITY_WEIGHT = 0.3  # weight of diversity in the eviction criterion


@dataclass
class Individual:
    solution: Solution
    objective: LexicographicObjective
    is_feasible: bool
    _div_contribution: float = field(default=0.0, compare=False, repr=False)


class ElitePool:
    """Single-tier elite pool with diversity-preserving survivor selection."""

    def __init__(self, max_size: int = _DEFAULT_POOL_SIZE) -> None:
        self.max_size = max_size
        self._pool: list[Individual] = []

    def __len__(self) -> int:
        return len(self._pool)

    def __iter__(self):
        return iter(self._pool)

    def add(self, individual: Individual) -> None:
        self._pool.append(individual)
        self._refresh_diversity()
        if len(self._pool) > self.max_size:
            self._prune()

    def best(self) -> Individual | None:
        return min(self._pool, key=lambda ind: ind.objective) if self._pool else None

    def sample(self, rng: random.Random, n: int = 2) -> list[Individual]:
        if len(self._pool) < n:
            return list(self._pool)
        return rng.sample(self._pool, n)

    def _refresh_diversity(self) -> None:
        solutions = [ind.solution for ind in self._pool]
        for ind in self._pool:
            others = [s for s in solutions if s is not ind.solution]
            ind._div_contribution = diversity_contribution(ind.solution, others)

    def _prune(self) -> None:
        n = len(self._pool)
        cost_ranks = _rank([ind.objective for ind in self._pool])
        # Higher div_contribution → better diversity → lower rank
        div_ranks = _rank([-ind._div_contribution for ind in self._pool])
        combined = [
            (1 - _DIVERSITY_WEIGHT) * cost_ranks[i] + _DIVERSITY_WEIGHT * div_ranks[i]
            for i in range(n)
        ]
        worst_idx = combined.index(max(combined))
        self._pool.pop(worst_idx)


class HGSPopulation:
    """Two-pool HGS population: feasible and infeasible elites."""

    def __init__(self, pool_size: int = _DEFAULT_POOL_SIZE) -> None:
        self.feas = ElitePool(max_size=pool_size)
        self.infeas = ElitePool(max_size=pool_size)

    def add(self, individual: Individual) -> None:
        if individual.is_feasible:
            self.feas.add(individual)
        else:
            self.infeas.add(individual)

    def best(self) -> Individual | None:
        feas_best = self.feas.best()
        if feas_best is not None:
            return feas_best
        return self.infeas.best()

    def sample_parents(self, rng: random.Random) -> tuple[Individual | None, Individual | None]:
        """Pick two parents — prefer feasible pool, fall back to infeasible."""
        pool = self.feas if len(self.feas) >= 2 else self.infeas
        if len(pool) < 2:
            # Mix from both pools
            all_inds = list(self.feas) + list(self.infeas)
            if len(all_inds) < 2:
                return (all_inds[0] if all_inds else None, None)
            pair = rng.sample(all_inds, 2)
            return pair[0], pair[1]
        pair = pool.sample(rng, 2)
        return pair[0], pair[1]

    def total_size(self) -> int:
        return len(self.feas) + len(self.infeas)


def _rank(values: list) -> list[float]:
    """Return normalised rank in [0, 1] for each value (lower value → better = lower rank)."""
    n = len(values)
    if n <= 1:
        return [0.0] * n
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    for rank, idx in enumerate(order):
        ranks[idx] = rank / (n - 1)
    return ranks
