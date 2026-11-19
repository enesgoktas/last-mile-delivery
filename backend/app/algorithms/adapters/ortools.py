"""OR-Tools CVRPTW solver adapter."""
from __future__ import annotations

from app.algorithms.solver_adapter import SolveOptions, SolverAdapter, SolveResult
from app.algorithms.vrp_solver import VRPInput, solve


class ORToolsCVRPTWAdapter:
    """Wraps the OR-Tools CVRPTW solver (vrp_solver.solve)."""

    name: str = "ortools_cvrptw"

    def solve(self, problem: VRPInput, options: SolveOptions) -> SolveResult:
        solution = solve(
            problem,
            initial_routes=options.initial_routes,
            time_limit_seconds=int(options.time_budget_s),
            spread_fleet=options.spread_fleet,
        )
        return SolveResult(
            solution=solution,
            solver_kind=self.name,
            elapsed_s=0.0,
            metrics={
                "solver": "ortools_cvrptw",
                "solver_status": solution.solver_status,
                "routing_objective": options.routing_objective,
                "fleet_utilization": options.fleet_utilization,
            },
        )

    def supports(self, problem: VRPInput) -> bool:
        return True

    def explain_limits(self, problem: VRPInput) -> list[str]:
        return []


assert isinstance(ORToolsCVRPTWAdapter(), SolverAdapter)
