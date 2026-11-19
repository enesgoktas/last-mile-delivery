"""Set-partitioning recombination using OR-Tools CBC (M5).

Builds a covering MIP over the route pool:
  - Binary x[r] for each pooled route r.
  - Binary y[i] slack for each request i (unserved is penalised).
  - Each request covered by exactly one selected route OR allowed to be slack.
  - Each physical vehicle used at most once.
  - Minimize: sum(cost[r]*x[r]) + sum(penalty[i]*y[i]).

The result is a new Solution whose routes come entirely from the pool (if feasible)
with leftover unserved requests filled back in from the original solution.
"""
from __future__ import annotations

import copy

from app.algorithms.rich_vrp.domain.models import Problem, Route, Solution
from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution
from app.algorithms.rich_vrp.recombination.route_pool import PooledRoute, RoutePool

_SOLVER_NAME = "CBC_MIXED_INTEGER_PROGRAMMING"
_UNSERVED_PENALTY_SCALE = 10_000_000  # large enough to dominate route cost


def _unserved_penalty(request_id: str, problem: Problem) -> int:
    try:
        req = problem.request(request_id)
        return req.unserved_penalty or _UNSERVED_PENALTY_SCALE
    except KeyError:
        return _UNSERVED_PENALTY_SCALE


def sp_repair(
    pool: RoutePool,
    problem: Problem,
    time_limit_s: float = 20.0,
    max_routes: int = 500,
) -> Solution | None:
    """Run the SP model and return an improved Solution, or None on failure.

    Returns None if the pool is empty, OR-Tools is unavailable, or the solver
    finds no feasible solution within the time limit.
    """
    try:
        from ortools.linear_solver import pywraplp  # noqa: PLC0415
    except ImportError:
        return None

    candidates = pool.sample_for_sp(max_routes=max_routes)
    if not candidates:
        return None

    all_rids = {req.id for req in problem.requests}
    solver = pywraplp.Solver.CreateSolver(_SOLVER_NAME)
    if solver is None:
        return None
    solver.set_time_limit(int(time_limit_s * 1000))

    # x[r]: 1 if route r is selected
    x: list[pywraplp.Variable] = [solver.BoolVar(f"x{i}") for i in range(len(candidates))]
    # y[i]: 1 if request i is unserved (slack)
    rid_list = sorted(all_rids)
    y: list[pywraplp.Variable] = [solver.BoolVar(f"y{idx}") for idx in range(len(rid_list))]

    # Coverage constraints: for each request, exactly one route covers it OR slack
    coverage: dict[int, pywraplp.Constraint] = {}
    for idx, rid in enumerate(rid_list):
        ct = solver.Add(y[idx] + sum(
            x[r] for r, route in enumerate(candidates) if rid in route.request_ids
        ) == 1)
        coverage[idx] = ct

    # Vehicle-use constraints: each physical vehicle at most once
    vehicle_to_routes: dict[str, list[int]] = {}
    for r, route in enumerate(candidates):
        vid = route.vehicle_physical_id
        vehicle_to_routes.setdefault(vid, []).append(r)
    for _vid, ridxs in vehicle_to_routes.items():
        if len(ridxs) > 1:
            solver.Add(sum(x[r] for r in ridxs) <= 1)

    # Objective
    obj = solver.Objective()
    for r, route in enumerate(candidates):
        obj.SetCoefficient(x[r], route.cost)
    for idx, rid in enumerate(rid_list):
        obj.SetCoefficient(y[idx], _unserved_penalty(rid, problem))
    obj.SetMinimization()

    status = solver.Solve()
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        return None

    # Build solution from selected routes
    selected: list[PooledRoute] = [
        candidates[r] for r in range(len(candidates)) if x[r].solution_value() > 0.5
    ]
    unserved: set[str] = {
        rid_list[idx] for idx in range(len(rid_list)) if y[idx].solution_value() > 0.5
    }

    # Reconstruct Route objects from PooledRoute snapshots
    vehicle_used: set[str] = set()
    sp_routes: list[Route] = []
    for pr in selected:
        vid = pr.vehicle_physical_id
        if vid in vehicle_used:
            continue
        vehicle_used.add(vid)
        try:
            vehicle = problem.vehicle(vid)
        except KeyError:
            continue
        route = Route(vehicle=vehicle, visits=copy.deepcopy(pr.visits))
        route.cost.total_generalized_cost = pr.cost
        sp_routes.append(route)

    obj_val = objective_for_solution(sp_routes, unserved, problem)
    return Solution(routes=sp_routes, unserved=unserved, objective=obj_val)
