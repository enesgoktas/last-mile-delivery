"""Incremental delta-cost evaluator for local search moves.

The evaluator computes the change in route cost when a move is applied,
without performing a full O(n) evaluate_route call on the whole route.

For M3 this is a thin wrapper around evaluate_route; the hot-path
optimisation (segment cache, forward/backward propagation) lives in
feasibility/cache.py and is wired in here.
"""
from __future__ import annotations

import copy

from app.algorithms.rich_vrp.domain.models import Problem, Route, Visit
from app.algorithms.rich_vrp.feasibility.kernel import evaluate_route


class DeltaEvaluator:
    """Evaluates the delta cost of candidate moves on routes."""

    def __init__(self, problem: Problem) -> None:
        self.problem = problem

    def cost_after_visits(self, vehicle, visits: list[Visit]) -> int:
        """Return generalized cost for a route with the given visit list."""
        trial = Route(
            vehicle=vehicle,
            visits=visits,
            total_distance_m=0,
            total_duration_s=0,
        )
        evaluate_route(trial, self.problem)
        return trial.cost.total_generalized_cost

    def is_feasible_visits(self, vehicle, visits: list[Visit]) -> bool:
        """Return True if the visit list is feasible (no hard violations)."""
        from app.algorithms.rich_vrp.feasibility.kernel import Feasibility
        trial = Route(
            vehicle=vehicle,
            visits=copy.deepcopy(visits),
            total_distance_m=0,
            total_duration_s=0,
        )
        ev = evaluate_route(trial, self.problem)
        return ev.feasibility != Feasibility.HARD_INFEASIBLE
