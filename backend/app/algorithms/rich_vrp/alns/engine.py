"""Rich ALNS engine — main loop (M5).

Implements the ALNS metaheuristic with:
- Segmented roulette wheel operator selection
- Record-to-Record Travel acceptance (SA as fallback)
- 6 destroy operators, 4 repair operators
- VND local search (M3): relocate, 2-opt, relocate_pair, 2-opt*, exchange,
  cross_exchange, optional_drop, compat_repair
- HGS hybridisation (M4): feasible/infeasible elite pools, SREX crossover,
  adaptive penalty manager
- Route-pool SP recombination + CP-SAT micro-repair (M5)
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)
_LOG_EVERY_N_ITERS = 100

from app.algorithms.rich_vrp.alns.acceptance import RecordToRecordTravel
from app.algorithms.rich_vrp.alns.destroy import (
    compatibility,
    random_remove,
    route_remove,
    shaw,
    time_window,
    worst_cost,
)
from app.algorithms.rich_vrp.alns.operator_selection import SegmentedRouletteWheel
from app.algorithms.rich_vrp.alns.repair import (
    compatibility_first,
    cost_normalized,
    greedy_paired,
    regret_k,
)
from app.algorithms.rich_vrp.alns.state import ALNSState
from app.algorithms.rich_vrp.domain.models import Problem, Route, Solution, VisitKind
from app.algorithms.rich_vrp.hgs.crossover import srex
from app.algorithms.rich_vrp.hgs.penalty import AdaptivePenalty
from app.algorithms.rich_vrp.hgs.population import HGSPopulation, Individual
from app.algorithms.rich_vrp.local_search.candidate_lists import (
    CandidateLists,
    build_candidate_lists,
)
from app.algorithms.rich_vrp.local_search.engine import improve as ls_improve
from app.algorithms.rich_vrp.local_search.evaluator import DeltaEvaluator
from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution
from app.algorithms.rich_vrp.recombination.cp_sat_repair import cp_sat_repair
from app.algorithms.rich_vrp.recombination.route_pool import RoutePool
from app.algorithms.rich_vrp.recombination.sp_repair import sp_repair
from app.algorithms.solver_adapter import SolveOptions

# Operator score constants
_SCORE_BEST = 8
_SCORE_IMPROVED = 4
_SCORE_ACCEPTED = 2
_SCORE_REJECTED = 0

_DESTROY_OPS = [
    random_remove.destroy,
    shaw.destroy,
    worst_cost.destroy,
    route_remove.destroy,
    time_window.destroy,
    compatibility.destroy,
]
_DESTROY_NAMES = ["random", "shaw", "worst_cost", "route", "time_window", "compatibility"]

_REPAIR_OPS = [
    greedy_paired.repair,
    regret_k.repair,
    cost_normalized.repair,
    compatibility_first.repair,
]
_REPAIR_NAMES = ["greedy", "regret_k", "cost_normalized", "compat_first"]


@dataclass(frozen=True)
class ALNSConfig:
    candidate_size: int = 24
    destroy_min_fraction: float = 0.10
    destroy_max_fraction: float = 0.40
    segment_length: int = 100
    decay_theta: float = 0.8
    rrt_start_pct: float = 0.015
    rrt_end_pct: float = 0.0015
    route_pool_cap: int = 5000
    # HGS (M4)
    hgs_pool_size: int = 25
    crossover_interval: int = 200
    # SP recombination (M5)
    sp_interval: int = 1500
    stagnation_threshold: int = 800
    sp_time_limit_s: float = 20.0
    sp_max_routes: int = 500
    # CP-SAT micro-repair (M5)
    cp_sat_time_limit_s: float = 15.0
    cp_sat_max_requests: int = 40
    # Decomposition (M6)
    max_zone_size: int = 80
    # Fleet utilization
    spread_fleet: bool = True

    @classmethod
    def from_options(cls, options: SolveOptions) -> ALNSConfig:
        return cls(spread_fleet=options.spread_fleet)


class RichALNS:
    """Main ALNS engine.  Call run() to solve."""

    def __init__(
        self,
        problem: Problem,
        seeds: list[Solution],
        config: ALNSConfig | None = None,
    ) -> None:
        self.problem = problem
        self.config = config or ALNSConfig()
        self.elapsed_s: float = 0.0
        self.metrics: dict = {}
        self._route_pool: RoutePool = RoutePool(cap=self.config.route_pool_cap)
        self._candidates: CandidateLists = build_candidate_lists(
            problem, k=self.config.candidate_size
        )
        self._evaluator: DeltaEvaluator = DeltaEvaluator(problem)
        self._population: HGSPopulation = HGSPopulation(pool_size=self.config.hgs_pool_size)
        self._penalty: AdaptivePenalty = AdaptivePenalty(window=self.config.segment_length)

        # Pick best seed as initial state
        best_seed = min(
            seeds,
            key=lambda s: objective_for_solution(s.routes, s.unserved, problem),
        )
        self._initial_state = ALNSState(solution=best_seed, problem=problem)

    def run(self, time_budget_s: float, rng: random.Random) -> Solution:
        t_start = time.perf_counter()
        cfg = self.config

        current = self._initial_state.clone()
        best = current.clone()
        best.recompute_objective()
        current.recompute_objective()
        best_feasible = all(r.feasible for r in best.solution.routes)

        # Seed the population with the initial solution
        self._add_to_population(best)

        initial_cost = max(best.objective.generalized_cost, 1)
        acceptance = RecordToRecordTravel(
            initial_cost=initial_cost,
            start_pct=cfg.rrt_start_pct,
            end_pct=cfg.rrt_end_pct,
        )

        n_destroy = len(_DESTROY_OPS)
        n_repair = len(_REPAIR_OPS)
        d_selector = SegmentedRouletteWheel(n_destroy, cfg.segment_length, cfg.decay_theta)
        r_selector = SegmentedRouletteWheel(n_repair, cfg.segment_length, cfg.decay_theta)

        total_iters = 0
        accepted_count = 0
        improving_count = 0
        best_iter = 0
        crossover_count = 0
        sp_count = 0
        cp_sat_count = 0
        iters_since_improvement = 0
        d_uses = [0] * n_destroy
        d_rewards = [0.0] * n_destroy
        r_uses = [0] * n_repair
        r_rewards = [0.0] * n_repair

        while True:
            elapsed = time.perf_counter() - t_start
            if elapsed >= time_budget_s:
                break

            # Update acceptance threshold progress
            acceptance.update_progress(elapsed / time_budget_s)

            # HGS crossover trigger (every crossover_interval iterations)
            if (
                total_iters > 0
                and total_iters % cfg.crossover_interval == 0
                and self._population.total_size() >= 2
            ):
                offspring = self._run_crossover(rng, time_budget_s, t_start)
                if offspring is not None:
                    self._add_to_population(offspring)
                    crossover_count += 1
                    if offspring.objective < best.objective:
                        best = offspring
                        best_iter = total_iters
                        iters_since_improvement = 0

            # SP recombination trigger: every sp_interval iters OR on stagnation
            sp_due = total_iters > 0 and total_iters % cfg.sp_interval == 0
            stagnated = iters_since_improvement >= cfg.stagnation_threshold and total_iters > 0
            if (sp_due or stagnated) and len(self._route_pool) >= 2:
                remaining = time_budget_s - (time.perf_counter() - t_start)
                sp_budget = min(cfg.sp_time_limit_s, remaining * 0.3)
                if sp_budget > 1.0:
                    sp_sol = sp_repair(
                        self._route_pool,
                        self.problem,
                        time_limit_s=sp_budget,
                        max_routes=cfg.sp_max_routes,
                    )
                    if sp_sol is not None:
                        sp_count += 1
                        sp_state = ALNSState(solution=sp_sol, problem=self.problem)
                        sp_state.recompute_objective()
                        # CP-SAT micro-repair on remaining unserved
                        if sp_sol.unserved:
                            remaining2 = time_budget_s - (time.perf_counter() - t_start)
                            cp_budget = min(cfg.cp_sat_time_limit_s, remaining2 * 0.2)
                            if cp_budget > 1.0:
                                repaired = cp_sat_repair(
                                    sp_sol,
                                    self.problem,
                                    time_limit_s=cp_budget,
                                    max_requests=cfg.cp_sat_max_requests,
                                    rng=rng,
                                )
                                if repaired.objective < sp_state.objective:
                                    sp_state = ALNSState(solution=repaired, problem=self.problem)
                                    sp_state.recompute_objective()
                                    cp_sat_count += 1
                        self._add_to_population(sp_state)
                        if sp_state.objective < best.objective:
                            best = sp_state
                            best_iter = total_iters
                            iters_since_improvement = 0
                iters_since_improvement = 0  # reset after SP to avoid thrashing

            # Adaptive penalty adjustment (once per segment)
            if total_iters > 0 and total_iters % cfg.segment_length == 0:
                self._penalty.adjust()

            # 1. Compute destroy count q
            n_served = len(current.active_request_ids())
            if n_served == 0:
                break
            q = rng.randint(
                max(1, int(n_served * cfg.destroy_min_fraction)),
                max(1, int(n_served * cfg.destroy_max_fraction)),
            )

            # 2. Select operators
            d_idx = d_selector.select(rng)
            r_idx = r_selector.select(rng)

            # 3. Destroy (on a clone)
            trial = current.clone()
            trial, removed = _DESTROY_OPS[d_idx](trial, self.problem, q, rng)

            # 4. Repair
            trial = _REPAIR_OPS[r_idx](trial, self.problem, removed, rng)

            # 5. VND local search
            remaining = time_budget_s - (time.perf_counter() - t_start)
            ls_budget = min(remaining * 0.2, 1.0)
            if ls_budget > 0.05:
                ls_improve(trial, self.problem, self._candidates, self._evaluator, max_seconds=ls_budget)
            trial.recompute_objective()

            # Track feasibility for adaptive penalty
            trial_feasible = all(r.feasible for r in trial.solution.routes)
            self._penalty.record(trial_feasible)

            # 6. Acceptance
            # Feasibility-preferred best: only update best with a fully feasible
            # trial when the current best is already feasible. If the current
            # best is infeasible (rare, only when the seed itself was infeasible),
            # any improvement is accepted as best so the engine can climb out.
            # This prevents the lex objective from "preferring" an infeasible
            # trial just because its lateness/cost happens to be lower — the
            # downstream validation would reject it and force a fallback rescue.
            if best_feasible:
                is_new_best = trial_feasible and trial.objective < best.objective
            else:
                is_new_best = trial.objective < best.objective
            is_improved = trial.objective < current.objective
            accepted = is_new_best or is_improved or acceptance.accept(
                trial.objective, current.objective, best.objective
            )

            # 7. Scoring
            if is_new_best:
                score = _SCORE_BEST
                improving_count += 1
                accepted_count += 1
                best = trial.clone()
                best.recompute_objective()
                best_feasible = trial_feasible
                best_iter = total_iters
                iters_since_improvement = 0
                self._add_to_population(best)
                logger.debug(
                    "solver.alns.improved iter=%d elapsed=%.1fs best_lex=%s",
                    total_iters, time.perf_counter() - t_start, list(best.objective.as_tuple()),
                )
            elif is_improved:
                score = _SCORE_IMPROVED
                improving_count += 1
                accepted_count += 1
                iters_since_improvement = 0
                self._add_to_population(trial)
            elif accepted:
                score = _SCORE_ACCEPTED
                accepted_count += 1
                iters_since_improvement += 1
            else:
                score = _SCORE_REJECTED
                iters_since_improvement += 1

            d_selector.update(d_idx, score)
            r_selector.update(r_idx, score)
            d_uses[d_idx] += 1
            d_rewards[d_idx] += score
            r_uses[r_idx] += 1
            r_rewards[r_idx] += score

            if accepted:
                current = trial

            # 8. Route pool accumulation
            self._accumulate_routes(best)

            total_iters += 1

            if total_iters % _LOG_EVERY_N_ITERS == 0:
                elapsed_now = time.perf_counter() - t_start
                logger.info(
                    "solver.alns.heartbeat iter=%d elapsed=%.1fs best_lex=%s "
                    "accepted_ratio=%.3f feasibility=%.3f pool=%d sp=%d cp_sat=%d xover=%d",
                    total_iters, elapsed_now, list(best.objective.as_tuple()),
                    accepted_count / total_iters, self._penalty.feasibility_ratio,
                    len(self._route_pool), sp_count, cp_sat_count, crossover_count,
                )

        # Final best: check HGS population for a better solution.
        # Mirror the in-loop policy: if our `best` is already feasible, only
        # replace with a feasible pop_best; otherwise accept any improvement.
        pop_best = self._population.best()
        if pop_best is not None and pop_best.objective < best.objective:
            pop_best_feasible = all(r.feasible for r in pop_best.solution.routes)
            if (not best_feasible) or pop_best_feasible:
                best = ALNSState(solution=pop_best.solution, problem=self.problem)
                best.recompute_objective()
                best_feasible = pop_best_feasible

        self.elapsed_s = time.perf_counter() - t_start
        active_routes_at_exit = sum(
            1 for r in best.solution.routes
            if any(v.kind == VisitKind.DELIVERY for v in r.visits)
        )
        logger.info(
            "solver.alns.exit iter=%d elapsed=%.1fs accepted=%d improved=%d "
            "best_at=%d sp=%d cp_sat=%d xover=%d active_routes=%d/%d "
            "feas_ratio=%.3f final_lex=%s",
            total_iters, self.elapsed_s, accepted_count, improving_count,
            best_iter, sp_count, cp_sat_count, crossover_count,
            active_routes_at_exit, len(self.problem.vehicles),
            self._penalty.feasibility_ratio, list(best.objective.as_tuple()),
        )
        self.metrics = {
            "total_iterations": total_iters,
            "accepted_count": accepted_count,
            "improving_count": improving_count,
            "best_iteration": best_iter,
            "crossover_count": crossover_count,
            "sp_count": sp_count,
            "cp_sat_count": cp_sat_count,
            "hgs_feas_pool_size": len(self._population.feas),
            "hgs_infeas_pool_size": len(self._population.infeas),
            "feasibility_ratio": self._penalty.feasibility_ratio,
            "destroy_uses": {_DESTROY_NAMES[i]: d_uses[i] for i in range(n_destroy)},
            "repair_uses": {_REPAIR_NAMES[i]: r_uses[i] for i in range(n_repair)},
            "destroy_weights": {_DESTROY_NAMES[i]: d_selector.weights()[i] for i in range(n_destroy)},
            "final_lex_tuple": list(best.objective.as_tuple()),
            "route_pool_size": len(self._route_pool),
        }
        return best.solution

    def _add_to_population(self, state: ALNSState) -> None:
        sol = state.solution
        is_feas = all(r.feasible for r in sol.routes)
        obj = state.objective or objective_for_solution(sol.routes, sol.unserved, self.problem)
        ind = Individual(solution=sol, objective=obj, is_feasible=is_feas)
        self._population.add(ind)

    def _run_crossover(
        self,
        rng: random.Random,
        time_budget_s: float,
        t_start: float,
    ) -> ALNSState | None:
        p1_ind, p2_ind = self._population.sample_parents(rng)
        if p1_ind is None or p2_ind is None:
            return None

        offspring_sol = srex(p1_ind.solution, p2_ind.solution, self.problem, rng)
        offspring_state = ALNSState(solution=offspring_sol, problem=self.problem)
        offspring_state.recompute_objective()

        # Apply LS to offspring
        remaining = time_budget_s - (time.perf_counter() - t_start)
        ls_budget = min(remaining * 0.15, 0.5)
        if ls_budget > 0.05:
            ls_improve(
                offspring_state, self.problem, self._candidates, self._evaluator, max_seconds=ls_budget
            )
            offspring_state.recompute_objective()

        return offspring_state

    def _accumulate_routes(self, state: ALNSState) -> None:
        for route in state.solution.routes:
            self._route_pool.add(route)


# ---------------------------------------------------------------------------
# Kept for backwards compatibility with M2 tests that reference _route_two_opt.
# The real 2-opt implementation now lives in local_search/moves/two_opt.py.
# ---------------------------------------------------------------------------

def _route_two_opt(route: Route, problem: Problem) -> None:
    """Intra-route 2-opt — thin shim; real impl in local_search.moves.two_opt."""
    from app.algorithms.rich_vrp.local_search.moves.two_opt import route_two_opt  # noqa: PLC0415
    route_two_opt(route, problem)
