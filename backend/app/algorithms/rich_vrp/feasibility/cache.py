"""Per-route segment cache for O(1) insertion feasibility checks.

Precomputes forward arrival times and backward latest-departure times
so that M3 local search can evaluate insertions without full re-traversal.

Usage pattern:
    cache = SegmentCache.build(route, problem)
    # After a move, invalidate and rebuild only the affected segment:
    cache.invalidate(from_idx)
    cache.rebuild_forward(from_idx, route, problem)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.algorithms.rich_vrp.domain.models import (
    Problem,
    Route,
    VisitKind,
)


@dataclass
class SegmentCache:
    """Precomputed per-visit forward and backward time bounds for a route.

    forward_arrival[i] = earliest arrival at route.visits[i] assuming optimal dispatch.
    backward_latest[i] = latest arrival at route.visits[i] such that all
                         subsequent time windows are still reachable.
    Both arrays have the same length as route.visits.

    The numpy arrays (nodes_np, tw_open_np, service_np, tw_close_np) are
    kept in sync so the Cython hot path can be called without re-extracting
    them on every move evaluation.
    """

    forward_arrival: list[int] = field(default_factory=list)
    backward_latest: list[int] = field(default_factory=list)
    route_len: int = 0

    # Flat numpy arrays for the Cython hot path (None until build())
    nodes_np: object = None        # int64[n] — matrix node per visit
    tw_open_np: object = None      # int64[n] — TW open per visit (0 = none)
    service_np: object = None      # int64[n] — service time per visit
    tw_close_np: object = None     # int64[n] — TW close per visit (0 = none)

    @classmethod
    def build(cls, route: Route, problem: Problem) -> SegmentCache:
        """Compute the full forward and backward arrays for the route."""
        cache = cls()
        cache._extract_arrays(route, problem)
        cache._compute_forward(route, problem)
        cache._compute_backward(route, problem)
        cache.route_len = len(route.visits)
        return cache

    def invalidate(self, from_idx: int) -> None:
        """Truncate cached arrays at from_idx so rebuild_forward knows what's stale."""
        self.forward_arrival = self.forward_arrival[:from_idx]
        self.backward_latest = self.backward_latest[:from_idx]

    def rebuild_forward(self, from_idx: int, route: Route, problem: Problem) -> None:
        """Recompute forward_arrival from from_idx onward after a route mutation."""
        self._compute_forward(route, problem, start=from_idx)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_arrays(self, route: Route, problem: Problem) -> None:
        """Pre-extract visit data into flat numpy arrays for the Cython path."""
        visits = route.visits
        n = len(visits)
        nodes = np.zeros(n, dtype=np.int64)
        tw_open = np.zeros(n, dtype=np.int64)
        service = np.zeros(n, dtype=np.int64)
        tw_close = np.zeros(n, dtype=np.int64)
        vehicle = route.vehicle

        for i, visit in enumerate(visits):
            nodes[i] = visit.node
            tw_open[i] = _tw_open_val(visit, problem)
            service[i] = _service_time(visit, problem)
            tw_close[i] = _tw_close_val(visit, problem, vehicle.shift_window[1] + 7200)

        self.nodes_np = nodes
        self.tw_open_np = tw_open
        self.service_np = service
        self.tw_close_np = tw_close

    def _compute_forward(self, route: Route, problem: Problem, start: int = 0) -> None:
        """Compute forward_arrival, using the Cython hot path when available."""
        vehicle = route.vehicle

        try:
            from app.algorithms.rich_vrp.local_search.evaluator_cpp import (
                COMPILED,
                forward_pass_c,
            )
            _use_cython = COMPILED
        except ImportError:
            _use_cython = False

        if _use_cython and self.nodes_np is not None and start == 0:
            dur_np = problem.matrix.np_duration
            arrivals = forward_pass_c(
                dur_np,
                self.nodes_np,
                self.tw_open_np,
                self.service_np,
                vehicle.shift_window[0],
            )
            self.forward_arrival = arrivals.tolist()
            return

        # Pure-Python path (also used for partial rebuilds from start > 0)
        matrix = problem.matrix
        arrivals: list[int] = list(self.forward_arrival[:start])

        if start == 0:
            prev_departure = vehicle.shift_window[0]
            prev_node = vehicle.start_node
        else:
            prev_departure = arrivals[start - 1] + _service_time(route.visits[start - 1], problem)
            prev_node = route.visits[start - 1].node

        for i in range(start, len(route.visits)):
            visit = route.visits[i]
            if visit.kind == VisitKind.DEPOT_START:
                arrivals.append(vehicle.shift_window[0])
                prev_node = visit.node
                prev_departure = vehicle.shift_window[0]
                continue

            travel = matrix.duration(prev_node, visit.node, vehicle.profile)
            arrival = prev_departure + travel

            tw_open = _tw_open_val(visit, problem)
            if tw_open is not None and tw_open > 0 and arrival < tw_open:
                arrival = tw_open

            arrivals.append(arrival)
            service = _service_time(visit, problem)
            prev_departure = arrival + service
            prev_node = visit.node

        self.forward_arrival = arrivals

    def _compute_backward(self, route: Route, problem: Problem) -> None:
        """Backward pass: latest arrival at each visit."""
        vehicle = route.vehicle

        try:
            from app.algorithms.rich_vrp.local_search.evaluator_cpp import (
                COMPILED,
                backward_pass_c,
            )
            _use_cython = COMPILED
        except ImportError:
            _use_cython = False

        if _use_cython and self.nodes_np is not None:
            dur_np = problem.matrix.np_duration
            hard_end = vehicle.hard_shift_end if vehicle.hard_shift_end else vehicle.shift_window[1] + 7200
            latest = backward_pass_c(
                dur_np,
                self.nodes_np,
                self.tw_close_np,
                self.service_np,
                hard_end,
            )
            self.backward_latest = latest.tolist()
            return

        # Pure-Python path — same recursive formula as backward_pass_c:
        # latest[i] = latest[i+1] - travel(i, i+1) - service_i
        # capped at the current visit's own TW close (if set).
        matrix = problem.matrix
        n = len(route.visits)
        latest: list[int] = [0] * n
        hard_end = vehicle.hard_shift_end if vehicle.hard_shift_end else vehicle.shift_window[1] + 7200
        latest[n - 1] = hard_end

        for i in range(n - 2, -1, -1):
            visit_i = route.visits[i]
            visit_next = route.visits[i + 1]
            travel = matrix.duration(visit_i.node, visit_next.node, vehicle.profile)
            service_i = _service_time(visit_i, problem)
            lat = latest[i + 1] - travel - service_i
            # Cap at the current visit's own window close (0 = none / depot)
            tw_c = _tw_close_val(visit_i, problem, 0)
            if tw_c > 0 and lat > tw_c:
                lat = tw_c
            latest[i] = lat

        self.backward_latest = latest


