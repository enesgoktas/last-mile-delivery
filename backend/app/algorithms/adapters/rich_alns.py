"""Rich ALNS + HGS solver adapter (Phase C — OR-Tools removed from runtime path).

M9: OR-Tools is no longer in any production solve path. Failures are visible events,
not silently masked by a fallback. OR-Tools survives only as a comparison scenario
and bench adapter; it is never instantiated here.

M6 rolling horizon entry points:
  solve_tactical(problem, active_solution_internal, locked_vehicle_ids, options)
  solve_reactive(problem, active_solution_internal, added_ids, removed_ids, options)
"""
from __future__ import annotations

import concurrent.futures
import logging
import random
import time

from app.algorithms.solver_adapter import SolveOptions, SolverAdapter, SolveResult
from app.algorithms.vrp_solver import SolverStatus, VRPInput, VRPSolution

logger = logging.getLogger(__name__)

_TIMEOUT_MULTIPLIER = 1.2

# Hard deadline = budget × this factor. If the engine doesn't return by then we
# bail out with a failed solution and let the caller move on (e.g. the next
# scenario in a Compare Routes run). The orphaned thread continues until the
# engine's own iteration-boundary check fires; this is acceptable because the
# alternative is wedging the worker indefinitely.
_HARD_DEADLINE_MULTIPLIER = 1.5
_DEADLINE_FLOOR_S = 5.0


