"""Emit + reconcile `ETAPredictionLog` rows.

Layer 2b. Two entry points:

  * `record_planner_etas` — called at the end of dispatch persist with
    the freshly-created stops. One row per DELIVERY stop with
    `predicted_by=PLANNER`, `predicted_at=now`, `predicted_arrival` =
    the stop's `planned_arrival`.
  * `reconcile_arrival` — called from the lifecycle helper when a stop
    transitions to ARRIVED / COMPLETED. Fills `actual_arrival` + `error_s`
    on every pending row for that stop. Idempotent.

Together these build the dataset that powers the ETA-accuracy
dashboard. A month into the pilot, we can compute:

    SELECT
      AVG(ABS(error_s)) / 60.0 AS mean_abs_error_min,
      PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY ABS(error_s)) / 60.0
        AS p95_min
    FROM eta_prediction_log
    WHERE actual_arrival IS NOT NULL
      AND tenant_id = ?
      AND predicted_by = 'PLANNER';
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eta_prediction_log import ETAPredictedBy, ETAPredictionLog
from app.utils.compat import utcnow_naive

logger = logging.getLogger(__name__)


async def record_planner_etas(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    optimization_run_id: uuid.UUID | None,
    stops: list,
) -> int:
    """Insert one PLANNER row per DELIVERY stop in `stops`.

    `stops` is whatever the persist path holds — duck-typed against
    `stop.id`, `stop.planned_arrival`, `stop.stop_type`. Returns the
    number of rows written.
    """
    if not stops:
        return 0
    now = utcnow_naive()
    rows: list[ETAPredictionLog] = []
    for stop in stops:
        if getattr(stop, "stop_type", None) is None:
            continue
        stop_type_value = getattr(stop.stop_type, "value", stop.stop_type)
        if stop_type_value != "DELIVERY":
            continue
        predicted_arrival = getattr(stop, "planned_arrival", None)
        if predicted_arrival is None:
            continue
        horizon_s = int((predicted_arrival - now).total_seconds())
        rows.append(
            ETAPredictionLog(
                tenant_id=tenant_id,
                stop_id=stop.id,
                optimization_run_id=optimization_run_id,
                predicted_at=now,
                predicted_arrival=predicted_arrival,
                predicted_by=ETAPredictedBy.PLANNER,
                prediction_horizon_s=max(horizon_s, 0),
            )
        )
    if not rows:
        return 0
    db.add_all(rows)
    return len(rows)


async def reconcile_arrival(
    db: AsyncSession,
    *,
    stop_id: uuid.UUID,
    actual_arrival: datetime,
) -> int:
    """Fill `actual_arrival` + `error_s` on every still-pending log row
    for this stop. Idempotent: subsequent calls are no-ops because we
    only touch rows where `actual_arrival IS NULL`.
    """
    pending = (
        await db.execute(
            select(ETAPredictionLog).where(
                ETAPredictionLog.stop_id == stop_id,
                ETAPredictionLog.actual_arrival.is_(None),
            )
        )
    ).scalars().all()
    if not pending:
        return 0
    for row in pending:
        row.actual_arrival = actual_arrival
        row.error_s = int(
            (actual_arrival - row.predicted_arrival).total_seconds()
        )
    return len(pending)
