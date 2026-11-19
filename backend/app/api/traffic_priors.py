"""Admin endpoints for the historical traffic prior table.

All endpoints require the X-Superadmin-Key header. The prior table is
global infrastructure (not tenant-scoped), so management lives under
the platform superadmin surface alongside `admin.py`.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import SuperadminDep
from app.config import settings
from app.database import get_write_db
from app.services import traffic_prior_service, traffic_profile_service
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/admin/traffic-priors", tags=["Superadmin"])


class PriorStatusResponse(BaseModel):
    region_code: str
    prior_enabled: bool
    total_cells: int
    total_buckets: int
    source_first_seen: date | None
    source_last_seen: date | None
    last_computed_at: datetime | None
    extrapolated_pct: float


class PriorRefreshResponse(BaseModel):
    region_code: str
    task_id: str
    status: str


class PriorCacheInvalidationResponse(BaseModel):
    status: str = "ok"


@router.get("/status", response_model=PriorStatusResponse)
async def get_traffic_priors_status(
    _: SuperadminDep,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    region_code: Annotated[str | None, Query()] = None,
) -> PriorStatusResponse:
    target = region_code or settings.TRAFFIC_PRIOR_ACTIVE_REGION_CODE
    status_obj = await traffic_prior_service.get_prior_status(db, region_code=target)
    return PriorStatusResponse(
        region_code=status_obj.region_code,
        prior_enabled=status_obj.prior_enabled,
        total_cells=status_obj.total_cells,
        total_buckets=status_obj.total_buckets,
        source_first_seen=status_obj.source_first_seen,
        source_last_seen=status_obj.source_last_seen,
        last_computed_at=status_obj.last_computed_at,
        extrapolated_pct=status_obj.extrapolated_pct,
    )


@router.post("/refresh", response_model=PriorRefreshResponse)
async def refresh_traffic_priors(
    _: SuperadminDep,
    region_code: Annotated[str | None, Query()] = None,
) -> PriorRefreshResponse:
    target = region_code or settings.TRAFFIC_PRIOR_ACTIVE_REGION_CODE
    async_result = celery_app.send_task(
        "app.workers.traffic_prior_tasks.refresh_traffic_priors",
        kwargs={"region_code": target},
    )
    return PriorRefreshResponse(
        region_code=target,
        task_id=async_result.id,
        status="queued",
    )


@router.post("/invalidate-cache", response_model=PriorCacheInvalidationResponse)
async def invalidate_prior_cache(
    _: SuperadminDep,
) -> PriorCacheInvalidationResponse:
    traffic_profile_service.invalidate_lookup_cache()
    return PriorCacheInvalidationResponse()
