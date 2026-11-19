from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit import AuditLog
from app.models.location import LocationSnapshot
from app.models.notification import Notification
from app.models.webhook import WebhookDeliveryLog
from app.utils.compat import utcnow_naive

logger = logging.getLogger(__name__)

_BATCH_SIZE = 5_000


def _cutoff(*, now: datetime, retention_days: int) -> datetime | None:
    if retention_days <= 0:
        return None
    return now - timedelta(days=retention_days)


async def _delete_before_batched(
    db: AsyncSession,
    model,
    timestamp_column,
    cutoff: datetime,
) -> int:
    """Delete rows older than *cutoff* in batches to avoid long table locks."""
    deleted_total = 0
    while True:
        subquery = (
            select(model.id)
            .where(timestamp_column < cutoff)
            .limit(_BATCH_SIZE)
            .scalar_subquery()
        )
        stmt = delete(model).where(model.id.in_(subquery))
        result = await db.execute(stmt)
        batch_count = int(result.rowcount or 0)
        deleted_total += batch_count
        if batch_count < _BATCH_SIZE:
            break
    return deleted_total


async def run_retention_cleanup(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or utcnow_naive()

    location_cutoff = _cutoff(now=now, retention_days=settings.LOCATION_RETENTION_DAYS)
    webhook_cutoff = _cutoff(now=now, retention_days=settings.WEBHOOK_LOG_RETENTION_DAYS)
    notification_cutoff = _cutoff(now=now, retention_days=settings.NOTIFICATION_RETENTION_DAYS)
    audit_cutoff = _cutoff(now=now, retention_days=settings.AUDIT_LOG_RETENTION_DAYS)

    deleted_location_snapshots = 0
    deleted_webhook_logs = 0
    deleted_notifications = 0
    deleted_audit_logs = 0

    if location_cutoff is not None:
        deleted_location_snapshots = await _delete_before_batched(
            db, LocationSnapshot, LocationSnapshot.recorded_at, location_cutoff,
        )

    if webhook_cutoff is not None:
        deleted_webhook_logs = await _delete_before_batched(
            db, WebhookDeliveryLog, WebhookDeliveryLog.sent_at, webhook_cutoff,
        )

    if notification_cutoff is not None:
        deleted_notifications = await _delete_before_batched(
            db, Notification, Notification.created_at, notification_cutoff,
        )

    if audit_cutoff is not None:
        deleted_audit_logs = await _delete_before_batched(
            db, AuditLog, AuditLog.created_at, audit_cutoff,
        )

    summary = {
        "deleted_location_snapshots": deleted_location_snapshots,
        "deleted_webhook_logs": deleted_webhook_logs,
        "deleted_notifications": deleted_notifications,
        "deleted_audit_logs": deleted_audit_logs,
        "cutoffs": {
            "location_recorded_before": (
                location_cutoff.isoformat() if location_cutoff else None
            ),
            "webhook_sent_before": webhook_cutoff.isoformat() if webhook_cutoff else None,
            "notification_created_before": (
                notification_cutoff.isoformat() if notification_cutoff else None
            ),
            "audit_created_before": audit_cutoff.isoformat() if audit_cutoff else None,
        },
    }
    logger.info("Retention cleanup completed: %s", summary)
    return summary
