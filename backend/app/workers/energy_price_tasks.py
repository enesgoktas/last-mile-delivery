from __future__ import annotations

import asyncio
import logging
from datetime import date

from app import database
from app.services.energy_price_service import refresh_turkey_energy_prices as refresh_turkey_energy_prices_service
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
    name="app.workers.energy_price_tasks.refresh_turkey_energy_prices",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def refresh_turkey_energy_prices(self, effective_date_iso: str | None = None) -> dict:
    def run() -> dict:
        target_date = date.fromisoformat(effective_date_iso) if effective_date_iso else None
        try:
            return _run_async(_refresh(target_date))
        except Exception as exc:
            logger.exception("Turkey energy price refresh failed")
            raise self.retry(exc=exc) if self.request.retries < self.max_retries else exc

    with bind_task_request_id(self.request):
        return run()


async def _refresh(target_date: date | None) -> dict:
    async with database.celery_async_session() as db:
        result = await refresh_turkey_energy_prices_service(db, effective_date=target_date)
        await db.commit()
        return result
