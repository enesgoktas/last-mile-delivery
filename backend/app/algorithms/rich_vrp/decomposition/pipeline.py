"""Decomposition pipeline for large instances (M6).

Pipeline for instances with > DECOMP_THRESHOLD requests:

  1. Cluster requests into geographic zones.
  2. Solve each zone with ALNS (sequential; parallelism is a future M7+ opt).
  3. Aggregate all zone route pools into a global pool.
  4. Run global SP recombination over the pool.
  5. Run border repair on cross-zone seams.

The pipeline falls back to a single-zone ALNS solve for small instances.
"""
from __future__ import annotations

import copy
import logging
import random
import time
from dataclasses import dataclass, field

from app.algorithms.rich_vrp.decomposition.clustering import (
    DECOMP_THRESHOLD,
    ClusterConfig,
    cluster_problem,
)
from app.algorithms.rich_vrp.domain.models import Problem, Solution, VisitKind
from app.algorithms.rich_vrp.matrices.provider import build_zone_problem
from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution
from app.algorithms.rich_vrp.recombination.border_repair import border_repair
from app.algorithms.rich_vrp.recombination.route_pool import RoutePool
from app.algorithms.rich_vrp.recombination.sp_repair import sp_repair

logger = logging.getLogger(__name__)


@dataclass
class DecompResult:
    """Return type for decompose_and_solve — bundles solution with aggregated metrics."""
    solution: Solution
    metrics: dict = field(default_factory=dict)


def decompose_and_solve(
    problem: Problem,
    time_budget_s: float,
    rng: random.Random,
    config=None,
    cluster_config: ClusterConfig | None = None,
) -> DecompResult:
    """Full decomposition pipeline.

    Falls back to a single ALNS solve for small problems.
    """
    from app.algorithms.rich_vrp.alns.engine import ALNSConfig, RichALNS  # noqa: PLC0415
    from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy  # noqa: PLC0415

    cfg: ALNSConfig = config or ALNSConfig()

    if len(problem.requests) <= DECOMP_THRESHOLD:
        seed = seed_greedy(problem, candidate_size=cfg.candidate_size)
        engine = RichALNS(problem=problem, seeds=[seed], config=cfg)
        sol = engine.run(time_budget_s=time_budget_s, rng=rng)
        return DecompResult(solution=sol, metrics={**engine.metrics, "decomposed": False})

    # Step 1: Cluster
    cc = cluster_config or ClusterConfig(max_zone_size=getattr(cfg, "max_zone_size", 80))
    clusters = cluster_problem(problem, cc)

    if not clusters:
        seed = seed_greedy(problem, candidate_size=cfg.candidate_size)
        engine = RichALNS(problem=problem, seeds=[seed], config=cfg)
        sol = engine.run(time_budget_s=time_budget_s, rng=rng)
        return DecompResult(solution=sol, metrics={**engine.metrics, "decomposed": False})

    # Step 2: Zone solves
    # Budget split: 60% for zone solves, 20% for global SP, 10% for border, 10% buffer
    n_zones = len(clusters)
    zone_budget = (time_budget_s * 0.60) / n_zones
    sp_budget = time_budget_s * 0.20
    border_budget = time_budget_s * 0.10

    logger.info(
        "pipeline.decompose start n_requests=%d n_zones=%d zone_budget=%.1fs",
        len(problem.requests), n_zones, zone_budget,
    )

    global_pool = RoutePool(cap=cfg.route_pool_cap)
    zone_solutions: list[Solution] = []
    zone_metrics_list: list[dict] = []

    for cluster in clusters:
        zone_problem = build_zone_problem(problem, cluster)
        if not zone_problem.requests or not zone_problem.vehicles:
            continue
        effective_budget = max(zone_budget, 1.0)
        logger.info(
            "pipeline.zone_solve start zone=%d requests=%d vehicles=%d budget=%.1fs",
            cluster.cluster_id, len(zone_problem.requests), len(zone_problem.vehicles), effective_budget,
        )
        seed = seed_greedy(zone_problem, candidate_size=min(cfg.candidate_size, 12))
        zone_engine = RichALNS(problem=zone_problem, seeds=[seed], config=cfg)
        t_zone = time.perf_counter()
        zone_sol = zone_engine.run(time_budget_s=effective_budget, rng=rng)
        zone_elapsed = time.perf_counter() - t_zone
        zone_overtime = zone_elapsed > effective_budget * 1.2
        zone_metrics_list.append({
            **zone_engine.metrics,
            "zone_id": cluster.cluster_id,
            "zone_requests": len(zone_problem.requests),
            "zone_vehicles": len(zone_problem.vehicles),
            "zone_elapsed_s": zone_elapsed,
            "zone_overtime": zone_overtime,
        })
        logger.info(
            "pipeline.zone_solve done zone=%d elapsed=%.1fs overtime=%s unserved=%d",
            cluster.cluster_id, zone_elapsed, zone_overtime, len(zone_sol.unserved),
        )
        zone_solutions.append(zone_sol)
        for route in zone_sol.routes:
            global_pool.add(route)

    # Step 3: Merge zone solutions into a global solution
    merged = _merge_zone_solutions(zone_solutions, problem)

    # Step 4: Global SP recombination
    if len(global_pool) >= 2 and sp_budget > 1.0:
        sp_sol = sp_repair(global_pool, problem, time_limit_s=sp_budget, max_routes=cfg.sp_max_routes)
        if sp_sol is not None and sp_sol.objective <= merged.objective:
            merged = sp_sol

    # Step 5: Border repair
    if clusters and border_budget > 1.0:
        repaired = border_repair(merged, problem, clusters=clusters, time_budget_s=border_budget, rng=rng)
        if repaired.objective <= merged.objective:
            merged = repaired

    agg = _aggregate_zone_metrics(zone_metrics_list)
    logger.info(
        "pipeline.decompose done zones=%d total_iters=%d any_overtime=%s unserved=%d",
        agg.get("zone_count", 0), agg.get("total_iterations", 0),
        agg.get("any_zone_overtime", False), len(merged.unserved),
    )
    return DecompResult(solution=merged, metrics=agg)


