"""ALNS working state: wraps a Solution with helper methods for move operations."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from app.algorithms.rich_vrp.domain.models import (
    Problem,
    Request,
    Route,
    Solution,
    VisitKind,
)
from app.algorithms.rich_vrp.feasibility.kernel import evaluate_route
from app.algorithms.rich_vrp.objective.lexicographic import (
    LexicographicObjective,
    objective_for_solution,
)


@dataclass
class ALNSState:
    """Mutable working state for one ALNS iteration."""

    solution: Solution
    problem: Problem
    objective: LexicographicObjective = field(init=False)

    def __post_init__(self) -> None:
        self.objective = objective_for_solution(
            self.solution.routes, self.solution.unserved, self.problem
        )

    def clone(self) -> ALNSState:
        """Deep copy — used before a trial move to keep the current state intact."""
        new_sol = Solution(
            routes=[_clone_route(r) for r in self.solution.routes],
            unserved=set(self.solution.unserved),
        )
        return ALNSState(solution=new_sol, problem=self.problem)

    def recompute_objective(self) -> None:
        self.objective = objective_for_solution(
            self.solution.routes, self.solution.unserved, self.problem
        )

    # ------------------------------------------------------------------
    # Convenience accessors used by destroy and repair operators
    # ------------------------------------------------------------------

    def active_request_ids(self) -> set[str]:
        """IDs of requests currently assigned to a route (not unserved)."""
        return {
            v.request_id
            for route in self.solution.routes
            for v in route.visits
            if v.kind == VisitKind.DELIVERY and v.request_id
        }

    def route_for_request(self, request_id: str) -> Route | None:
        """Return the Route containing request_id, or None if unserved."""
        for route in self.solution.routes:
            for visit in route.visits:
                if visit.request_id == request_id and visit.kind == VisitKind.DELIVERY:
                    return route
        return None

    def remove_request(self, request_id: str) -> Request | None:
        """Remove a request's pickup and delivery visits from its route.

        Re-evaluates the route in place.  Returns the Request object,
        or None if the request was not found in any route.
        """
        try:
            req = self.problem.request(request_id)
        except KeyError:
            return None

        for route in self.solution.routes:
            before = len(route.visits)
            route.visits = [
                v for v in route.visits
                if v.request_id != request_id
            ]
            if len(route.visits) < before:
                evaluate_route(route, self.problem)
                self.solution.unserved.discard(request_id)
                return req
        return None

    def add_unserved(self, request_id: str) -> None:
        self.solution.unserved.add(request_id)


def _clone_route(route: Route) -> Route:
    return Route(
        vehicle=route.vehicle,
        visits=copy.deepcopy(route.visits),
        total_distance_m=route.total_distance_m,
        total_duration_s=route.total_duration_s,
        cost=copy.copy(route.cost),
        feasible=route.feasible,
    )
