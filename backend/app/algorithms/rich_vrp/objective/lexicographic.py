"""Lexicographic objective for the rich VRP engine.

Priority ordering (lower is better):
  1. Weighted unserved count  (URGENT >> HIGH >> NORMAL >> LOW)
  2. Lateness minutes + overtime minutes
  3. Generalized economic cost
  4. Route-stability distance (0 in M1; set in M8)
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import OrderPriority

_PRIORITY_WEIGHT: dict[OrderPriority, int] = {
    OrderPriority.URGENT: 10_000,
    OrderPriority.HIGH: 100,
    OrderPriority.NORMAL: 10,
    OrderPriority.LOW: 1,
}

_DELIVERY_SPREAD_COEFFICIENT_TL = 100


@dataclass(frozen=True, slots=True)
class LexicographicObjective:
    """4-tuple ordered lexicographically (lower = better solution)."""

    unserved_count_weighted: int
    lateness_and_overtime_min: int
    generalized_cost: int
    stability_distance: int = 0

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (
            self.unserved_count_weighted,
            self.lateness_and_overtime_min,
            self.generalized_cost,
            self.stability_distance,
        )

    def __lt__(self, other: LexicographicObjective) -> bool:
        return self.as_tuple() < other.as_tuple()

    def __le__(self, other: LexicographicObjective) -> bool:
        return self.as_tuple() <= other.as_tuple()

    def __gt__(self, other: LexicographicObjective) -> bool:
        return self.as_tuple() > other.as_tuple()

    def __ge__(self, other: LexicographicObjective) -> bool:
        return self.as_tuple() >= other.as_tuple()

    def __add__(self, other: LexicographicObjective) -> LexicographicObjective:
        return LexicographicObjective(
            unserved_count_weighted=self.unserved_count_weighted + other.unserved_count_weighted,
            lateness_and_overtime_min=self.lateness_and_overtime_min + other.lateness_and_overtime_min,
            generalized_cost=self.generalized_cost + other.generalized_cost,
            stability_distance=self.stability_distance + other.stability_distance,
        )

    def dominates(self, other: LexicographicObjective) -> bool:
        """Return True if self is strictly better than other on at least one component
        and not worse on any earlier component."""
        self_t = self.as_tuple()
        other_t = other.as_tuple()
        for s, o in zip(self_t, other_t, strict=True):
            if s < o:
                return True
            if s > o:
                return False
        return False


def unserved_weight(priority: OrderPriority) -> int:
    return _PRIORITY_WEIGHT.get(priority, 1)


def objective_for_solution(
    routes: list,
    unserved_ids: set[str],
    problem: object,
) -> LexicographicObjective:
    """Compute the lexicographic objective for a complete solution.

    Imports Problem lazily to avoid circular dependency at module load.
    """
    from app.algorithms.rich_vrp.domain.models import Problem, Route, VisitKind  # noqa: PLC0415

    assert isinstance(problem, Problem)

    unserved_weighted = 0
    for order_id in unserved_ids:
        try:
            req = problem.request(order_id)
            unserved_weighted += unserved_weight(req.priority)
        except KeyError:
            unserved_weighted += 1

    lateness_ot = 0
    total_cost = 0

    for route in routes:
        assert isinstance(route, Route)
        if route.cost:
            lateness_ot += route.cost.late_minutes + route.cost.overtime_minutes
            total_cost += route.cost.total_generalized_cost

    spread_penalty = 0
    if problem.spread_fleet:
        delivery_counts = [
            sum(1 for v in r.visits if v.kind == VisitKind.DELIVERY and v.request_id)
            for r in routes
        ]
        active_counts = [c for c in delivery_counts if c > 0]
        if len(active_counts) >= 2:
            spread_penalty = (max(active_counts) - min(active_counts)) * _DELIVERY_SPREAD_COEFFICIENT_TL

    return LexicographicObjective(
        unserved_count_weighted=unserved_weighted,
        lateness_and_overtime_min=lateness_ot,
        generalized_cost=total_cost + spread_penalty,
        stability_distance=0,
    )
