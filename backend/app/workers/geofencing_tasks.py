"""Celery tasks for geofencing checks on courier location updates."""
from __future__ import annotations

import asyncio
import logging
import uuid

from app import database
from app.services import geofencing_service
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


@celery_app.task(name="app.workers.geofencing_tasks.process_geofence_update", bind=True)
def process_geofence_update(self, courier_id: str, lat: float, lon: float) -> dict:
    with bind_task_request_id(self.request):
        logger.info("Geofence update task started [courier_id=%s]", courier_id)
        return _run_async(_process(courier_id, lat, lon))


async def _process(courier_id: str, lat: float, lon: float) -> dict:
    async with database.celery_async_session() as db:
        result = await geofencing_service.process_location_update(
            db,
            courier_id=uuid.UUID(courier_id),
            lat=lat,
            lon=lon,
        )
        await db.commit()
    return result
