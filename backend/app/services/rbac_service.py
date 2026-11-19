"""Granular RBAC service (Phase 8.2).

Defines the full permission matrix and provides helpers for checking and
overriding permissions at the tenant level.

Permission identifiers use the format ``<resource>:<action>``, e.g.
``orders:create``, ``routes:approve``, ``analytics:read``.

Consumers call ``has_permission(user, permission, db)`` instead of (or in
addition to) ``require_role``.  This lets tenants grant fine-grained overrides
without changing the user's coarse role.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enums import UserRole
from app.models.role_permission import RolePermission
from app.models.user import User
from app.utils.auth import get_current_user

# ── Default permission matrix ─────────────────────────────────────────────────
# Keys are (role, permission) tuples. True = granted by default.
# Tenant overrides stored in RolePermission take precedence.

DEFAULT_PERMISSIONS: dict[tuple[str, str], bool] = {
    # orders
    ("OWNER",       "orders:read"):            True,
    ("OWNER",       "orders:create"):          True,
    ("OWNER",       "orders:update"):          True,
    ("OWNER",       "orders:cancel"):          True,
    ("ADMIN",       "orders:read"):            True,
    ("ADMIN",       "orders:create"):          True,
    ("ADMIN",       "orders:update"):          True,
    ("ADMIN",       "orders:cancel"):          True,
    ("DISPATCHER",  "orders:read"):            True,
    ("DISPATCHER",  "orders:create"):          True,
    ("DISPATCHER",  "orders:update"):          True,
    ("DISPATCHER",  "orders:cancel"):          False,
    ("COURIER",     "orders:read"):            True,   # own orders only (enforced at service layer)
    ("COURIER",     "orders:create"):          False,
    ("COURIER",     "orders:update"):          False,
    ("COURIER",     "orders:cancel"):          False,
    # routes
    ("OWNER",       "routes:read"):            True,
    ("OWNER",       "routes:plan"):            True,
    ("OWNER",       "routes:approve"):         True,
    ("OWNER",       "routes:abort"):           True,
    ("ADMIN",       "routes:read"):            True,
    ("ADMIN",       "routes:plan"):            True,
    ("ADMIN",       "routes:approve"):         True,
    ("ADMIN",       "routes:abort"):           True,
    ("DISPATCHER",  "routes:read"):            True,
    ("DISPATCHER",  "routes:plan"):            True,
    ("DISPATCHER",  "routes:approve"):         False,
    ("DISPATCHER",  "routes:abort"):           False,
    ("COURIER",     "routes:read"):            True,   # own route only
    ("COURIER",     "routes:plan"):            False,
    ("COURIER",     "routes:approve"):         False,
    ("COURIER",     "routes:abort"):           False,
    # couriers
    ("OWNER",       "couriers:manage"):        True,
    ("ADMIN",       "couriers:manage"):        True,
    ("DISPATCHER",  "couriers:manage"):        False,
    ("COURIER",     "couriers:manage"):        False,
    # analytics
    ("OWNER",       "analytics:read"):         True,
    ("ADMIN",       "analytics:read"):         True,
    ("DISPATCHER",  "analytics:read"):         True,
    ("COURIER",     "analytics:read"):         False,
    # settings / policy
    ("OWNER",       "settings:manage"):        True,
    ("ADMIN",       "settings:manage"):        True,
    ("DISPATCHER",  "settings:manage"):        False,
    ("COURIER",     "settings:manage"):        False,
    # users
    ("OWNER",       "users:manage"):           True,
    ("ADMIN",       "users:manage"):           True,
    ("DISPATCHER",  "users:manage"):           False,
    ("COURIER",     "users:manage"):           False,
    # audit log
    ("OWNER",       "audit:read"):             True,
    ("ADMIN",       "audit:read"):             True,
    ("DISPATCHER",  "audit:read"):             False,
    ("COURIER",     "audit:read"):             False,
    # webhooks
    ("OWNER",       "webhooks:manage"):        True,
    ("ADMIN",       "webhooks:manage"):        True,
    ("DISPATCHER",  "webhooks:manage"):        False,
    ("COURIER",     "webhooks:manage"):        False,
    # branding / SSO
    ("OWNER",       "branding:manage"):        True,
    ("ADMIN",       "branding:manage"):        True,
    ("DISPATCHER",  "branding:manage"):        False,
    ("COURIER",     "branding:manage"):        False,
    ("OWNER",       "oidc:manage"):            True,
    ("ADMIN",       "oidc:manage"):            True,
    ("DISPATCHER",  "oidc:manage"):            False,
    ("COURIER",     "oidc:manage"):            False,
}

# Full list of known permissions (for validation)
ALL_PERMISSIONS: frozenset[str] = frozenset(perm for _, perm in DEFAULT_PERMISSIONS)


async def has_permission(
    user: User,
    permission: str,
    db: AsyncSession,
) -> bool:
    """Check whether a user has a specific permission, respecting tenant overrides."""
    role = user.role.value

    # Check for a tenant-level override first
    result = await db.execute(
        select(RolePermission).where(
            RolePermission.tenant_id == user.tenant_id,
            RolePermission.role == role,
            RolePermission.permission == permission,
        )
    )
    override = result.scalar_one_or_none()
    if override is not None:
        return override.granted

    # Fall back to global defaults
    return DEFAULT_PERMISSIONS.get((role, permission), False)


def require_permission(permission: str) -> Callable[..., Awaitable[User]]:
    """FastAPI dependency factory: require the current user to have a specific permission."""

    async def _check(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if not await has_permission(current_user, permission, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required",
            )
        return current_user

    return _check


async def list_effective_permissions(
    user: User,
    db: AsyncSession,
) -> dict[str, bool]:
    """Return the full effective permission map for a user (defaults + overrides)."""
    role = user.role.value

    # Start with defaults
    effective = {
        perm: granted
        for (r, perm), granted in DEFAULT_PERMISSIONS.items()
        if r == role
    }

    # Apply tenant overrides
    result = await db.execute(
        select(RolePermission).where(
            RolePermission.tenant_id == user.tenant_id,
            RolePermission.role == role,
        )
    )
    for override in result.scalars().all():
        effective[override.permission] = override.granted

    return effective


async def set_permission_override(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    role: str,
    permission: str,
    granted: bool,
) -> RolePermission:
    """Create or update a tenant-level permission override."""
    if permission not in ALL_PERMISSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown permission '{permission}'. Valid permissions: {sorted(ALL_PERMISSIONS)}",
        )
    try:
        UserRole(role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown role '{role}'",
        ) from exc

    result = await db.execute(
        select(RolePermission).where(
            RolePermission.tenant_id == tenant_id,
            RolePermission.role == role,
            RolePermission.permission == permission,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = RolePermission(tenant_id=tenant_id, role=role, permission=permission, granted=granted)
        db.add(row)
    else:
        row.granted = granted

    await db.flush()
    return row


async def delete_permission_override(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    role: str,
    permission: str,
) -> None:
    """Remove a tenant-level override, reverting to the global default."""
    result = await db.execute(
        select(RolePermission).where(
            RolePermission.tenant_id == tenant_id,
            RolePermission.role == role,
            RolePermission.permission == permission,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.flush()
