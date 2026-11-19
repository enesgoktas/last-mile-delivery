"""Human-dispatcher baseline solver adapter."""
from __future__ import annotations

from app.algorithms.human_dispatcher_baseline import solve_human_dispatcher_baseline
from app.algorithms.solver_adapter import SolveOptions, SolverAdapter, SolveResult
from app.algorithms.vrp_solver import VRPInput


class HumanDispatcherBaselineAdapter:
    """Wraps the deterministic human-style sweep heuristic."""

    name: str = "human_dispatcher"

    def solve(self, problem: VRPInput, options: SolveOptions) -> SolveResult:
        solution = solve_human_dispatcher_baseline(problem)
        return SolveResult(
            solution=solution,
            solver_kind=self.name,
            elapsed_s=0.0,
            metrics={"solver": "human_dispatcher_baseline"},
        )

    def supports(self, problem: VRPInput) -> bool:
        return True

    def explain_limits(self, problem: VRPInput) -> list[str]:
        return []


# Satisfy the Protocol at import time (runtime_checkable)
assert isinstance(HumanDispatcherBaselineAdapter(), SolverAdapter)
