"""Granular RBAC management endpoints (Phase 8.2)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_write_db
from app.models.enums import UserRole
from app.models.role_permission import RolePermission
from app.models.user import User
from app.services import rbac_service
from app.utils.auth import require_role
from app.utils.tenant_context import get_current_tenant_id

router = APIRouter(prefix="/rbac", tags=["RBAC"])

AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN))]


class PermissionOverrideRequest(BaseModel):
    role: str
    permission: str
    granted: bool


class PermissionOverrideResponse(BaseModel):
    id: uuid.UUID
    role: str
    permission: str
    granted: bool


class EffectivePermissionsResponse(BaseModel):
    role: str
    permissions: dict[str, bool]


@router.get("/permissions", response_model=EffectivePermissionsResponse)
async def get_my_permissions(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(UserRole.COURIER))],
) -> EffectivePermissionsResponse:
    """Return the effective permissions for the current user (any role)."""
    effective = await rbac_service.list_effective_permissions(current_user, db)
    return EffectivePermissionsResponse(
        role=current_user.role.value,
        permissions=effective,
    )


@router.get("/overrides", response_model=list[PermissionOverrideResponse])
async def list_overrides(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: AdminDep,
) -> list[PermissionOverrideResponse]:
    """List all tenant-level permission overrides."""
    result = await db.execute(
        select(RolePermission).where(
            RolePermission.tenant_id == get_current_tenant_id(current_user)
        )
    )
    rows = result.scalars().all()
    return [
        PermissionOverrideResponse(
            id=r.id, role=r.role, permission=r.permission, granted=r.granted
        )
        for r in rows
    ]


@router.put("/overrides", response_model=PermissionOverrideResponse, status_code=status.HTTP_200_OK)
async def set_override(
    payload: PermissionOverrideRequest,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
) -> PermissionOverrideResponse:
    """Create or update a tenant-level permission override."""
    tenant_id = get_current_tenant_id(current_user)
    row = await rbac_service.set_permission_override(
        db,
        tenant_id=tenant_id,
        role=payload.role,
        permission=payload.permission,
        granted=payload.granted,
    )
    return PermissionOverrideResponse(
        id=row.id, role=row.role, permission=row.permission, granted=row.granted
    )


@router.delete("/overrides", status_code=status.HTTP_204_NO_CONTENT)
async def delete_override(
    role: str,
    permission: str,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Remove a tenant-level override, reverting to the system default."""
    tenant_id = get_current_tenant_id(current_user)
    await rbac_service.delete_permission_override(
        db, tenant_id=tenant_id, role=role, permission=permission
    )


@router.get("/permissions/all", response_model=list[str])
async def list_all_permissions(
    _: AdminDep,
) -> list[str]:
    """Return the complete list of known permission identifiers."""
    return sorted(rbac_service.ALL_PERMISSIONS)
