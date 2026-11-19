"""Celery tasks for failed-order no-response escalations."""
from __future__ import annotations

import asyncio
import logging

from app import database
from app.services import failed_delivery_service
from app.workers.celery_app import celery_app
from app.workers.task_request_context import bind_task_request_id

logger = logging.getLogger(__name__)


def _run_async(coro):
    database._session_factory_override.set(database.celery_async_session)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.workers.failed_delivery_tasks.escalate_stale_failed_orders", bind=True)
def escalate_stale_failed_orders(self) -> dict:
    with bind_task_request_id(self.request):
        logger.info("Failed-delivery escalation task started")
        return _run_async(_escalate())


async def _escalate() -> dict:
    async with database.celery_async_session() as db:
        result = await failed_delivery_service.escalate_stale_failed_orders(db)
        await db.commit()
    return {
        "escalated_count": result["escalated_count"],
        "order_ids": [str(order_id) for order_id in result["order_ids"]],
    }
