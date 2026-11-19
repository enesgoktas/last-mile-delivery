from __future__ import annotations

import contextvars
import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import HTTPException, status

# Role names that bypass per-tenant scoping. Hard-coded as strings here to
# avoid a circular import with `app.models.enums` (which transitively pulls
# in `app.database`, which imports this module). Keep in sync with
# `app.models.enums.PLATFORM_ROLES`.
_PLATFORM_ROLE_NAMES = frozenset({"SUPERADMIN"})

TenantIdentity = Any
_tenant_scope_ctx: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "tenant_scope",
    default=None,
)


def get_current_tenant_id(subject: TenantIdentity) -> uuid.UUID:
    if isinstance(subject, uuid.UUID):
        return subject
    if isinstance(subject, str):
        return uuid.UUID(subject)

    tenant_id = getattr(subject, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context missing from authenticated principal",
        )
    if isinstance(tenant_id, uuid.UUID):
        return tenant_id
    return uuid.UUID(str(tenant_id))


def is_platform_role_subject(subject: TenantIdentity) -> bool:
    """Return True when *subject* is a User-like object with a platform-level role.

    Platform roles bypass per-tenant scoping — kept in sync with
    `app.models.enums.PLATFORM_ROLES`. UUIDs / strings always return False
    because we cannot determine a role from them alone.
    """
    role = getattr(subject, "role", None)
    if role is None:
        return False
    # Compare by name (StrEnum value) to dodge the circular import.
    role_name = getattr(role, "value", str(role))
    return role_name in _PLATFORM_ROLE_NAMES


def get_current_tenant_scope() -> uuid.UUID | None:
    return _tenant_scope_ctx.get()


def set_current_tenant_scope(subject: TenantIdentity | None) -> contextvars.Token[uuid.UUID | None]:
    tenant_id = None if subject is None else get_current_tenant_id(subject)
    return _tenant_scope_ctx.set(tenant_id)


def reset_current_tenant_scope(token: contextvars.Token[uuid.UUID | None]) -> None:
    _tenant_scope_ctx.reset(token)


def tenant_filter(model, subject: TenantIdentity):
    """Return a SQLAlchemy clause to scope queries to *subject*'s tenant.

    For platform-role subjects (SUPERADMIN), returns `sa.true()` — i.e. no
    filter — so cross-tenant queries don't restrict to the platform tenant.
    Endpoints that want to scope a SUPERADMIN's view to a specific tenant
    must do so explicitly via path/query parameters; this helper is only
    for "default tenant scope" behavior.
    """
    column = getattr(model, "tenant_id", None)
    if column is None:
        raise AttributeError(f"{model!r} does not expose tenant_id")
    if is_platform_role_subject(subject):
        return sa.true()
    return column == get_current_tenant_id(subject)
