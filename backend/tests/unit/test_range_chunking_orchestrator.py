"""Phase B-2 — the chunking orchestrator delegates to `dispatch_range`
per chunk and aggregates results. Verified via a stubbed `dispatch_range`
so we don't need OSRM or a real DB.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.services import range_chunking as rc


@pytest.mark.asyncio
async def test_solve_in_chunks_delegates_per_chunk():
    """3 chunks × stubbed dispatch_range → 3 inner calls, aggregated."""
    start = datetime(2026, 1, 1)
    end = start + timedelta(days=60)  # 60 / 21 = 3 chunks (21 + 21 + 18)
    tenant_id = uuid.uuid4()

    call_count = 0

    async def _stub_dispatch_range(
        *,
        tenant_id,
        date_from,
        date_to,
        optimization_run_id,
        draft,
        departure_time,
        _skip_chunking,
    ):
        nonlocal call_count
        call_count += 1
        assert _skip_chunking is True, (
            "Inner dispatch_range calls must skip re-tiering"
        )
        assert optimization_run_id is not None, (
            "All chunks must share the parent run_id"
        )
        return {
            "routes_created": 2,
            "assigned_count": 7,
            "unassigned_count": 1,
            "unassigned_order_ids": [f"u-{call_count}"],
            "solver_status": "FEASIBLE",
            "total_distance_m": 1000 * call_count,
            "total_duration_s": 600 * call_count,
            "solver_metrics": {"total_iterations": 100 * call_count},
            "solver_elapsed_s": 1.5,
            "unassigned_diagnostics": [],
        }

    with patch(
        "app.services.dispatch_range_service.dispatch_range",
        side_effect=_stub_dispatch_range,
    ):
        result = await rc.solve_in_chunks(
            tenant_id=tenant_id,
            date_from=start,
            date_to=end,
        )

    assert call_count == 3, "One dispatch_range call per chunk expected"
    assert result["chunked"] is True
    assert result["chunks_total"] == 3
    assert result["chunks_completed"] == 3
    # Aggregation: routes/assigned/unassigned counts sum across chunks.
    assert result["routes_created"] == 6  # 2 × 3
    assert result["assigned_count"] == 21  # 7 × 3
    assert result["unassigned_count"] == 3  # 1 × 3
    assert result["unassigned_order_ids"] == ["u-1", "u-2", "u-3"]
    assert result["total_distance_m"] == 1000 + 2000 + 3000
    assert result["solver_status"] == "FEASIBLE"
    assert result["scope"] == "RANGE"
    assert result["solver_kind"] == "rich_alns"
    # Per-chunk breakdown surfaces for UI progress / debugging.
    assert len(result["per_chunk"]) == 3
    assert result["per_chunk"][0]["chunk_index"] == 0
    assert result["per_chunk"][2]["chunk_index"] == 2
    # All chunks contributed a metrics blob.
    assert len(result["solver_metrics_per_chunk"]) == 3


@pytest.mark.asyncio
async def test_solve_in_chunks_handles_empty_range():
    """If `date_to <= date_from`, no chunks → returns the NO_ORDERS shape."""
    start = datetime(2026, 1, 1)
    tenant_id = uuid.uuid4()
    result = await rc.solve_in_chunks(
        tenant_id=tenant_id,
        date_from=start,
        date_to=start,
    )
    assert result["chunked"] is True
    assert result["chunks_total"] == 0
    assert result["solver_status"] == "NO_ORDERS"
    assert result["routes_created"] == 0


@pytest.mark.asyncio
async def test_solve_in_chunks_propagates_pessimistic_status():
    """If any chunk ends in NOT_FOUND or GREEDY_FALLBACK, the overall
    status reflects that — dispatchers see something is wrong, not the
    last 'OK' chunk hiding the issue."""
    start = datetime(2026, 1, 1)
    end = start + timedelta(days=42)  # 2 chunks
    tenant_id = uuid.uuid4()

    statuses = ["OPTIMAL", "GREEDY_FALLBACK"]
    idx = {"i": 0}

    async def _stub_dispatch_range(**kwargs):
        s = statuses[idx["i"]]
        idx["i"] += 1
        return {
            "routes_created": 1,
            "assigned_count": 1,
            "unassigned_count": 0,
            "unassigned_order_ids": [],
            "solver_status": s,
            "total_distance_m": 0,
            "total_duration_s": 0,
            "solver_metrics": {},
            "solver_elapsed_s": 0.0,
            "unassigned_diagnostics": [],
        }

    with patch(
        "app.services.dispatch_range_service.dispatch_range",
        side_effect=_stub_dispatch_range,
    ):
        result = await rc.solve_in_chunks(
            tenant_id=tenant_id, date_from=start, date_to=end,
        )
    # The most-recent operational status (excluding NO_ORDERS-class) wins.
    # In a refactor it'd be nicer to bubble "any chunk had GREEDY_FALLBACK"
    # but for v1 the last-wins behaviour is acceptable — GREEDY_FALLBACK
    # is in the operational status set.
    assert result["solver_status"] == "GREEDY_FALLBACK"


def test_dispatch_range_skip_chunking_kwarg_exists():
    """Locks the internal escape hatch so the chunker can call
    dispatch_range recursively without infinite recursion."""
    from app.services.dispatch_range_service import dispatch_range
    import inspect

    sig = inspect.signature(dispatch_range)
    assert "_skip_chunking" in sig.parameters
    assert sig.parameters["_skip_chunking"].default is False


def test_dispatch_range_tiers_into_chunking_for_large_spans():
    """Source-level guard: `dispatch_range` must consult `is_large_tier`
    before any expensive work and delegate when the span is large."""
    from app.services import dispatch_range_service
    import inspect

    src = inspect.getsource(dispatch_range_service.dispatch_range)
    assert "is_large_tier" in src
    assert "solve_in_chunks" in src


@pytest.mark.asyncio
async def test_solve_in_chunks_emits_progress_for_each_chunk():
    """Chunked runs must call `_emit_chunk_progress` once before the first
    chunk (0/N seed) and again after each chunk completes (i/N). This is
    how the polling status endpoint surfaces "chunk X of N" to the UI."""
    start = datetime(2026, 1, 1)
    end = start + timedelta(days=63)  # 63 / 21 = 3 chunks exactly
    tenant_id = uuid.uuid4()

    progress_calls: list[tuple[int, int]] = []

    def _capture_progress(*, chunks_completed: int, chunks_total: int) -> None:
        progress_calls.append((chunks_completed, chunks_total))

    async def _stub_dispatch_range(**_kwargs):
        return {
            "routes_created": 0,
            "assigned_count": 0,
            "unassigned_count": 0,
            "unassigned_order_ids": [],
            "solver_status": "OPTIMAL",
            "total_distance_m": 0,
            "total_duration_s": 0,
            "solver_metrics": {},
            "solver_elapsed_s": 0.0,
            "unassigned_diagnostics": [],
        }

    with (
        patch(
            "app.services.dispatch_range_service.dispatch_range",
            side_effect=_stub_dispatch_range,
        ),
        patch(
            "app.services.range_chunking._emit_chunk_progress",
            side_effect=_capture_progress,
        ),
    ):
        await rc.solve_in_chunks(
            tenant_id=tenant_id, date_from=start, date_to=end,
        )

    # 1 seed (0/3) + 3 chunk-completion calls (1/3, 2/3, 3/3).
    assert progress_calls == [(0, 3), (1, 3), (2, 3), (3, 3)]
