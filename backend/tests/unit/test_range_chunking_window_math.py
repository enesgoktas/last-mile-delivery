"""Phase B-2 — window math + tier decision for the chunking orchestrator.

The expensive bits (actual solver runs across chunks) belong in the
integration suite. These pure tests pin the boundary arithmetic so a
refactor can't quietly let an order fall off the edge of a chunk window
or get double-assigned.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.services import range_chunking as rc


# ── is_large_tier ─────────────────────────────────────────────────────────


def test_is_large_tier_boundary():
    """Spans at-or-below the threshold solve as a single VRP. Above it
    they go through the chunker."""
    assert rc.is_large_tier(rc.LARGE_TIER_SPAN_THRESHOLD - 1) is False
    assert rc.is_large_tier(rc.LARGE_TIER_SPAN_THRESHOLD) is False
    assert rc.is_large_tier(rc.LARGE_TIER_SPAN_THRESHOLD + 1) is True


def test_is_large_tier_default_thresholds():
    # Lock the defaults so the tier line doesn't quietly move.
    assert rc.LARGE_TIER_SPAN_THRESHOLD == 30
    assert rc.CHUNK_WINDOW_DAYS == 21


# ── split_into_chunks ────────────────────────────────────────────────────


def test_split_evenly_divisible_range():
    """42 days / 21-day windows → exactly 2 chunks."""
    start = datetime(2026, 1, 1)
    end = start + timedelta(days=42)
    chunks = rc.split_into_chunks(start, end, window_days=21)
    assert len(chunks) == 2
    assert chunks[0] == (start, start + timedelta(days=21))
    assert chunks[1] == (start + timedelta(days=21), end)
    # End of last chunk == requested end. No tail loss.
    assert chunks[-1][1] == end


def test_split_with_tail_remainder():
    """46 days / 21-day windows → 3 chunks: 21 + 21 + 4."""
    start = datetime(2026, 1, 1)
    end = start + timedelta(days=46)
    chunks = rc.split_into_chunks(start, end, window_days=21)
    assert len(chunks) == 3
    assert (chunks[0][1] - chunks[0][0]).days == 21
    assert (chunks[1][1] - chunks[1][0]).days == 21
    # Tail chunk is the remainder.
    assert (chunks[2][1] - chunks[2][0]).days == 4
    assert chunks[2][1] == end


def test_split_smaller_than_window():
    """A 5-day span ≤ window_days produces a single chunk equal to the
    input range — chunker is a no-op."""
    start = datetime(2026, 1, 1)
    end = start + timedelta(days=5)
    chunks = rc.split_into_chunks(start, end, window_days=21)
    assert len(chunks) == 1
    assert chunks[0] == (start, end)


def test_split_no_overlap_no_gaps():
    """Across every consecutive chunk pair, the previous end equals the
    next start — non-overlapping AND no gaps."""
    start = datetime(2026, 1, 1)
    end = start + timedelta(days=90)
    chunks = rc.split_into_chunks(start, end, window_days=21)
    for i in range(len(chunks) - 1):
        assert chunks[i][1] == chunks[i + 1][0], (
            f"gap or overlap between chunks {i} and {i+1}: "
            f"{chunks[i][1]} vs {chunks[i+1][0]}"
        )


def test_split_inverted_range_returns_empty():
    """date_to <= date_from yields zero chunks (caller handles)."""
    start = datetime(2026, 1, 1)
    assert rc.split_into_chunks(start, start) == []
    assert rc.split_into_chunks(start, start - timedelta(days=1)) == []


def test_split_three_month_horizon():
    """The dispatcher's '3 months' scenario yields ~4 chunks of 21 days."""
    start = datetime(2026, 1, 1)
    end = start + timedelta(days=90)
    chunks = rc.split_into_chunks(start, end, window_days=21)
    assert len(chunks) == 5  # ceil(90/21) = 5; last chunk holds the remainder
    assert chunks[-1][1] == end
    total_days = sum((c[1] - c[0]).days for c in chunks)
    assert total_days == 90, (
        f"Chunks must cover the full 90-day span without loss; got {total_days}"
    )
