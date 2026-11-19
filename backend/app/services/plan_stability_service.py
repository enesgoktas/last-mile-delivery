"""Compute and persist a plan-stability score after each range re-run.

Layer 2b. When the range planner runs over a date range it overlaps
with a prior plan more often than not (dispatchers re-run as new
orders arrive). For each (order, prior_courier, prior_day) tuple we
check whether the new plan kept the same (courier, day) — and roll up
into one stability_pct per run.

A high score = dispatcher-friendly (drivers see consistent schedules);
low = solver thrashing. Useful for tuning the (still-disabled)
plan-stability cost-function term.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan_stability_score import PlanStabilityScore

logger = logging.getLogger(__name__)


async def record_plan_stability(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    optimization_run_id: uuid.UUID,
    prior_run_id: uuid.UUID | None,
    prior_assignments: Mapping[uuid.UUID, tuple[uuid.UUID, date]],
    new_assignments: Mapping[uuid.UUID, tuple[uuid.UUID, date]],
) -> PlanStabilityScore | None:
    """Persist one `PlanStabilityScore` row for the (run, prior_run) pair.

    `*_assignments` map order_id → (courier_id, scheduled_date). Orders
    that appear in `prior_assignments` but not in `new_assignments`
    (or vice-versa) are counted as "evaluated but changed" — they
    contribute to the denominator only.
    """
    eligible_orders = set(prior_assignments) | set(new_assignments)
    if not eligible_orders:
        return None
    evaluated = len(prior_assignments)
    if evaluated == 0:
        # No prior plan to compare against — stability is undefined.
        return None

    unchanged = 0
    same_courier = 0
    same_day = 0
    for order_id, (prior_courier, prior_day) in prior_assignments.items():
        new_pair = new_assignments.get(order_id)
        if new_pair is None:
            continue
        new_courier, new_day = new_pair
        if new_courier == prior_courier and new_day == prior_day:
            unchanged += 1
        if new_courier == prior_courier:
            same_courier += 1
        if new_day == prior_day:
            same_day += 1

    stability_pct = unchanged / evaluated if evaluated > 0 else None
    row = PlanStabilityScore(
        tenant_id=tenant_id,
        optimization_run_id=optimization_run_id,
        prior_run_id=prior_run_id,
        orders_evaluated=evaluated,
        orders_unchanged=unchanged,
        orders_same_courier=same_courier,
        orders_same_day=same_day,
        stability_pct=stability_pct,
    )
    db.add(row)
    return row