# ------------------------------------------------------------------
# Helpers to extract time-window bounds from visits
# ------------------------------------------------------------------

def _tw_open(visit, problem: Problem) -> int | None:
    if visit.request_id is None:
        return None
    try:
        req = problem.request(visit.request_id)
    except KeyError:
        return None
    if visit.kind == VisitKind.PICKUP:
        return req.pickup_window[0]
    if visit.kind == VisitKind.DELIVERY:
        return req.delivery_window[0]
    return None


def _tw_open_val(visit, problem: Problem) -> int:
    """Return TW open as int (0 = no window)."""
    v = _tw_open(visit, problem)
    return v if v is not None else 0


def _tw_close(visit, problem: Problem, default: int) -> int:
    if visit.request_id is None:
        return default
    try:
        req = problem.request(visit.request_id)
    except KeyError:
        return default
    if visit.kind == VisitKind.PICKUP:
        return req.pickup_window[1]
    if visit.kind == VisitKind.DELIVERY:
        hard = req.hard_delivery_deadline
        return hard if hard is not None else req.delivery_window[1]
    return default


def _tw_close_val(visit, problem: Problem, default: int) -> int:
    """Return TW close as int (0 = DEPOT_END / use shift_hard_end)."""
    if visit.kind in (VisitKind.DEPOT_START, VisitKind.DEPOT_END):
        return 0
    return _tw_close(visit, problem, default)


def _service_time(visit, problem: Problem) -> int:
    if visit.request_id is None:
        return 0
    try:
        req = problem.request(visit.request_id)
    except KeyError:
        return 0
    if visit.kind == VisitKind.PICKUP:
        return req.service_time_pickup_s
    if visit.kind == VisitKind.DELIVERY:
        return req.service_time_delivery_s
    return 0
