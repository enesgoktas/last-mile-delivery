import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_write_db
from app.models.enums import UserRole, WebhookDeliveryStatus
from app.models.user import User
from app.schemas.webhook import (
    WebhookDeliveryLogResponse,
    WebhookEndpointCreate,
    WebhookEndpointCreatedResponse,
    WebhookEndpointResponse,
    WebhookEndpointUpdate,
    WebhookReplayResponse,
)
from app.services import webhook_service
from app.utils.auth import require_role

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN))]


@router.get("", response_model=list[WebhookEndpointResponse])
async def list_webhooks(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    return await webhook_service.list_webhooks(db, current_user)


@router.post("", response_model=WebhookEndpointCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookEndpointCreate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    endpoint, raw_secret = await webhook_service.create_webhook(db, payload, current_user)
    return WebhookEndpointCreatedResponse(
        id=endpoint.id,
        client_id=endpoint.client_id,
        url=endpoint.url,
        events=endpoint.events,
        is_active=endpoint.is_active,
        created_at=endpoint.created_at,
        secret=raw_secret,
    )


@router.put("/{webhook_id}", response_model=WebhookEndpointResponse)
async def update_webhook(
    webhook_id: uuid.UUID,
    payload: WebhookEndpointUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    return await webhook_service.update_webhook(db, webhook_id, payload, current_user)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    await webhook_service.delete_webhook(db, webhook_id, current_user)


@router.get("/{webhook_id}/logs", response_model=list[WebhookDeliveryLogResponse])
async def get_webhook_logs(
    webhook_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
    limit: int = Query(100, ge=1, le=500),
    delivery_status: WebhookDeliveryStatus | None = Query(None),
):
    return await webhook_service.get_webhook_logs(
        db,
        webhook_id,
        current_user,
        limit=limit,
        delivery_status=delivery_status,
    )


@router.post("/{webhook_id}/replay", response_model=WebhookReplayResponse)
async def replay_webhook_failures(
    webhook_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
    limit: int = Query(20, ge=1, le=200),
):
    """Requeue most recent failed webhook deliveries for replay."""
    count = await webhook_service.replay_failed_deliveries(db, webhook_id, current_user, limit=limit)
    return WebhookReplayResponse(webhook_id=webhook_id, requeued_count=count)
