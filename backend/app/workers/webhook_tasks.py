"""Celery tasks for outbound webhook delivery."""
from __future__ import annotations

import asyncio
import logging
import uuid

from app import database
from app.config import settings
from app.services import webhook_service
from app.utils.perf_metrics import perf_metrics
from app.workers.celery_app import celery_app
from app.workers.task_dispatch import enqueue_task
from app.workers.task_request_context import bind_task_request_id

logger = logging.getLogger(__name__)


def _run_async(coro):
    database._session_factory_override.set(database.celery_async_session)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.workers.webhook_tasks.dispatch_webhook_event", bind=True)
def dispatch_webhook_event(self, event_type: str, payload: dict) -> dict:
    with bind_task_request_id(self.request):
        logger.info("Webhook dispatch task started [event=%s]", event_type)
        return _run_async(_dispatch(event_type, payload))


@celery_app.task(name="app.workers.webhook_tasks.deliver_webhook", bind=True)
def deliver_webhook(self, endpoint_id: str, event_type: str, payload: dict, attempt: int = 1) -> dict:
    with bind_task_request_id(self.request):
        logger.info(
            "Webhook delivery task started [endpoint_id=%s, event=%s, attempt=%d]",
            endpoint_id,
            event_type,
            attempt,
        )
        return _run_async(_deliver(endpoint_id, event_type, payload, attempt))


async def _dispatch(event_type: str, payload: dict) -> dict:
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        logger.warning("Webhook dispatch skipped because payload has no tenant_id.")
        perf_metrics.increment("webhook_dispatch_missing_tenant")
        return {"event": event_type, "enqueued": 0}
    async with database.celery_async_session() as db:
        endpoints = await webhook_service.list_endpoints_for_event(db, event_type, tenant_id)

    for endpoint in endpoints:
        enqueue_task(deliver_webhook, args=[str(endpoint.id), event_type, payload, 1])

    perf_metrics.increment("webhook_deliveries_enqueued", by=len(endpoints))
    return {"event": event_type, "enqueued": len(endpoints)}


async def _deliver(endpoint_id: str, event_type: str, payload: dict, attempt: int) -> dict:
    async with database.celery_async_session() as db:
        success, status_code, next_retry_at, delivery_status = await webhook_service.deliver_webhook(
            db,
            endpoint_id=uuid.UUID(endpoint_id),
            event_type=event_type,
            payload=payload,
            attempt_number=attempt,
        )
        await db.commit()

    if not success and attempt < settings.WEBHOOK_MAX_ATTEMPTS:
        delay_seconds = webhook_service.calculate_webhook_backoff_seconds(attempt)
        enqueue_task(
            deliver_webhook,
            args=[endpoint_id, event_type, payload, attempt + 1],
            countdown=delay_seconds,
        )

    return {
        "endpoint_id": endpoint_id,
        "event": event_type,
        "success": success,
        "response_status": status_code,
        "delivery_status": delivery_status.value,
        "next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
        "attempt": attempt,
    }