class RichALNSAdapter:
    """Primary production solver adapter (Phase C — no OR-Tools fallback)."""

    name: str = "rich_alns"

    def solve(self, problem: VRPInput, options: SolveOptions) -> SolveResult:
        deadline_s = max(_DEADLINE_FLOOR_S, options.time_budget_s * _HARD_DEADLINE_MULTIPLIER)
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rich_alns_solve"
        )
        try:
            future = executor.submit(_run_real_engine, problem, options, self.name)
            try:
                result = future.result(timeout=deadline_s)
            except concurrent.futures.TimeoutError:
                logger.warning(
                    "rich_alns hard deadline hit (%.1fs > %.1f×%.1fs); "
                    "abandoning solve and returning failed solution",
                    deadline_s, _HARD_DEADLINE_MULTIPLIER, options.time_budget_s,
                )
                logger.warning("solver.engine_failed_no_fallback reason=deadline_exceeded")
                return SolveResult(
                    solution=_failed_solution(problem),
                    solver_kind=self.name,
                    elapsed_s=deadline_s,
                    metrics={"engine_failed": True, "failure_reason": "deadline_exceeded"},
                )
            except Exception:
                logger.exception(
                    "rich_alns engine raised; returning NOT_FOUND (no fallback in Phase C)"
                )
                logger.warning("solver.engine_failed_no_fallback reason=engine_exception")
                return SolveResult(
                    solution=_failed_solution(problem),
                    solver_kind=self.name,
                    elapsed_s=0.0,
                    metrics={"engine_failed": True, "failure_reason": "engine_exception"},
                )
        finally:
            # wait=False: don't block the caller; orphan thread will exit
            # when the engine's own time check fires at iteration boundary.
            executor.shutdown(wait=False)

        if result.elapsed_s > options.time_budget_s * _TIMEOUT_MULTIPLIER:
            logger.warning(
                "rich_alns engine exceeded budget (%.1fs > %.1f×%.1fs); "
                "returning partial result (no fallback in Phase C)",
                result.elapsed_s,
                _TIMEOUT_MULTIPLIER,
                options.time_budget_s,
            )
            logger.warning("solver.engine_failed_no_fallback reason=engine_timeout")
            return SolveResult(
                solution=result.solution,
                solver_kind=self.name,
                elapsed_s=result.elapsed_s,
                metrics={
                    **result.metrics,
                    "engine_overtime": True,
                    "fallback_invoked": False,
                },
            )

        unserved_urgent = _unserved_urgent_ids(problem, result)
        if unserved_urgent:
            logger.warning(
                "rich_alns left %d URGENT request(s) unserved (%s); "
                "no fallback in Phase C",
                len(unserved_urgent),
                unserved_urgent,
            )
            logger.warning("solver.engine_failed_no_fallback reason=unserved_urgent")
            return SolveResult(
                solution=result.solution,
                solver_kind=self.name,
                elapsed_s=result.elapsed_s,
                metrics={
                    **result.metrics,
                    "unserved_urgent_count": len(unserved_urgent),
                    "unserved_urgent_ids": sorted(unserved_urgent),
                    "fallback_invoked": False,
                },
            )

        return SolveResult(
            solution=result.solution,
            solver_kind=self.name,
            elapsed_s=result.elapsed_s,
            metrics={**result.metrics, "fallback_invoked": False},
        )

    def solve_tactical(
        self,
        problem: VRPInput,
        active_solution_internal,
        locked_vehicle_ids: set[str],
        options: SolveOptions,
    ) -> SolveResult:
        """Replan un-started routes, keeping in-progress vehicles frozen."""
        t0 = time.perf_counter()
        from app.algorithms.rich_vrp.alns.engine import ALNSConfig  # noqa: PLC0415
        from app.algorithms.rich_vrp.decomposition.rolling_horizon import (  # noqa: PLC0415
            solve_tactical,
        )
        from app.algorithms.rich_vrp.domain.builders import (  # noqa: PLC0415
            build_problem,
            export_solution,
        )

        internal_problem = build_problem(problem, spread_fleet=options.spread_fleet)
        cfg = ALNSConfig.from_options(options)
        rng = random.Random(options.seed)
        result_internal = solve_tactical(
            internal_problem,
            active_solution_internal,
            locked_vehicle_ids,
            options.time_budget_s,
            rng,
            config=cfg,
        )
        result_external = export_solution(result_internal, problem)
        return SolveResult(
            solution=result_external,
            solver_kind=self.name,
            elapsed_s=time.perf_counter() - t0,
            metrics={"mode": "tactical"},
        )

    def solve_reactive(
        self,
        problem: VRPInput,
        active_solution_internal,
        added_request_ids: set[str],
        removed_request_ids: set[str],
        options: SolveOptions,
    ) -> SolveResult:
        """Fast local repair for order additions/cancellations."""
        t0 = time.perf_counter()
        from app.algorithms.rich_vrp.decomposition.rolling_horizon import (  # noqa: PLC0415
            solve_reactive,
        )
        from app.algorithms.rich_vrp.domain.builders import (  # noqa: PLC0415
            build_problem,
            export_solution,
        )

        internal_problem = build_problem(problem, spread_fleet=options.spread_fleet)
        rng = random.Random(options.seed)
        result_internal = solve_reactive(
            internal_problem,
            active_solution_internal,
            added_request_ids,
            removed_request_ids,
            rng,
        )
        result_external = export_solution(result_internal, problem)
        return SolveResult(
            solution=result_external,
            solver_kind=self.name,
            elapsed_s=time.perf_counter() - t0,
            metrics={
                "mode": "reactive",
                "added": len(added_request_ids),
                "removed": len(removed_request_ids),
            },
        )

    def supports(self, problem: VRPInput) -> bool:
        return True

    def explain_limits(self, problem: VRPInput) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_real_engine(problem: VRPInput, options: SolveOptions, solver_name: str) -> SolveResult:
    from app.algorithms.rich_vrp.alns.engine import ALNSConfig, RichALNS  # noqa: PLC0415
    from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy  # noqa: PLC0415
    from app.algorithms.rich_vrp.decomposition.clustering import DECOMP_THRESHOLD  # noqa: PLC0415
    from app.algorithms.rich_vrp.decomposition.pipeline import decompose_and_solve  # noqa: PLC0415
    from app.algorithms.rich_vrp.domain.builders import (  # noqa: PLC0415
        build_problem,
        export_solution,
        import_solution,
    )

    t0 = time.perf_counter()
    internal_problem = build_problem(problem, spread_fleet=options.spread_fleet)
    _fuel_costs = [v.cost.fuel_per_km for v in internal_problem.vehicles]
    if _fuel_costs:
        _s = sorted(_fuel_costs)
        logger.info(
            "rich_alns.fuel_per_km n=%d min=%d median=%d max=%d zeros=%d",
            len(_s), _s[0], _s[len(_s) // 2], _s[-1],
            sum(1 for c in _s if c == 0),
        )
    logger.info("rich_alns.spread_fleet=%s", options.spread_fleet)
    config = ALNSConfig.from_options(options)
    rng = random.Random(options.seed)

    if len(internal_problem.requests) > DECOMP_THRESHOLD:
        decomp = decompose_and_solve(
            internal_problem,
            time_budget_s=options.time_budget_s,
            rng=rng,
            config=config,
        )
        final_internal = decomp.solution
        engine_metrics: dict = decomp.metrics
    else:
        seeds = [seed_greedy(internal_problem, candidate_size=config.candidate_size)]
        warm_start_used = False
        if options.warm_start_solution is not None:
            try:
                warm_seed = import_solution(options.warm_start_solution, internal_problem)
                seeds.append(warm_seed)
                warm_start_used = True
                logger.info(
                    "rich_alns.warm_start applied: %d routes, %d unserved (vs greedy)",
                    len(warm_seed.routes), len(warm_seed.unserved),
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "rich_alns.warm_start import failed; falling back to greedy seed"
                )
        engine = RichALNS(internal_problem, seeds=seeds, config=config)
        final_internal = engine.run(time_budget_s=options.time_budget_s, rng=rng)
        engine_metrics = engine.metrics
        engine_metrics["warm_start_used"] = warm_start_used

    elapsed = time.perf_counter() - t0
    final_external = export_solution(final_internal, problem)
    return SolveResult(
        solution=final_external,
        solver_kind=solver_name,
        elapsed_s=elapsed,
        metrics=engine_metrics,
    )


def _unserved_urgent_ids(problem: VRPInput, result: SolveResult) -> set[str]:
    """Return order IDs that are URGENT and unserved in result."""
    from app.models.enums import OrderPriority  # noqa: PLC0415
    urgent_ids = {
        o.order_id for o in problem.orders
        if o.priority == OrderPriority.URGENT
    }
    return urgent_ids & set(result.solution.unassigned_orders)


def _failed_solution(problem: VRPInput) -> VRPSolution:
    """Return an empty VRPSolution with all orders unassigned."""
    from app.algorithms.vrp_solver import EconomicCostBreakdown  # noqa: PLC0415
    return VRPSolution(
        routes=[],
        unassigned_orders=[o.order_id for o in problem.orders],
        total_distance_m=0,
        total_duration_s=0,
        solver_status=SolverStatus.NOT_FOUND,
        cost_breakdown=EconomicCostBreakdown(),
    )


assert isinstance(RichALNSAdapter(), SolverAdapter)
