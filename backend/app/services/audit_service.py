from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def log_audit_event(
    db: AsyncSession,
    *,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    event = AuditLog(
        action=action,
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(event)
    await db.flush()
    return event


def _build_audit_filters(
    query,
    *,
    tenant_id: uuid.UUID | None,
    action: str | None,
    actor_user_id: uuid.UUID | None,
    resource_type: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
):
    if tenant_id is not None:
        query = query.where(AuditLog.tenant_id == tenant_id)
    if action:
        query = query.where(AuditLog.action == action)
    if actor_user_id is not None:
        query = query.where(AuditLog.actor_user_id == actor_user_id)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if date_from:
        query = query.where(AuditLog.created_at >= date_from)
    if date_to:
        query = query.where(AuditLog.created_at <= date_to)
    return query


async def count_audit_logs(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> int:
    q = _build_audit_filters(
        select(func.count()).select_from(AuditLog),
        tenant_id=tenant_id,
        action=action,
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        date_from=date_from,
        date_to=date_to,
    )
    result = await db.execute(q)
    return int(result.scalar_one())


async def get_audit_logs(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[AuditLog]:
    q = _build_audit_filters(
        select(AuditLog),
        tenant_id=tenant_id,
        action=action,
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        date_from=date_from,
        date_to=date_to,
    )
    q = q.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())