def _merge_zone_solutions(zone_solutions: list[Solution], problem: Problem) -> Solution:
    """Combine routes from all zone solutions into one global Solution."""
    if not zone_solutions:
        all_unserved = {req.id for req in problem.requests}
        obj = objective_for_solution([], all_unserved, problem)
        return Solution(routes=[], unserved=all_unserved, objective=obj)

    all_routes = []
    all_served: set[str] = set()

    for zone_sol in zone_solutions:
        for route in zone_sol.routes:
            all_routes.append(copy.deepcopy(route))
        for route in zone_sol.routes:
            for visit in route.visits:
                if visit.kind == VisitKind.DELIVERY and visit.request_id:
                    all_served.add(visit.request_id)

    all_request_ids = {req.id for req in problem.requests}
    unserved = all_request_ids - all_served
    # Also include unserved from zone solutions
    for zone_sol in zone_solutions:
        unserved |= zone_sol.unserved

    obj = objective_for_solution(all_routes, unserved, problem)
    return Solution(routes=all_routes, unserved=unserved, objective=obj)


def _aggregate_zone_metrics(zone_metrics_list: list[dict]) -> dict:
    """Roll up per-zone ALNS metrics into a single dict for the adapter layer."""
    if not zone_metrics_list:
        return {"zone_count": 0, "decomposed": True}

    iters_per_zone = [m.get("total_iterations", 0) for m in zone_metrics_list]
    total_iters = sum(iters_per_zone)
    total_weight = max(total_iters, 1)

    avg_feas = sum(
        m.get("feasibility_ratio", 0.0) * iters_per_zone[i]
        for i, m in enumerate(zone_metrics_list)
    ) / total_weight

    return {
        "decomposed": True,
        "zone_count": len(zone_metrics_list),
        "total_iterations": total_iters,
        "accepted_count": sum(m.get("accepted_count", 0) for m in zone_metrics_list),
        "improving_count": sum(m.get("improving_count", 0) for m in zone_metrics_list),
        "feasibility_ratio": avg_feas,
        "crossover_count": sum(m.get("crossover_count", 0) for m in zone_metrics_list),
        "sp_count": sum(m.get("sp_count", 0) for m in zone_metrics_list),
        "cp_sat_count": sum(m.get("cp_sat_count", 0) for m in zone_metrics_list),
        "route_pool_size": sum(m.get("route_pool_size", 0) for m in zone_metrics_list),
        "zone_overtime_flags": [m.get("zone_overtime", False) for m in zone_metrics_list],
        "any_zone_overtime": any(m.get("zone_overtime", False) for m in zone_metrics_list),
        "zone_elapsed_s": [m.get("zone_elapsed_s", 0.0) for m in zone_metrics_list],
    }
