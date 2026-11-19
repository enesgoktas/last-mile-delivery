"""Acceptance criteria for the ALNS engine.

RecordToRecordTravel — linear threshold decay from start_pct to end_pct.
SimulatedAnnealing   — geometric temperature cooling.

Lexicographic discipline (applied in both):
  If the new solution is worse on unserved_weighted or lateness+OT
  compared to the *current* solution, reject immediately without checking
  the economic cost criterion.
"""
from __future__ import annotations

import math

from app.algorithms.rich_vrp.objective.lexicographic import LexicographicObjective


class RecordToRecordTravel:
    """Accept if economic cost of new solution is within threshold of the best."""

    def __init__(
        self,
        initial_cost: int,
        start_pct: float = 0.015,
        end_pct: float = 0.0015,
    ) -> None:
        self._initial_cost = max(initial_cost, 1)
        self._start = start_pct * self._initial_cost
        self._end = end_pct * self._initial_cost
        self._progress = 0.0  # 0.0 = start, 1.0 = end

    def update_progress(self, progress: float) -> None:
        """progress: fraction of time budget elapsed (0.0 – 1.0)."""
        self._progress = max(0.0, min(1.0, progress))

    @property
    def _threshold(self) -> float:
        return self._start + (self._end - self._start) * self._progress

    def accept(
        self,
        new_obj: LexicographicObjective,
        current_obj: LexicographicObjective,
        best_obj: LexicographicObjective,
    ) -> bool:
        # Lex guards: reject if worse on hard-priority components vs current
        if new_obj.unserved_count_weighted > current_obj.unserved_count_weighted:
            return False
        if (
            new_obj.unserved_count_weighted == current_obj.unserved_count_weighted
            and new_obj.lateness_and_overtime_min > current_obj.lateness_and_overtime_min
        ):
            return False
        # RRT: compare economic cost against best
        delta = new_obj.generalized_cost - best_obj.generalized_cost
        return delta <= self._threshold


class SimulatedAnnealing:
    """Metropolis acceptance with geometric cooling."""

    def __init__(
        self,
        initial_cost: int,
        initial_temperature: float | None = None,
        cooling_rate: float = 0.9995,
    ) -> None:
        if initial_temperature is None:
            initial_temperature = max(initial_cost * 0.02, 1.0)
        self._temperature = initial_temperature
        self._cooling_rate = cooling_rate

    def accept(
        self,
        new_obj: LexicographicObjective,
        current_obj: LexicographicObjective,
        best_obj: LexicographicObjective,  # noqa: ARG002
    ) -> bool:
        # Lex guards
        if new_obj.unserved_count_weighted > current_obj.unserved_count_weighted:
            return False
        if (
            new_obj.unserved_count_weighted == current_obj.unserved_count_weighted
            and new_obj.lateness_and_overtime_min > current_obj.lateness_and_overtime_min
        ):
            return False
        delta = new_obj.generalized_cost - current_obj.generalized_cost
        if delta <= 0:
            return True
        import random
        return random.random() < math.exp(-delta / max(self._temperature, 1e-9))

    def cool(self) -> None:
        self._temperature *= self._cooling_rate
