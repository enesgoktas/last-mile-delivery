"""Adaptive operator selection for the ALNS engine.

Two implementations:
  SegmentedRouletteWheel — decaying score-based weights, updated per segment.
  AlphaUCB              — classical UCB1 bandit with improvement reward.
"""
from __future__ import annotations

import math
import random


class SegmentedRouletteWheel:
    """Operator selection via weighted random draw with decaying segment updates.

    Scores per outcome (higher = better):
        best     : 8  (new global best)
        improved : 4  (better than current, not global best)
        accepted : 2  (accepted but not better than current)
        rejected : 0  (not accepted)

    After each segment of `segment_length` iterations the weights decay:
        w[i] = θ · w[i] + (1 - θ) · avg_score_in_segment_i
    """

    def __init__(
        self,
        n_operators: int,
        segment_length: int = 100,
        decay_theta: float = 0.8,
        initial_weight: float = 1.0,
    ) -> None:
        self._n = n_operators
        self._segment_length = segment_length
        self._theta = decay_theta
        self._weights = [initial_weight] * n_operators
        self._segment_scores = [0.0] * n_operators
        self._segment_uses = [0] * n_operators
        self._iter = 0

    def select(self, rng: random.Random) -> int:
        total = sum(self._weights)
        if total <= 0:
            return rng.randrange(self._n)
        r = rng.random() * total
        cumulative = 0.0
        for i, w in enumerate(self._weights):
            cumulative += w
            if r <= cumulative:
                return i
        return self._n - 1

    def update(self, idx: int, score: int) -> None:
        self._segment_scores[idx] += score
        self._segment_uses[idx] += 1
        self._iter += 1
        if self._iter % self._segment_length == 0:
            self._end_segment()

    def _end_segment(self) -> None:
        for i in range(self._n):
            if self._segment_uses[i] > 0:
                avg = self._segment_scores[i] / self._segment_uses[i]
                self._weights[i] = self._theta * self._weights[i] + (1 - self._theta) * avg
                self._weights[i] = max(self._weights[i], 1e-6)
            self._segment_scores[i] = 0.0
            self._segment_uses[i] = 0

    def weights(self) -> list[float]:
        return list(self._weights)


class AlphaUCB:
    """UCB1 bandit operator selection.

    Reward is the improvement in best-so-far objective (non-negative; 0 if no improvement).
    Formula: score[i] = mean_reward[i] + alpha * sqrt(log(t) / n[i])
    """

    def __init__(self, n_operators: int, alpha: float = 0.05) -> None:
        self._n = n_operators
        self._alpha = alpha
        self._total_reward = [0.0] * n_operators
        self._uses = [0] * n_operators
        self._t = 0

    def select(self, rng: random.Random) -> int:
        self._t += 1
        # Force exploration: select each operator at least once first
        for i in range(self._n):
            if self._uses[i] == 0:
                return i
        scores = [
            self._total_reward[i] / self._uses[i]
            + self._alpha * math.sqrt(math.log(self._t) / self._uses[i])
            for i in range(self._n)
        ]
        best = max(range(self._n), key=lambda i: scores[i])
        # Break ties randomly
        best_score = scores[best]
        candidates = [i for i, s in enumerate(scores) if s >= best_score - 1e-9]
        return rng.choice(candidates)

    def update(self, idx: int, reward: float) -> None:
        self._total_reward[idx] += max(reward, 0.0)
        self._uses[idx] += 1

    def mean_rewards(self) -> list[float]:
        return [
            self._total_reward[i] / self._uses[i] if self._uses[i] > 0 else 0.0
            for i in range(self._n)
        ]
