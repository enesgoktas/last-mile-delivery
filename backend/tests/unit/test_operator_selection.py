"""Tests for operator selection (M2.4)."""
from __future__ import annotations

import random

from app.algorithms.rich_vrp.alns.operator_selection import AlphaUCB, SegmentedRouletteWheel


def test_roulette_never_picks_zero_weight():
    rng = random.Random(0)
    sel = SegmentedRouletteWheel(n_operators=3, segment_length=100)
    # Force operator 0 to weight 0 by always scoring it 0 and others high
    for _ in range(200):
        idx = sel.select(rng)
        # Update: operator 0 always rejected, others always best
        if idx == 0:
            sel.update(0, 0)
        else:
            sel.update(idx, 8)

    # After enough iterations, weight[0] should be negligible
    # The roulette should rarely select it
    counts = [0, 0, 0]
    for _ in range(1000):
        counts[sel.select(rng)] += 1
    assert counts[0] < 200, f"Operator 0 selected too often: {counts[0]}/1000"


def test_roulette_seed_reproducibility():
    sel1 = SegmentedRouletteWheel(n_operators=4)
    sel2 = SegmentedRouletteWheel(n_operators=4)
    rng1 = random.Random(42)
    rng2 = random.Random(42)
    for _ in range(50):
        assert sel1.select(rng1) == sel2.select(rng2)
        sel1.update(0, 4)
        sel2.update(0, 4)


def test_ucb_explores_before_exploiting():
    sel = AlphaUCB(n_operators=3, alpha=0.05)
    rng = random.Random(0)
    # First 3 calls should each pick a different operator (forced exploration)
    seen = set()
    for _ in range(3):
        seen.add(sel.select(rng))
        sel.update(list(seen)[-1], 1.0)
    assert seen == {0, 1, 2}


def test_ucb_exploits_best_operator():
    sel = AlphaUCB(n_operators=2, alpha=0.001)  # low alpha → exploitation
    rng = random.Random(0)
    # Force-explore
    sel.select(rng)
    sel.update(0, 10.0)
    sel.select(rng)
    sel.update(1, 1.0)
    # Now should strongly prefer operator 0
    picks = [sel.select(rng) for _ in range(20)]
    assert picks.count(0) > picks.count(1)


def test_roulette_segment_decay():
    sel = SegmentedRouletteWheel(n_operators=2, segment_length=10, decay_theta=0.5)
    # Both start equal; give operator 0 all the reward
    for _ in range(10):
        sel.update(0, 8)
        sel.update(1, 0)
    # After segment, op 0 should have higher weight
    weights = sel.weights()
    assert weights[0] > weights[1]
