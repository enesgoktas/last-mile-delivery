from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.enums import WebhookDeliveryStatus
from app.models.webhook import ApiClient, WebhookDeliveryLog, WebhookEndpoint
from app.schemas.webhook import WebhookEndpointCreate, WebhookEndpointUpdate
from app.utils.compat import utcnow_naive
from app.utils.perf_metrics import perf_metrics
from app.utils.tenant_context import get_current_tenant_id, tenant_filter
from app.utils.webhooks import decrypt_webhook_secret, encrypt_webhook_secret
from app.workers.task_dispatch import enqueue_task


def _sign_payload(secret: str, payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def list_webhooks(db: AsyncSession, principal) -> list[WebhookEndpoint]:
    result = await db.execute(
        select(WebhookEndpoint)
        .where(tenant_filter(WebhookEndpoint, principal))
        .order_by(WebhookEndpoint.created_at.desc())
    )
    return list(result.scalars().all())


async def create_webhook(db: AsyncSession, data: WebhookEndpointCreate, principal) -> tuple[WebhookEndpoint, str]:
    client_result = await db.execute(
        select(ApiClient).where(
            ApiClient.id == data.client_id,
            tenant_filter(ApiClient, principal),
        )
    )
    client = client_result.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API client not found")

    raw_secret = data.secret or secrets.token_urlsafe(32)
    endpoint = WebhookEndpoint(
        tenant_id=get_current_tenant_id(principal),
        client_id=data.client_id,
        url=data.url,
        events=data.events,
        secret=encrypt_webhook_secret(raw_secret),
        is_active=True,
    )
    db.add(endpoint)
    await db.flush()
    await db.refresh(endpoint)
    return endpoint, raw_secret


async def update_webhook(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    data: WebhookEndpointUpdate,
    principal,
) -> WebhookEndpoint:
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == webhook_id,
            tenant_filter(WebhookEndpoint, principal),
        )
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(endpoint, key, value)

    await db.flush()
    await db.refresh(endpoint)
    return endpoint


async def delete_webhook(db: AsyncSession, webhook_id: uuid.UUID, principal) -> None:
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == webhook_id,
            tenant_filter(WebhookEndpoint, principal),
        )
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    await db.delete(endpoint)
    await db.flush()


async def get_webhook_logs(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    principal,
    limit: int = 100,
    delivery_status: WebhookDeliveryStatus | None = None,
) -> list[WebhookDeliveryLog]:
    query = (
        select(WebhookDeliveryLog)
        .where(
            WebhookDeliveryLog.webhook_endpoint_id == webhook_id,
            tenant_filter(WebhookDeliveryLog, principal),
        )
        .order_by(WebhookDeliveryLog.sent_at.desc())
        .limit(limit)
    )
    if delivery_status:
        query = query.where(WebhookDeliveryLog.delivery_status == delivery_status)
    result = await db.execute(query)
    return list(result.scalars().all())


async def replay_failed_deliveries(
    db: AsyncSession,
    webhook_id: uuid.UUID,
    principal,
    *,
    limit: int = 20,
) -> int:
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == webhook_id,
            tenant_filter(WebhookEndpoint, principal),
        )
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    failed_result = await db.execute(
        select(WebhookDeliveryLog)
        .where(
            WebhookDeliveryLog.webhook_endpoint_id == webhook_id,
            WebhookDeliveryLog.delivery_status == WebhookDeliveryStatus.DEAD_LETTER,
        )
        .order_by(WebhookDeliveryLog.sent_at.desc())
        .limit(limit)
    )
    failed_logs = list(failed_result.scalars().all())
    if not failed_logs:
        return 0

    from app.workers.webhook_tasks import deliver_webhook

    for log in failed_logs:
        payload = log.payload.get("data", {}) if isinstance(log.payload, dict) else {}
        enqueue_task(
            deliver_webhook,
            args=[str(webhook_id), log.event_type, payload, 1],
        )

    return len(failed_logs)


async def list_endpoints_for_event(
    db: AsyncSession,
    event_type: str,
    tenant_id,
) -> list[WebhookEndpoint]:
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.is_active.is_(True),
            WebhookEndpoint.events.contains([event_type]),
            tenant_filter(WebhookEndpoint, tenant_id),
        )
    )
    return list(result.scalars().all())


def calculate_webhook_backoff_seconds(attempt_number: int) -> int:
    if attempt_number <= 0:
        return settings.WEBHOOK_BACKOFF_BASE_SECONDS
    raw = settings.WEBHOOK_BACKOFF_BASE_SECONDS * (2 ** (attempt_number - 1))
    return min(raw, settings.WEBHOOK_BACKOFF_MAX_SECONDS)


