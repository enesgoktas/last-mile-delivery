"""CP-SAT micro-repair for small unserved sets (M5).

Strategy:
  1. For each unserved request, enumerate which vehicles can legally carry it
     (compatibility, capacity, optionality).
  2. Build a CP-SAT model that assigns requests to vehicles, respecting capacity.
  3. After assignment, greedily sequence each request into its assigned vehicle's
     route via feasible_insertion_positions.

The CP-SAT model maximises the number of requests served (weighted by priority).
This is a fast heuristic, not exact — the sequencing is greedy after assignment.
"""
from __future__ import annotations

import copy
import random

from app.algorithms.rich_vrp.constructive.greedy_seed import _apply_insertion
from app.algorithms.rich_vrp.domain.models import Problem, Solution, VisitKind
from app.algorithms.rich_vrp.feasibility.kernel import feasible_insertion_positions
from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution

_MAX_REQUESTS = 40
_PRIORITY_WEIGHT = {"URGENT": 8, "HIGH": 4, "NORMAL": 2, "LOW": 1}


def _priority_weight(request_id: str, problem: Problem) -> int:
    try:
        req = problem.request(request_id)
        return _PRIORITY_WEIGHT.get(req.priority.value if hasattr(req.priority, "value") else str(req.priority), 2)
    except KeyError:
        return 2


def _vehicle_can_carry(vehicle, req, problem: Problem) -> bool:
    from app.algorithms.rich_vrp.feasibility.kernel import is_compatible  # noqa: PLC0415
    return is_compatible(req, vehicle)


def cp_sat_repair(
    solution: Solution,
    problem: Problem,
    time_limit_s: float = 15.0,
    max_requests: int = _MAX_REQUESTS,
    rng: random.Random | None = None,
) -> Solution:
    """Try to insert unserved requests back into existing routes.

    Returns the (potentially improved) solution.  Never makes the solution worse —
    if no improvement is found, the original solution is returned unchanged.
    """
    if not solution.unserved:
        return solution

    try:
        from ortools.sat.python import cp_model  # noqa: PLC0415
    except ImportError:
        return solution

    rng = rng or random.Random()

    # Limit to max_requests — sort by priority (highest first)
    candidates = sorted(
        solution.unserved,
        key=lambda rid: -_priority_weight(rid, problem),
    )[:max_requests]

    # For each request, build the list of compatible vehicles by index
    feasible_vehicles: dict[str, list[int]] = {}
    for rid in candidates:
        try:
            req = problem.request(rid)
        except KeyError:
            continue
        fv = [
            v.index for v in problem.vehicles
            if _vehicle_can_carry(v, req, problem)
        ]
        if fv:
            feasible_vehicles[rid] = fv

    if not feasible_vehicles:
        return solution

    # Build CP-SAT assignment model
    model = cp_model.CpModel()
    # x[rid][v_idx]: bool — request rid assigned to vehicle v_idx
    x: dict[str, dict[int, cp_model.IntVar]] = {}
    for rid, vids in feasible_vehicles.items():
        x[rid] = {v: model.NewBoolVar(f"x_{rid}_{v}") for v in vids}

    # Each request assigned to at most one vehicle
    for _rid, var_map in x.items():
        model.AddAtMostOne(var_map.values())

    # Capacity constraints per vehicle (rough: just count by weight_g)
    vehicle_by_idx = {v.index: v for v in problem.vehicles}
    for v_idx, vehicle in vehicle_by_idx.items():
        cap = vehicle.capacity.get("weight_g", 0)
        if cap > 0:
            load_vars = []
            for rid, var_map in x.items():
                if v_idx in var_map:
                    try:
                        req = problem.request(rid)
                        load_vars.append((req.load.get("weight_g", 0), var_map[v_idx]))
                    except KeyError:
                        pass
            if load_vars:
                existing_load = sum(
                    v.cumulative_load.get("weight_g", 0)
                    for route in solution.routes
                    if route.vehicle.index == v_idx
                    for v in route.visits
                    if v.kind == VisitKind.DEPOT_END
                )
                remaining_cap = cap - existing_load
                if remaining_cap >= 0:
                    model.Add(
                        sum(w * var for w, var in load_vars) <= remaining_cap
                    )

    # Objective: maximise weighted served count
    model.Maximize(sum(
        _priority_weight(rid, problem) * var
        for rid, var_map in x.items()
        for var in var_map.values()
    ))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.log_search_progress = False
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return solution

    # Build assignment: rid -> vehicle_index
    assignment: dict[str, int] = {}
    for rid, var_map in x.items():
        for v_idx, var in var_map.items():
            if solver.Value(var):
                assignment[rid] = v_idx
                break

    if not assignment:
        return solution

    # Greedy sequencing: insert each assigned request into its vehicle's route
    new_routes = [copy.deepcopy(r) for r in solution.routes]
    route_by_vehicle: dict[int, int] = {r.vehicle.index: i for i, r in enumerate(new_routes)}

    newly_served: set[str] = set()
    for rid, v_idx in assignment.items():
        if v_idx not in route_by_vehicle:
            continue
        r_idx = route_by_vehicle[v_idx]
        route = new_routes[r_idx]
        try:
            req = problem.request(rid)
        except KeyError:
            continue

        best_cost = None
        best_pu = best_de = None
        for pu, de in feasible_insertion_positions(route, req, problem):
            trial = copy.deepcopy(route)
            _apply_insertion(trial, req, pu, de, problem)
            cost = trial.cost.total_generalized_cost
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best_pu, best_de = pu, de
            break  # greedy: first feasible

        if best_pu is not None:
            _apply_insertion(route, req, best_pu, best_de, problem)
            newly_served.add(rid)

    if not newly_served:
        return solution

    new_unserved = solution.unserved - newly_served
    obj = objective_for_solution(new_routes, new_unserved, problem)
    return Solution(routes=new_routes, unserved=new_unserved, objective=obj)
