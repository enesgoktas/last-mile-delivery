"""Pull solver-quality fields out of the opaque `metrics` JSONB and
populate the typed columns on `OptimizationRun`.

Layer 2a — lets the optimization-quality dashboard filter and aggregate
in SQL (`WHERE solver_kind = 'rich_alns' AND fallback_invoked = FALSE`)
instead of parsing JSONB on every query.

The JSONB column stays in place for solver-specific extras that don't
warrant promotion. The seven fields here are the load-bearing ones the
operations team actually needs.
"""
from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.optimization_run import OptimizationRun


def _coerce_bool(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return None


def _coerce_int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def promote_solver_metrics(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    solver_kind: str | None,
    metrics: dict | None,
    time_budget_allowed_s: float | None = None,
    time_budget_used_s: float | None = None,
) -> None:
    """Update the typed columns on the `optimization_runs` row.

    Pulls these fields from `metrics` JSONB if not given explicitly:
      - `total_iterations` / `iterations`
      - `feasibility_ratio`
      - `fallback_invoked`
      - `decomposition_used`
      - `warm_start_hit` / `warm_start_used`

    `time_budget_used_s` / `time_budget_allowed_s` are usually passed
    by the caller because the adapter returns them as top-level fields
    on the SolveResult, not inside metrics.
    """
    metrics = metrics or {}
    iterations = _coerce_int(
        metrics.get("total_iterations") or metrics.get("iterations")
    )
    feasibility_ratio = _coerce_float(metrics.get("feasibility_ratio"))
    fallback_invoked = _coerce_bool(metrics.get("fallback_invoked"))
    decomposition_used = _coerce_bool(
        metrics.get("decomposition_used")
        or metrics.get("decomposition_invoked")
    )
    warm_start_hit = _coerce_bool(
        metrics.get("warm_start_hit") or metrics.get("warm_start_used")
    )

    await db.execute(
        update(OptimizationRun)
        .where(OptimizationRun.id == run_id)
        .values(
            solver_kind=solver_kind,
            iterations=iterations,
            feasibility_ratio=feasibility_ratio,
            fallback_invoked=fallback_invoked,
            decomposition_used=decomposition_used,
            warm_start_hit=warm_start_hit,
            time_budget_used_s=_coerce_float(time_budget_used_s),
            time_budget_allowed_s=_coerce_float(time_budget_allowed_s),
        )
    )
