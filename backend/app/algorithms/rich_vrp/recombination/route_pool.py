"""Route pool for set-partitioning recombination (M5).

Accumulates feasible routes produced by the ALNS engine across all iterations.
Deduplicates by (request_set_hash, vehicle_index) so we never store two routes
serving the same requests on the same vehicle.  When the pool is full, new routes
only displace existing ones if they are cheaper (same key but lower cost).
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

from app.algorithms.rich_vrp.domain.models import Route, VisitKind

_DEFAULT_CAP = 5000


@dataclass
class PooledRoute:
    """A frozen snapshot of one route for use in the SP model."""
    request_ids: frozenset[str]
    vehicle_physical_id: str
    vehicle_index: int
    cost: int
    visits: list = field(repr=False)  # deep-copied Visit list


class RoutePool:
    """Deduplicating pool of feasible routes.

    A route is identified by the pair (frozenset[request_ids], vehicle_index).
    If a cheaper route with the same key arrives, it replaces the existing one.
    """

    def __init__(self, cap: int = _DEFAULT_CAP) -> None:
        self._cap = cap
        self._routes: list[PooledRoute] = []
        self._index: dict[tuple[frozenset, int], int] = {}  # key -> position in _routes

    def add(self, route: Route) -> None:
        rids = frozenset(
            v.request_id
            for v in route.visits
            if v.kind == VisitKind.DELIVERY and v.request_id
        )
        if not rids:
            return
        key = (rids, route.vehicle.index)
        cost = route.cost.total_generalized_cost

        if key in self._index:
            pos = self._index[key]
            if cost < self._routes[pos].cost:
                self._routes[pos] = PooledRoute(
                    request_ids=rids,
                    vehicle_physical_id=route.vehicle.physical_id,
                    vehicle_index=route.vehicle.index,
                    cost=cost,
                    visits=copy.deepcopy(route.visits),
                )
            return

        if len(self._routes) >= self._cap:
            return

        pr = PooledRoute(
            request_ids=rids,
            vehicle_physical_id=route.vehicle.physical_id,
            vehicle_index=route.vehicle.index,
            cost=cost,
            visits=copy.deepcopy(route.visits),
        )
        self._index[key] = len(self._routes)
        self._routes.append(pr)

    def sample_for_sp(self, max_routes: int = 500, rng: random.Random | None = None) -> list[PooledRoute]:
        """Return a representative subset for the SP model.

        Prefers cheaper routes but includes a random draw to maintain diversity.
        """
        if len(self._routes) <= max_routes:
            return list(self._routes)
        # Take the cheapest half and random-sample the rest.
        sorted_routes = sorted(self._routes, key=lambda r: r.cost)
        cheap_n = max_routes // 2
        cheap = sorted_routes[:cheap_n]
        rest = sorted_routes[cheap_n:]
        rng = rng or random.Random()
        diverse = rng.sample(rest, min(max_routes - cheap_n, len(rest)))
        return cheap + diverse

    def __len__(self) -> int:
        return len(self._routes)
