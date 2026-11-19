"""Rolling-window chunking for long-horizon range dispatch.

For spans up to `LARGE_TIER_SPAN_THRESHOLD` days, `dispatch_range` solves the
whole horizon as one VRP. Beyond that, the solver state grows
unmanageably (`couriers × span_days` virtual vehicles × thousands of orders),
and even Rich ALNS cannot converge in a reasonable time budget. This module
splits the horizon into non-overlapping chunks of `CHUNK_WINDOW_DAYS` and
solves each chunk independently, then stitches the results.

**Tradeoff**: chunking trades global optimality for tractability. An order
whose window allows delivery on either side of a chunk boundary is decided
within whichever chunk it falls into based on its `earliest_delivery`. With
overlapping windows + warm-start seed between chunks the boundary could be
optimised away, but that's a follow-up — non-overlapping is the
simplest-correct starting point.

The orchestrator calls `dispatch_range(...)` recursively for each chunk
with the internal flag `_skip_chunking=True` so the inner call doesn't
re-tier and loop forever.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta

from celery import current_task

logger = logging.getLogger(__name__)


def _emit_chunk_progress(*, chunks_completed: int, chunks_total: int) -> None:
    """Push per-chunk progress to the Celery task so the UI can poll it.

    `current_task` is a context-aware proxy. When this module runs outside a
    Celery task (unit tests, direct service calls) the proxy resolves to a
    placeholder whose `request.id` is None — we skip the update_state call so
    the chunker stays usable in any context.
    """
    try:
        task = current_task._get_current_object()  # type: ignore[attr-defined]
    except Exception:
        return
    if task is None or getattr(task.request, "id", None) is None:
        return
    try:
        task.update_state(
            state="PROGRESS",
            meta={
                "chunks_completed": chunks_completed,
                "chunks_total": chunks_total,
                "scope": "RANGE",
            },
        )
    except Exception:
        logger.warning("range_chunking: failed to emit progress update", exc_info=True)


# Beyond this span we chunk. The threshold is intentionally a bit above the
# medium-tier ceiling so spans like 25 days still get a single global solve
# (cheaper, better quality) and only the genuinely large requests pay the
# chunking tax.
LARGE_TIER_SPAN_THRESHOLD = 30

# Chunk window size. 21 days is a sweet spot: long enough to amortise solver
# setup cost; short enough that the per-chunk solve completes within the
# medium-tier budget ceiling.
CHUNK_WINDOW_DAYS = 21


def is_large_tier(span_days: int) -> bool:
    """True iff the range should be solved via the chunked orchestrator
    instead of a single global ALNS solve."""
    return span_days > LARGE_TIER_SPAN_THRESHOLD


def split_into_chunks(
    date_from: datetime, date_to: datetime, *, window_days: int = CHUNK_WINDOW_DAYS
) -> list[tuple[datetime, datetime]]:
    """Slice `[date_from, date_to)` into non-overlapping windows.

    Each chunk's end is exclusive — same convention `dispatch_range` uses.
    The last chunk may be shorter than `window_days` if the span doesn't
    divide evenly.

    Returns a list of `(chunk_start, chunk_end)` tuples in order.
    """
    if date_to <= date_from:
        return []
    chunks: list[tuple[datetime, datetime]] = []
    cursor = date_from
    while cursor < date_to:
        chunk_end = min(cursor + timedelta(days=window_days), date_to)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return chunks


async def solve_in_chunks(
    *,
    tenant_id: uuid.UUID,
    date_from: datetime,
    date_to: datetime,
    optimization_run_id: uuid.UUID | None = None,
    draft: bool = False,
    departure_time: datetime | None = None,
) -> dict:
    """Run `dispatch_range` on each chunk of `[date_from, date_to)` and
    aggregate the per-chunk results into a single payload that matches
    the single-solve return shape.

    The same `optimization_run_id` is shared across all chunks — dispatchers
    see one optimisation run with N routes, not N runs of one route each.
    """
    # Lazy import — avoids a circular reference between dispatch_range_service
    # and this module (dispatch_range_service imports this file to decide
    # tier; this file calls dispatch_range to do the actual work).
    from app.services.dispatch_range_service import dispatch_range

    started = time.monotonic()
    chunks = split_into_chunks(date_from, date_to)
    if not chunks:
        return {
            "routes_created": 0,
            "assigned_count": 0,
            "unassigned_count": 0,
            "unassigned_order_ids": [],
            "solver_status": "NO_ORDERS",
            "scope": "RANGE",
            "chunked": True,
            "chunks_total": 0,
            "chunks_completed": 0,
            "span_days": (date_to - date_from).days,
        }

    run_id = optimization_run_id or uuid.uuid4()
    span_days = (date_to - date_from).days

    aggregate = {
        "routes_created": 0,
        "assigned_count": 0,
        "unassigned_count": 0,
        "unassigned_order_ids": [],
        "total_distance_m": 0,
        "total_duration_s": 0,
        "unassigned_diagnostics": [],
        "per_chunk": [],
        "solver_metrics_per_chunk": [],
    }
    solver_status = None

    # Emit the initial 0-of-N progress signal so the UI can switch out of
    # the "submitting…" state as soon as chunking starts, before the first
    # chunk finishes.
    _emit_chunk_progress(chunks_completed=0, chunks_total=len(chunks))

    for chunk_idx, (chunk_start, chunk_end) in enumerate(chunks):
        logger.info(
            "Range chunking: solving chunk %d/%d [%s..%s] for tenant %s",
            chunk_idx + 1,
            len(chunks),
            chunk_start.date().isoformat(),
            chunk_end.date().isoformat(),
            tenant_id,
        )
        chunk_result = await dispatch_range(
            tenant_id=tenant_id,
            date_from=chunk_start,
            date_to=chunk_end,
            optimization_run_id=run_id,
            draft=draft,
            departure_time=departure_time,
            _skip_chunking=True,
        )

        # Merge into the aggregate. Each chunk produces independent route
        # rows under the shared run_id, so route counts simply add.
        aggregate["routes_created"] += chunk_result.get("routes_created", 0)
        aggregate["assigned_count"] += chunk_result.get("assigned_count", 0)
        aggregate["unassigned_count"] += chunk_result.get("unassigned_count", 0)
        aggregate["unassigned_order_ids"].extend(
            chunk_result.get("unassigned_order_ids", [])
        )
        aggregate["total_distance_m"] += chunk_result.get("total_distance_m", 0)
        aggregate["total_duration_s"] += chunk_result.get("total_duration_s", 0)
        aggregate["unassigned_diagnostics"].extend(
            chunk_result.get("unassigned_diagnostics", [])
        )
        aggregate["per_chunk"].append({
            "chunk_index": chunk_idx,
            "date_from": chunk_start.isoformat(),
            "date_to": chunk_end.isoformat(),
            "routes_created": chunk_result.get("routes_created", 0),
            "assigned_count": chunk_result.get("assigned_count", 0),
            "unassigned_count": chunk_result.get("unassigned_count", 0),
            "solver_status": chunk_result.get("solver_status"),
            "solver_elapsed_s": chunk_result.get("solver_elapsed_s"),
        })
        if chunk_result.get("solver_metrics") is not None:
            aggregate["solver_metrics_per_chunk"].append(
                chunk_result.get("solver_metrics")
            )

        # Carry the most pessimistic status across chunks. If any chunk
        # ended in NOT_FOUND/ERROR/GREEDY_FALLBACK, the overall status
        # reflects that. Otherwise the last non-empty chunk wins.
        chunk_status = chunk_result.get("solver_status")
        if chunk_status and chunk_status not in (
            "NO_ORDERS", "NO_COURIERS", "OSRM_UNAVAILABLE"
        ):
            solver_status = chunk_status

        _emit_chunk_progress(
            chunks_completed=chunk_idx + 1, chunks_total=len(chunks)
        )

    final_status = solver_status or "NO_ORDERS"
    aggregate.update({
        "solver_status": final_status,
        "scope": "RANGE",
        "chunked": True,
        "chunks_total": len(chunks),
        "chunks_completed": len(chunks),
        "span_days": span_days,
        "anchor_iso": date_from.isoformat(),
        "optimization_run_id": str(run_id),
        "solver_kind": "rich_alns",
        "dispatch_range_latency_ms": round((time.monotonic() - started) * 1000, 2),
    })
    return aggregate
