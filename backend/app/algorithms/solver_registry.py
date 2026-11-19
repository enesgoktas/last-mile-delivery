"""Solver registry: maps solver_kind strings to SolverAdapter instances.

Usage:
    from app.algorithms.solver_registry import get_solver

    adapter = get_solver("rich_alns")
    result = adapter.solve(vrp_input, options)
"""
from __future__ import annotations

from app.algorithms.adapters.human_dispatcher import HumanDispatcherBaselineAdapter
from app.algorithms.adapters.ortools import ORToolsCVRPTWAdapter
from app.algorithms.adapters.rich_alns import RichALNSAdapter
from app.algorithms.solver_adapter import SolverAdapter

_REGISTRY: dict[str, SolverAdapter] = {
    "human_dispatcher": HumanDispatcherBaselineAdapter(),
    "ortools_cvrptw": ORToolsCVRPTWAdapter(),
    "rich_alns": RichALNSAdapter(),
}


def get_solver(kind: str) -> SolverAdapter:
    """Return the adapter for the given solver_kind.

    Raises KeyError for unknown kinds so callers fail loudly rather than
    silently falling back to an unexpected solver.
    """
    if kind not in _REGISTRY:
        raise KeyError(
            f"Unknown solver_kind: {kind!r}. Known kinds: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[kind]


def known_solver_kinds() -> tuple[str, ...]:
    """Return all registered solver kind strings."""
    return tuple(_REGISTRY)
