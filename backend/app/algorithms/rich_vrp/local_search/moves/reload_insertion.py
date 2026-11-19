"""Reload-insertion local-search move (Phase 4 — multi-depot aware).

When a route exceeds vehicle capacity at some position, the kernel
returns HARD_INFEASIBLE with a `kind="capacity"` violation. This move
tries to repair the route by inserting a `VisitKind.RELOAD` visit at
the best (position, allowed-depot) pair the vehicle is permitted to
use. RELOAD visits reset cumulative load to zero, so a single well-
placed insertion can turn an overcapacity route feasible again.

Multi-depot:
  - When `vehicle.allowed_depot_nodes` is empty (the single-depot
    sentinel), only `start_node` is tried — matching pre-multi-depot
    behavior.
  - When populated, every allowed depot is tried at every candidate
    insertion position; the lowest-cost feasible result wins.

This move is also helpful for *cost* improvements when a feasible
route's reload is currently at a sub-optimal depot — e.g. a vehicle
with home depot A and additional depot B running a southbound batch
would prefer to reload at B rather than backtrack to A. The cost-
delta check at the end of the loop handles this case naturally.

PORT-TARGET: mirror into evaluator_cpp/ when the move stabilises and
proves worth the Cython speedup.
"""
from __future__ import annotations

import copy
import time

from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import (
    Problem,
    Route,
    Visit,
    VisitKind,
)
from app.algorithms.rich_vrp.feasibility.kernel import (
    Feasibility,
    evaluate_route,
)
from app.algorithms.rich_vrp.local_search.candidate_lists import CandidateLists
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator


# Beyond this many candidate (position × depot) pairs per route we sort
# depots by distance to the insertion point and only try the k closest.
# Keeps the worst-case work proportional to route length, not
# (route_length × num_depots).
_MAX_DEPOTS_PER_POSITION = 4


def _allowed_depots(route: Route) -> tuple[int, ...]:
    """Resolve the vehicle's allowed-depot set for RELOAD visits."""
    v = route.vehicle
    return v.allowed_depot_nodes or (v.start_node,)


def _nearest_depots(
    problem: Problem,
    insertion_node: int,
    depots: tuple[int, ...],
    k: int,
) -> list[int]:
    """Return up to `k` depots from `depots` sorted by travel duration
    from `insertion_node` (driving profile)."""
    if len(depots) <= k:
        return list(depots)
    matrix = problem.matrix
    return sorted(
        depots,
        key=lambda d: matrix.duration(insertion_node, d, "driving"),
    )[:k]


def _try_insert_reload(
    route: Route,
    problem: Problem,
    position: int,
    depot_node: int,
    require_feasible: bool,
) -> tuple[Route | None, int]:
    """Trial-insert a RELOAD at `position` (node = `depot_node`).

    `require_feasible` reflects the baseline's classification. When the
    baseline route is FEASIBLE, the trial must also be FEASIBLE — a
    SOFT_INFEASIBLE trial would silently downgrade the route by trading
    cost for late-window penalties baked into `total_generalized_cost`.
    When the baseline is HARD_INFEASIBLE (capacity overflow) any
    feasibility class except HARD is acceptable; even SOFT_INFEASIBLE
    is a net improvement.
    """
    trial_visits = list(route.visits)
    trial_visits.insert(
        position,
        Visit(
            request_id=None,
            kind=VisitKind.RELOAD,
            node=depot_node,
            arrival_s=0,
            departure_s=0,
        ),
    )
    trial = Route(
        vehicle=route.vehicle,
        visits=[copy.copy(v) for v in trial_visits],
        total_distance_m=0,
        total_duration_s=0,
    )
    ev = evaluate_route(trial, problem)
    if ev.feasibility == Feasibility.HARD_INFEASIBLE:
        return None, 0
    if require_feasible and ev.feasibility != Feasibility.FEASIBLE:
        return None, 0
    return trial, trial.cost.total_generalized_cost


def _route_is_capacity_infeasible(route: Route, problem: Problem) -> bool:
    """Check whether the current route fails the capacity constraint —
    that's the primary signal that a RELOAD insertion is worth trying."""
    ev = evaluate_route(route, problem)
    if ev.feasibility != Feasibility.HARD_INFEASIBLE:
        return False
    return any(v.kind == "capacity" for v in ev.hard_violations)


def search(
    state: ALNSState,
    problem: Problem,
    candidates: CandidateLists,
    evaluator: DeltaEvaluator,
    max_moves: int = 1,
    deadline_s: float | None = None,
) -> int:
    """Try inserting a RELOAD at the best (position, allowed-depot) pair.

    Returns the number of moves applied (0 or more). First-improving
    within each route; restarts from neighborhood top after every
    successful insertion (the VND engine handles the restart).
    """
    applied = 0

    for route in state.solution.routes:
        if applied >= max_moves:
            break
        if deadline_s is not None and time.perf_counter() >= deadline_s:
            break

        depots = _allowed_depots(route)

        # Candidate positions: anywhere *between* the inner visits
        # (skip 0 which is DEPOT_START and len(visits) which is past
        # DEPOT_END). A RELOAD is only meaningful between pickups/
        # deliveries, not at the very start or end of the route.
        inner_positions = [
            i
            for i, v in enumerate(route.visits)
            if 0 < i < len(route.visits)
            and v.kind not in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)
        ]
        if not inner_positions:
            continue

        # Baseline cost — if the route is already infeasible, treat the
        # baseline as "infinite cost" so any feasible insertion improves.
        ev_baseline = evaluate_route(route, problem)
        if ev_baseline.feasibility == Feasibility.HARD_INFEASIBLE:
            baseline_cost = float("inf")
            require_feasible_trial = False
        else:
            baseline_cost = float(route.cost.total_generalized_cost)
            # Don't trade soft violations for cost — a feasible baseline
            # must stay at FEASIBLE after the move.
            require_feasible_trial = (
                ev_baseline.feasibility == Feasibility.FEASIBLE
            )

        best: tuple[float, int, int] | None = None  # (cost, position, depot)
        for pos in inner_positions:
            insertion_node = route.visits[pos].node
            for depot in _nearest_depots(
                problem, insertion_node, depots, _MAX_DEPOTS_PER_POSITION
            ):
                trial, new_cost = _try_insert_reload(
                    route, problem, pos, depot, require_feasible_trial
                )
                if trial is None:
                    continue
                if new_cost >= baseline_cost:
                    continue
                if best is None or new_cost < best[0]:
                    best = (float(new_cost), pos, depot)

            if deadline_s is not None and time.perf_counter() >= deadline_s:
                break

        if best is not None:
            _, pos, depot = best
            route.visits.insert(
                pos,
                Visit(
                    request_id=None,
                    kind=VisitKind.RELOAD,
                    node=depot,
                    arrival_s=0,
                    departure_s=0,
                ),
            )
            evaluate_route(route, problem)
            applied += 1

    return applied
