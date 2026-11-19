from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_write_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.audit import AuditLogResponse
from app.services.audit_service import count_audit_logs, get_audit_logs
from app.utils.auth import require_role
from app.utils.tenant_context import get_current_tenant_id

router = APIRouter(prefix="/audit-logs", tags=["audit"])

AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.OWNER))]


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
    response: Response,
    action: str | None = Query(None, description="Filter by action string, e.g. auth.login"),
    actor_user_id: uuid.UUID | None = Query(None, description="Filter by acting user UUID"),
    resource_type: str | None = Query(None, description="Filter by resource type, e.g. order"),
    date_from: datetime | None = Query(None, description="Include events created on or after this timestamp"),
    date_to: datetime | None = Query(None, description="Include events created on or before this timestamp"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Return paginated audit log entries for the current tenant (admin only)."""
    tenant_id = get_current_tenant_id(current_user)

    total = await count_audit_logs(
        db,
        tenant_id=tenant_id,
        action=action,
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        date_from=date_from,
        date_to=date_to,
    )
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"

    return await get_audit_logs(
        db,
        tenant_id=tenant_id,
        action=action,
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=limit,
    )
