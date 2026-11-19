from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import FailedOrderCustomerResponseStatus, OrderStatus
from app.models.order import Order
from app.services.audit_service import log_audit_event
from app.utils.compat import utcnow_naive
from app.workers.task_dispatch import enqueue_task

logger = logging.getLogger(__name__)


async def escalate_stale_failed_orders(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or utcnow_naive()
    result = await db.execute(
        select(Order).where(
            Order.status == OrderStatus.FAILED,
            Order.customer_response_status == FailedOrderCustomerResponseStatus.PENDING,
            Order.customer_response_due_at.is_not(None),
            Order.customer_response_due_at <= now,
        )
    )
    stale_orders = list(result.scalars().all())
    if not stale_orders:
        return {"escalated_count": 0, "order_ids": []}

    escalated_order_ids: list[uuid.UUID] = []
    for order in stale_orders:
        order.customer_response_status = FailedOrderCustomerResponseStatus.ESCALATED
        order.escalated_at = now
        await log_audit_event(
            db,
            action="failed_delivery.escalated",
            tenant_id=order.tenant_id,
            resource_type="order",
            resource_id=str(order.id),
            details={
                "order_id": str(order.id),
                "external_ref": order.external_ref,
                "failed_at": order.failed_at.isoformat() if order.failed_at else None,
                "customer_response_due_at": (
                    order.customer_response_due_at.isoformat()
                    if order.customer_response_due_at
                    else None
                ),
            },
        )

        try:
            from app.workers.webhook_tasks import dispatch_webhook_event

            enqueue_task(
                dispatch_webhook_event,
                args=[
                    "order.failed.escalated",
                    {
                        "order_id": str(order.id),
                        "tenant_id": str(order.tenant_id),
                        "external_ref": order.external_ref,
                        "status": order.status.value,
                        "customer_response_status": order.customer_response_status.value,
                        "escalated_at": now.isoformat() + "Z",
                        "timestamp": now.isoformat() + "Z",
                    },
                ],
            )
        except Exception:
            # Escalation state change must remain durable even if downstream delivery fails.
            logger.exception(
                "Failed to enqueue escalation webhook for order %s",
                order.id,
            )

        escalated_order_ids.append(order.id)

    await db.flush()
    return {"escalated_count": len(escalated_order_ids), "order_ids": escalated_order_ids}
