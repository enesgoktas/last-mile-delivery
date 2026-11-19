"""Solver adapter protocol: the only API external code uses to invoke a VRP solver.

All three solvers (human-dispatcher baseline, OR-Tools CVRPTW, and the new
rich ALNS engine) conform to this protocol.  The registry in solver_registry.py
maps solver_kind strings to concrete adapter instances.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.algorithms.vrp_solver import VRPInput, VRPSolution


@dataclass(frozen=True, slots=True)
class SolveOptions:
    """Unified options forwarded to every solver adapter."""

    time_budget_s: float = 60.0
    seed: int = 0
    spread_fleet: bool = True
    skip_multi_trip: bool = False
    # Warm-start: OR-Tools-shaped node-index sequences per vehicle (None = no warm start)
    initial_routes: list[list[int]] | None = None
    # Warm-start solution from a different solver (typically OR-Tools handed to
    # rich_alns). Adapters that don't use it ignore it. Strictly more
    # informative than `initial_routes` because it carries already-simulated
    # arrival/departure times and per-courier route shape.
    warm_start_solution: VRPSolution | None = None
    # Scenario-level flags; adapters that don't use them may ignore them
    routing_objective: str = "SERVICE_FIRST"
    fleet_utilization: str = "FULL_FLEET"


@dataclass(frozen=True, slots=True)
class SolveResult:
    """Unified result returned by every solver adapter."""

    solution: VRPSolution
    # One of: "human_dispatcher", "ortools_cvrptw", "rich_alns"
    solver_kind: str
    elapsed_s: float
    # Solver-specific telemetry; must be JSON-serialisable (persisted in objective_breakdown)
    metrics: dict = field(default_factory=dict)


@runtime_checkable
class SolverAdapter(Protocol):
    """Protocol that every concrete solver adapter must satisfy."""

    name: str

    def solve(self, problem: VRPInput, options: SolveOptions) -> SolveResult:
        """Run the solver and return a result.  Blocking; call from a thread, not async."""
        ...

    def supports(self, problem: VRPInput) -> bool:
        """Return True if this adapter can handle the given problem."""
        ...

    def explain_limits(self, problem: VRPInput) -> list[str]:
        """Human-readable reasons why this adapter cannot (fully) handle the problem."""
        ...


def timed_solve(
    adapter: SolverAdapter,
    problem: VRPInput,
    options: SolveOptions,
) -> SolveResult:
    """Convenience wrapper that measures wall-clock time and injects it into the result."""
    t0 = time.monotonic()
    result = adapter.solve(problem, options)
    elapsed = time.monotonic() - t0
    # Replace elapsed_s with measured value (adapters may set it themselves; we override)
    return SolveResult(
        solution=result.solution,
        solver_kind=result.solver_kind,
        elapsed_s=elapsed,
        metrics=result.metrics,
    )
