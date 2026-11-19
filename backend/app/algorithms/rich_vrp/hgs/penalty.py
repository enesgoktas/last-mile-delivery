"""Adaptive penalty manager for HGS feasibility targeting.

Tracks the feasibility ratio of the last N offspring and adjusts a penalty
multiplier so that ~65% of offspring are feasible (the target recommended by
Vidal et al. for VRP/PDPTW instances).

The multiplier is informational — the ALNS engine uses it to decide whether
to accept infeasible individuals into the infeasible elite pool.  It does not
change the underlying route cost computation.
"""
from __future__ import annotations

from collections import deque

_TARGET_RATIO = 0.65
_TOLERANCE = 0.05
_ADJUST_FACTOR = 1.2
_MIN_MULT = 0.1
_MAX_MULT = 10.0


class AdaptivePenalty:
    """Sliding-window feasibility tracker with penalty multiplier."""

    def __init__(self, window: int = 100) -> None:
        self._window = window
        self._history: deque[bool] = deque(maxlen=window)
        self.multiplier: float = 1.0

    def record(self, is_feasible: bool) -> None:
        self._history.append(is_feasible)

    @property
    def feasibility_ratio(self) -> float:
        if not self._history:
            return _TARGET_RATIO
        return sum(self._history) / len(self._history)

    def adjust(self) -> None:
        """Call once per segment to update the multiplier."""
        ratio = self.feasibility_ratio
        if ratio > _TARGET_RATIO + _TOLERANCE:
            self.multiplier = min(_MAX_MULT, self.multiplier * _ADJUST_FACTOR)
        elif ratio < _TARGET_RATIO - _TOLERANCE:
            self.multiplier = max(_MIN_MULT, self.multiplier / _ADJUST_FACTOR)
