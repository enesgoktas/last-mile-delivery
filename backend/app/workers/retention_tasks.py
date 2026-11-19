"""Celery tasks for data retention cleanup jobs."""
from __future__ import annotations

import asyncio
import logging

from app import database
from app.services import data_retention_service
from app.workers.celery_app import celery_app
from app.workers.task_request_context import bind_task_request_id

logger = logging.getLogger(__name__)
celery_async_session = database.celery_async_session


def _run_async(coro):
    database._session_factory_override.set(celery_async_session)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.workers.retention_tasks.run_data_retention_cleanup", bind=True)
def run_data_retention_cleanup(self) -> dict:
    with bind_task_request_id(self.request):
        logger.info("Data retention cleanup task started")
        return _run_async(_cleanup())


async def _cleanup() -> dict:
    async with celery_async_session() as db:
        result = await data_retention_service.run_retention_cleanup(db)
        await db.commit()
    return result
