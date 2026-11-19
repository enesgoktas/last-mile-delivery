"""Rolling horizon planner — batch, tactical, and reactive modes (M6).

Three planning modes:

  batch    — Full solve from scratch, with decomposition for large instances.
  tactical — Replan only the routes that have not yet started (locked routes
             are frozen in place) within a 15–60 min window.
  reactive — Event-triggered local repair: a small set of requests is added
             or removed; only affected routes are re-optimised.

The batch and tactical modes use the ALNS engine (with optional decomposition).
The reactive mode uses CP-SAT micro-repair for fast turnaround.
"""
from __future__ import annotations

import copy
import random

from app.algorithms.rich_vrp.domain.models import Problem, Route, Solution, VisitKind
from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution

# Re-import once (avoids circular imports at module level)
_DECOMP_THRESHOLD = 200


def solve_batch(
    problem: Problem,
    time_budget_s: float,
    rng: random.Random,
    config=None,
) -> Solution:
    """Full ALNS solve with optional decomposition for large instances."""
    if len(problem.requests) > _DECOMP_THRESHOLD:
        from app.algorithms.rich_vrp.decomposition.pipeline import (
            decompose_and_solve,  # noqa: PLC0415
        )
        return decompose_and_solve(problem, time_budget_s=time_budget_s, rng=rng, config=config).solution

    from app.algorithms.rich_vrp.alns.engine import ALNSConfig, RichALNS  # noqa: PLC0415
    from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy  # noqa: PLC0415

    cfg = config or ALNSConfig()
    seed = seed_greedy(problem, candidate_size=cfg.candidate_size)
    engine = RichALNS(problem=problem, seeds=[seed], config=cfg)
    return engine.run(time_budget_s=time_budget_s, rng=rng)


def solve_tactical(
    problem: Problem,
    current_solution: Solution,
    locked_vehicle_ids: set[str],
    time_budget_s: float,
    rng: random.Random,
    config=None,
) -> Solution:
    """Replan un-started routes, keeping in-progress routes frozen.

    Parameters
    ----------
    problem:
        Full problem instance (may include new/updated requests).
    current_solution:
        The solution currently being executed.
    locked_vehicle_ids:
        Physical vehicle IDs whose routes must not change (drivers are
        already en route or have started their first delivery).
    time_budget_s:
        Wall-clock budget for the replanning pass.
    """
    from app.algorithms.rich_vrp.alns.engine import ALNSConfig, RichALNS  # noqa: PLC0415
    from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy  # noqa: PLC0415

    # Split solution into locked and free parts
    locked_routes: list[Route] = []
    locked_request_ids: set[str] = set()

    for route in current_solution.routes:
        if route.vehicle.physical_id in locked_vehicle_ids:
            locked_routes.append(copy.deepcopy(route))
            for visit in route.visits:
                if visit.kind == VisitKind.DELIVERY and visit.request_id:
                    locked_request_ids.add(visit.request_id)

    # Build a sub-problem with only free requests
    free_requests = [r for r in problem.requests if r.id not in locked_request_ids]
    free_vehicles = [v for v in problem.vehicles if v.physical_id not in locked_vehicle_ids]

    if not free_requests or not free_vehicles:
        return current_solution

    from app.algorithms.rich_vrp.domain.models import Problem as _Problem  # noqa: PLC0415
    sub_problem = _Problem(
        requests=free_requests,
        vehicles=free_vehicles,
        matrix=problem.matrix,
        locations=problem.locations,
        service_date_anchor=problem.service_date_anchor,
        spread_fleet=problem.spread_fleet,
    )

    cfg = config or ALNSConfig()
    seed = seed_greedy(sub_problem, candidate_size=cfg.candidate_size)
    engine = RichALNS(problem=sub_problem, seeds=[seed], config=cfg)
    free_sol = engine.run(time_budget_s=time_budget_s, rng=rng)

    # Merge locked routes back in
    combined_routes = locked_routes + free_sol.routes
    combined_unserved = free_sol.unserved | current_solution.unserved - locked_request_ids
    obj = objective_for_solution(combined_routes, combined_unserved, problem)
    return Solution(routes=combined_routes, unserved=combined_unserved, objective=obj)


def solve_reactive(
    problem: Problem,
    current_solution: Solution,
    added_request_ids: set[str],
    removed_request_ids: set[str],
    rng: random.Random,
    config=None,
) -> Solution:
    """Fast local repair for order additions/cancellations.

    Uses CP-SAT micro-repair to insert new requests and greedy removal for
    cancelled ones.  Runs in seconds rather than minutes.
    """
    from app.algorithms.rich_vrp.recombination.cp_sat_repair import cp_sat_repair  # noqa: PLC0415

    cp_time = 15.0
    cp_max = 40

    # Step 1: Remove cancelled requests from routes
    new_routes = [copy.deepcopy(r) for r in current_solution.routes]
    if removed_request_ids:
        from app.algorithms.rich_vrp.feasibility.kernel import evaluate_route  # noqa: PLC0415
        for route in new_routes:
            route.visits = [
                v for v in route.visits
                if not (v.request_id and v.request_id in removed_request_ids)
                or v.kind in (VisitKind.DEPOT_START, VisitKind.DEPOT_END)
            ]
            evaluate_route(route, problem)

    new_unserved = (current_solution.unserved - removed_request_ids) | added_request_ids
    obj = objective_for_solution(new_routes, new_unserved, problem)
    patched = Solution(routes=new_routes, unserved=new_unserved, objective=obj)

    # Step 2: Insert new requests via CP-SAT micro-repair
    if added_request_ids:
        patched = cp_sat_repair(
            patched,
            problem,
            time_limit_s=cp_time,
            max_requests=cp_max,
            rng=rng,
        )

    return patched
