"""Helpers for writing `DispatchDecisionLog` rows during dispatch persist.

A single decision log row captures: for one (run, order) pair, which
courier the solver chose, the score it gave that choice, and any
top-N runner-up couriers it considered (when the adapter exposes them).

The dispatch services call `record_decisions_for_solution` once per
solve at persist time. The helper is intentionally batch-friendly so the
write is one INSERT … VALUES regardless of how many orders the solver
touched.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dispatch_decision_log import DispatchDecisionLog
from app.models.enums import DispatchDecisionKind


def _build_row(
    *,
    tenant_id: uuid.UUID,
    optimization_run_id: uuid.UUID | None,
    order_id: uuid.UUID,
    chosen_courier_id: uuid.UUID | None,
    chosen_score: float | None,
    kind: DispatchDecisionKind,
    alternatives: list[dict[str, Any]] | None,
    decision_reason: str | None,
) -> DispatchDecisionLog:
    return DispatchDecisionLog(
        tenant_id=tenant_id,
        optimization_run_id=optimization_run_id,
        order_id=order_id,
        chosen_courier_id=chosen_courier_id,
        chosen_score=chosen_score,
        kind=kind,
        alternatives=alternatives,
        decision_reason=decision_reason,
    )


async def record_decisions_for_solution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    optimization_run_id: uuid.UUID | None,
    kind: DispatchDecisionKind,
    assignments: Iterable[tuple[uuid.UUID, uuid.UUID, float | None]],
    unassigned: Iterable[tuple[uuid.UUID, str | None]] = (),
    alternatives_by_order: dict[uuid.UUID, list[dict[str, Any]]] | None = None,
) -> int:
    """Bulk-insert one row per assigned/unassigned order in the solution.

    `assignments` is an iterable of `(order_id, chosen_courier_id, score)`.
    `unassigned` is an iterable of `(order_id, reason)` — the solver
    couldn't place these and `chosen_courier_id` is recorded NULL.

    `alternatives_by_order` is an optional map from order_id → top-N
    runner-up alternatives (each a dict with `courier_id`, `score`,
    `reason` keys). Skipped if the adapter doesn't expose them.

    Returns the number of rows written.
    """
    alternatives_by_order = alternatives_by_order or {}
    rows: list[DispatchDecisionLog] = []
    for order_id, courier_id, score in assignments:
        rows.append(
            _build_row(
                tenant_id=tenant_id,
                optimization_run_id=optimization_run_id,
                order_id=order_id,
                chosen_courier_id=courier_id,
                chosen_score=score,
                kind=kind,
                alternatives=alternatives_by_order.get(order_id),
                decision_reason=None,
            )
        )
    for order_id, reason in unassigned:
        rows.append(
            _build_row(
                tenant_id=tenant_id,
                optimization_run_id=optimization_run_id,
                order_id=order_id,
                chosen_courier_id=None,
                chosen_score=None,
                kind=kind,
                alternatives=alternatives_by_order.get(order_id),
                decision_reason=reason or "Solver returned no feasible assignment",
            )
        )
    if not rows:
        return 0
    db.add_all(rows)
    return len(rows)