def resolve_webhook_delivery_status(
    *,
    success: bool,
    attempt_number: int,
    max_attempts: int,
) -> WebhookDeliveryStatus:
    if success:
        return WebhookDeliveryStatus.SUCCESS
    if attempt_number < max_attempts:
        return WebhookDeliveryStatus.RETRY_SCHEDULED
    return WebhookDeliveryStatus.DEAD_LETTER


def _record_webhook_delivery_metrics(
    *,
    delivery_status: WebhookDeliveryStatus,
    latency_ms: float,
) -> None:
    perf_metrics.increment("webhook_delivery_attempts")
    perf_metrics.observe_ms("webhook_delivery_latency_ms", latency_ms)

    if delivery_status == WebhookDeliveryStatus.SUCCESS:
        perf_metrics.increment("webhook_delivery_success")
    elif delivery_status == WebhookDeliveryStatus.RETRY_SCHEDULED:
        perf_metrics.increment("webhook_delivery_retry_scheduled")
    else:
        perf_metrics.increment("webhook_delivery_dead_letter")


async def deliver_webhook(
    db: AsyncSession,
    endpoint_id: uuid.UUID,
    event_type: str,
    payload: dict,
    attempt_number: int,
) -> tuple[bool, int | None, datetime | None, WebhookDeliveryStatus]:
    result = await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id))
    endpoint = result.scalar_one_or_none()
    if not endpoint or not endpoint.is_active:
        perf_metrics.increment("webhook_delivery_skipped")
        return True, None, None, WebhookDeliveryStatus.SUCCESS

    started = time.monotonic()
    timestamp = utcnow_naive()
    envelope = {
        "event": event_type,
        "timestamp": timestamp.isoformat() + "Z",
        "data": payload,
    }
    try:
        signing_secret = decrypt_webhook_secret(endpoint.secret)
    except ValueError as exc:
        response_status = None
        next_retry_at = None
        delivery_status = resolve_webhook_delivery_status(
            success=False,
            attempt_number=attempt_number,
            max_attempts=settings.WEBHOOK_MAX_ATTEMPTS,
        )
        if delivery_status == WebhookDeliveryStatus.RETRY_SCHEDULED:
            delay_seconds = calculate_webhook_backoff_seconds(attempt_number)
            next_retry_at = utcnow_naive() + timedelta(seconds=delay_seconds)
        log = WebhookDeliveryLog(
            tenant_id=endpoint.tenant_id,
            webhook_endpoint_id=endpoint.id,
            event_type=event_type,
            payload=envelope,
            attempt_number=attempt_number,
            delivery_status=delivery_status,
            response_status=response_status,
            error_message=f"Invalid encrypted webhook secret: {exc}",
            next_retry_at=next_retry_at,
        )
        db.add(log)
        await db.flush()
        _record_webhook_delivery_metrics(
            delivery_status=delivery_status,
            latency_ms=(time.monotonic() - started) * 1000,
        )
        return False, response_status, next_retry_at, delivery_status

    signature = _sign_payload(signing_secret, envelope)

    response_status = None
    next_retry_at = None
    success = False
    error_message = None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                endpoint.url,
                json=envelope,
                headers={"X-Webhook-Signature": signature},
            )
            response_status = resp.status_code
            success = 200 <= resp.status_code < 300
            if not success:
                error_message = f"Webhook returned status {resp.status_code}"
    except Exception as exc:
        success = False
        error_message = str(exc)

    delivery_status = resolve_webhook_delivery_status(
        success=success,
        attempt_number=attempt_number,
        max_attempts=settings.WEBHOOK_MAX_ATTEMPTS,
    )

    if delivery_status == WebhookDeliveryStatus.RETRY_SCHEDULED:
        delay_seconds = calculate_webhook_backoff_seconds(attempt_number)
        next_retry_at = utcnow_naive() + timedelta(seconds=delay_seconds)

    log = WebhookDeliveryLog(
        tenant_id=endpoint.tenant_id,
        webhook_endpoint_id=endpoint.id,
        event_type=event_type,
        payload=envelope,
        attempt_number=attempt_number,
        delivery_status=delivery_status,
        response_status=response_status,
        error_message=error_message,
        next_retry_at=next_retry_at,
    )
    db.add(log)
    await db.flush()
    _record_webhook_delivery_metrics(
        delivery_status=delivery_status,
        latency_ms=(time.monotonic() - started) * 1000,
    )

    return success, response_status, next_retry_at, delivery_status
