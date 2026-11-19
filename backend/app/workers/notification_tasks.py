"""Celery tasks for outbound customer/courier notifications."""
from __future__ import annotations

import asyncio
import logging

from app import database
from app.services import notification_service
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


@celery_app.task(name="app.workers.notification_tasks.dispatch_order_notifications", bind=True)
def dispatch_order_notifications(self, event_type: str, payload: dict) -> dict:
    with bind_task_request_id(self.request):
        logger.info("Notification dispatch task started [event=%s]", event_type)
        return _run_async(_dispatch_order_notifications(event_type, payload))


async def _dispatch_order_notifications(event_type: str, payload: dict) -> dict:
    async with database.celery_async_session() as db:
        results = await notification_service.dispatch_order_event_notifications(db, event_type, payload)
        await db.commit()
    return {"event": event_type, "notifications_processed": len(results)}
