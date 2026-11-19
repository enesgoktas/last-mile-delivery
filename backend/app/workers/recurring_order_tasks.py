"""Celery tasks for recurring-order materialisation.

A single beat task — `materialise_recurring_orders` — walks every active
template and creates due `Order` rows.  The service-layer logic
(`recurring_order_service.materialise_due_occurrences`) is fully idempotent,
so the cadence here just controls *latency* (how soon a newly-due occurrence
shows up in the orders table), not correctness.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app import database
from app.workers.celery_app import celery_app
from app.workers.task_request_context import bind_task_request_id

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from a sync Celery task with NullPool sessions."""
    database._session_factory_override.set(database.celery_async_session)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="app.workers.recurring_order_tasks.materialise_recurring_orders",
    bind=True,
    max_retries=1,
    soft_time_limit=240,
    time_limit=300,
)
def materialise_recurring_orders(self) -> dict[str, Any]:
    """Generate due orders for every active recurring template.

    Idempotent (`recurring_order_occurrences` has a unique constraint on
    `(template_id, occurrence_date)`), so it's safe to run on a beat schedule
    even when overlapping with manual `/recurring-orders/run` calls from the UI.
    """
    with bind_task_request_id(self.request):
        try:
            result = _run_async(_materialise_all())
            logger.info("Recurring-order materialisation completed: %s", result)
            return result
        except Exception as exc:
            logger.exception("Recurring-order materialisation failed: %s", exc)
            raise self.retry(exc=exc, countdown=60)


async def _materialise_all() -> dict[str, Any]:
    from app.services.recurring_order_service import materialise_due_occurrences

    async with database.celery_async_session() as db:
        stats = await materialise_due_occurrences(db)
        await db.commit()
        return {
            "templates_evaluated": stats.templates_evaluated,
            "occurrences_created": stats.occurrences_created,
            "occurrences_skipped_duplicate": stats.occurrences_skipped_duplicate,
            "occurrences_skipped_capped": stats.occurrences_skipped_capped,
        }
