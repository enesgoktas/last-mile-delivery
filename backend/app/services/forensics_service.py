"""Assemble the per-order forensic timeline.

Reads from six tables in parallel and returns a single response shape
the OrderForensicsDrawer renders. All queries are scoped to the
caller's tenant via the existing tenant_filter machinery.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dispatch_decision_log import DispatchDecisionLog
from app.models.eta_prediction_log import ETAPredictionLog
from app.models.order import Order
from app.models.order_history import OrderStatusHistory
from app.models.route import Route, RouteStop
from app.models.stop_history import StopStatusHistory
from app.utils.tenant_context import tenant_filter


async def get_order_forensics(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    principal,
) -> dict:
    """Return the full forensic payload for one order.

    Raises 404 if the order doesn't exist in the caller's tenant.
    """
    order = (
        await db.execute(
            select(Order).where(
                Order.id == order_id,
                tenant_filter(Order, principal),
            )
        )
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    status_history = (
        await db.execute(
            select(OrderStatusHistory)
            .where(OrderStatusHistory.order_id == order_id)
            .order_by(OrderStatusHistory.created_at.asc())
        )
    ).scalars().all()

    stops: list[RouteStop] = []
    stop_history: list[StopStatusHistory] = []
    dispatch_decisions: list[DispatchDecisionLog] = []
    eta_predictions: list[ETAPredictionLog] = []
    if order.route_id is not None:
        stops = list(
            (
                await db.execute(
                    select(RouteStop)
                    .where(
                        RouteStop.route_id == order.route_id,
                        RouteStop.order_id == order.id,
                    )
                    .order_by(RouteStop.sequence_number.asc())
                )
            )
            .scalars()
            .all()
        )
        stop_ids = [s.id for s in stops]
        if stop_ids:
            stop_history = list(
                (
                    await db.execute(
                        select(StopStatusHistory)
                        .where(StopStatusHistory.stop_id.in_(stop_ids))
                        .order_by(StopStatusHistory.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            eta_predictions = list(
                (
                    await db.execute(
                        select(ETAPredictionLog)
                        .where(ETAPredictionLog.stop_id.in_(stop_ids))
                        .order_by(ETAPredictionLog.predicted_at.asc())
                    )
                )
                .scalars()
                .all()
            )

    dispatch_decisions = list(
        (
            await db.execute(
                select(DispatchDecisionLog)
                .where(DispatchDecisionLog.order_id == order.id)
                .order_by(DispatchDecisionLog.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    return {
        "order": {
            "id": order.id,
            "external_ref": order.external_ref,
            "status": order.status.value,
            "priority": order.priority.value,
            "scheduled_for_date": (
                order.scheduled_for_date.isoformat()
                if order.scheduled_for_date is not None
                else None
            ),
            "earliest_delivery": order.earliest_delivery,
            "latest_delivery": order.latest_delivery,
            "assigned_courier_id": order.assigned_courier_id,
            "route_id": order.route_id,
            "attempt_count": order.attempt_count,
            "failed_at": order.failed_at,
            "failure_cause_category": (
                order.failure_cause_category.value
                if order.failure_cause_category is not None
                else None
            ),
            "failure_cause_subcategory": order.failure_cause_subcategory,
        },
        "status_history": [
            {
                "from_status": h.from_status.value if h.from_status else None,
                "to_status": h.to_status.value,
                "actor_user_id": h.actor_user_id,
                "reason": h.reason,
                "created_at": h.created_at,
            }
            for h in status_history
        ],
        "stops": [
            {
                "id": s.id,
                "sequence_number": s.sequence_number,
                "stop_type": s.stop_type.value,
                "status": s.status.value,
                "lat": s.lat,
                "lon": s.lon,
                "planned_arrival": s.planned_arrival,
                "planned_departure": s.planned_departure,
                "actual_arrival": s.actual_arrival,
                "failure_reason": (
                    s.failure_reason.value if s.failure_reason else None
                ),
                "delay_cause": s.delay_cause.value if s.delay_cause else None,
            }
            for s in stops
        ],
        "stop_history": [
            {
                "stop_id": h.stop_id,
                "from_status": h.from_status.value if h.from_status else None,
                "to_status": h.to_status.value,
                "reason": h.reason,
                "lat": h.lat,
                "lon": h.lon,
                "created_at": h.created_at,
            }
            for h in stop_history
        ],
        "dispatch_decisions": [
            {
                "optimization_run_id": d.optimization_run_id,
                "kind": d.kind.value,
                "chosen_courier_id": d.chosen_courier_id,
                "chosen_score": d.chosen_score,
                "alternatives": d.alternatives,
                "decision_reason": d.decision_reason,
                "created_at": d.created_at,
            }
            for d in dispatch_decisions
        ],
        "eta_predictions": [
            {
                "stop_id": e.stop_id,
                "predicted_by": e.predicted_by.value,
                "predicted_at": e.predicted_at,
                "predicted_arrival": e.predicted_arrival,
                "actual_arrival": e.actual_arrival,
                "prediction_horizon_s": e.prediction_horizon_s,
                "error_s": e.error_s,
            }
            for e in eta_predictions
        ],
    }
