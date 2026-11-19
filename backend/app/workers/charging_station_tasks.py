"""Celery wrapper around the Open Charge Map sync (Phase 2.3).

Mirrors the energy_price_tasks.py shape: bound task with retry policy,
sync→async via `_run_async`, and a single underscore-prefixed coroutine
that owns the DB session.
"""
from __future__ import annotations

import asyncio
import logging

from app import database
from app.services.charging_station_service import refresh_open_charge_map
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


@celery_app.task(
    name="app.workers.charging_station_tasks.refresh_charging_stations",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def refresh_charging_stations(self) -> dict:
    def run() -> dict:
        try:
            return _run_async(_refresh())
        except Exception as exc:
            logger.exception("Charging-station sync failed")
            if self.request.retries < self.max_retries:
                raise self.retry(exc=exc)
            raise

    with bind_task_request_id(self.request):
        return run()


async def _refresh() -> dict:
    async with database.celery_async_session() as db:
        result = await refresh_open_charge_map(db)
        await db.commit()
        return result
