from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_write_db
from app.schemas.tracking import TrackingRescheduleRequest, TrackingResponse
from app.services import tracking_service
from app.utils.client_ip import get_request_client_ip
from app.utils.rate_limit import enforce_fixed_window_limit

router = APIRouter()


@router.get("/{token}", response_model=TrackingResponse)
async def get_tracking(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Public customer tracking endpoint. No auth needed — token is a signed JWT."""
    client_ip = get_request_client_ip(request, default="unknown")
    await enforce_fixed_window_limit(
        key=f"rl:tracking:read:{client_ip}",
        limit=settings.PUBLIC_TRACKING_READ_RATE_LIMIT_PER_MIN,
        window_seconds=60,
    )
    return await tracking_service.get_tracking_data(db, token)


@router.post("/{token}/reschedule", response_model=TrackingResponse)
async def reschedule_with_token(
    token: str,
    body: TrackingRescheduleRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Public self-service rescheduling endpoint scoped by tracking token."""
    client_ip = get_request_client_ip(request, default="unknown")
    await enforce_fixed_window_limit(
        key=f"rl:tracking:write:{client_ip}",
        limit=settings.PUBLIC_TRACKING_WRITE_RATE_LIMIT_PER_HOUR,
        window_seconds=3600,
    )
    await tracking_service.reschedule_by_tracking_token(
        db,
        token,
        earliest_delivery=body.earliest_delivery,
        latest_delivery=body.latest_delivery,
        customer_instructions=body.customer_instructions,
    )
    return await tracking_service.get_tracking_data(db, token)
