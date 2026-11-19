"""Celery task: monthly refresh of IBB traffic priors.

Mirrors the shape of `charging_station_tasks.py`. The actual heavy
lifting lives in `traffic_prior_service.refresh_region`; this module
only owns the Celery wrapper, retry policy, and DB session.

The download step (fetching the latest IBB monthly CSV bundle from the
public open-data portal) is intentionally separate from the ingest. For
v1, this task expects CSVs to already be present at the configured
local path; an operator (or a separate downloader task) is responsible
for populating that directory before this task runs.
"""
from __future__ import annotations

import asyncio
import glob
import logging
from datetime import date
from pathlib import Path

from app import database
from app.config import settings
from app.services import traffic_prior_service, traffic_profile_service
from app.workers.celery_app import celery_app
from app.workers.task_request_context import bind_task_request_id

logger = logging.getLogger(__name__)

_DEFAULT_CSV_GLOB = "/var/data/ibb/*.csv"


def _run_async(coro):
    database._session_factory_override.set(database.celery_async_session)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="app.workers.traffic_prior_tasks.refresh_traffic_priors",
    bind=True,
    max_retries=1,
    default_retry_delay=600,
)
def refresh_traffic_priors(self, region_code: str | None = None) -> dict:
    region = region_code or settings.TRAFFIC_PRIOR_ACTIVE_REGION_CODE

    if not settings.TRAFFIC_PRIOR_ENABLED:
        logger.info("TRAFFIC_PRIOR_ENABLED is False; refresh skipped.")
        return {"region_code": region, "status": "disabled"}

    def run() -> dict:
        try:
            return _run_async(_refresh(region_code=region))
        except Exception as exc:
            logger.exception("IBB traffic-prior refresh failed for region=%s", region)
            if self.request.retries < self.max_retries:
                raise self.retry(exc=exc)
            raise

    with bind_task_request_id(self.request):
        return run()


async def _refresh(*, region_code: str) -> dict:
    csv_glob = _DEFAULT_CSV_GLOB
    paths = [Path(p) for p in sorted(glob.glob(csv_glob))]
    if not paths:
        logger.warning(
            "No IBB CSVs found at %s; refresh produced zero rows.", csv_glob
        )
        return {
            "region_code": region_code,
            "status": "no_csvs",
            "rows_processed": 0,
        }

    async with database.celery_async_session() as db:
        summary = await traffic_prior_service.refresh_region(
            db,
            region_code=region_code,
            csv_paths=paths,
            source_first_seen=_min_seen_date(paths),
            source_last_seen=date.today(),
        )

    # Drop the in-process LRU so the freshly-updated rows show up in the
    # next routing request without waiting for the 15-min TTL.
    traffic_profile_service.invalidate_lookup_cache()

    return {
        "region_code": summary.region_code,
        "status": "ok",
        "csv_files": len(summary.csv_paths),
        "rows_processed": summary.rows_processed,
        "extrapolated_buckets": summary.extrapolated_buckets,
        "duration_seconds": round(summary.duration_seconds, 2),
    }


def _min_seen_date(paths: list[Path]) -> date:
    """Best-effort estimate of the earliest data date from filenames.

    IBB monthly bundles are named like `traffic_density_YYYYMM.csv`.
    Fall back to a fixed 2020-01-01 if the pattern doesn't parse.
    """
    earliest = None
    for path in paths:
        stem = path.stem
        # Expect ..._YYYYMM at the end.
        for token in stem.split("_"):
            if len(token) == 6 and token.isdigit():
                try:
                    y = int(token[:4])
                    m = int(token[4:])
                    candidate = date(y, m, 1)
                    if earliest is None or candidate < earliest:
                        earliest = candidate
                except ValueError:
                    continue
    return earliest or date(2020, 1, 1)
