"""Role-based superadmin control plane.

Distinct from `app/api/admin.py`, which uses an `X-Superadmin-Key` header
for cross-tenant programmatic access. This router uses the standard
JWT login flow gated to `UserRole.SUPERADMIN`, so platform admins (Doruk
and Murat, seeded in migration 0029) can manage tenants from a browser
dashboard.

Endpoints exposed:
  * `GET    /superadmin/tenants`                          — list all
  * `POST   /superadmin/tenants`                          — create tenant + initial OWNER user
  * `GET    /superadmin/tenants/{id}`                     — full detail
  * `PATCH  /superadmin/tenants/{id}`                     — update tenant fields
  * `GET    /superadmin/tenants/{id}/feature-flags`
  * `PATCH  /superadmin/tenants/{id}/feature-flags`
  * `GET    /superadmin/tenants/{id}/operations-policy`
  * `PATCH  /superadmin/tenants/{id}/operations-policy`
  * `POST   /superadmin/tenants/{id}/users`               — invite OWNER/ADMIN user
  * `GET    /superadmin/feature-flags/schema`             — drives the schema-driven UI

The legacy `/admin/*` API-key endpoints continue to work alongside this
router; nothing here removes them.
"""
from __future__ import annotations

import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, ValidationError
from sqlalchemy import case as sa_case
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_write_db
from app.models.enums import UserRole
from app.models.system_settings import SystemSettings
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.operations_policy import (
    FEATURE_FLAG_SCHEMA,
    FeatureFlags,
    OperationsPolicy,
    default_operations_policy_dict,
    feature_flags_schema,
)
from app.utils.auth import get_current_user, hash_password

router = APIRouter(prefix="/superadmin", tags=["Superadmin"])


# ── Auth dependency ────────────────────────────────────────────────────


async def require_superadmin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Reject anyone who isn't `UserRole.SUPERADMIN`."""
    if current_user.role != UserRole.SUPERADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required",
        )
    return current_user


SuperadminUser = Annotated[User, Depends(require_superadmin)]


# ── Pydantic IO models ────────────────────────────────────────────────


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TenantSummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    user_count: int
    feature_flags: FeatureFlags


class TenantDetail(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    logo_url: str | None
    primary_color: str | None
    secondary_color: str | None
    user_count: int
    feature_flags: FeatureFlags
    operations_policy: dict[str, Any]


class InitialUserInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.OWNER


class TenantCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    logo_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    feature_flags: FeatureFlags = Field(default_factory=FeatureFlags)
    operations_policy: dict[str, Any] | None = None  # full policy override; if None, defaults are used
    initial_user: InitialUserInput

    @classmethod
    def __get_validators__(cls):  # type: ignore[override]
        yield from super().__get_validators__()  # pragma: no cover

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        if not _SLUG_RE.match(self.slug):
            raise ValueError(
                "slug must be lowercase alphanumeric with hyphens (no spaces / uppercase)"
            )


class TenantUpdateInput(BaseModel):
    name: str | None = None
    slug: str | None = None
    is_active: bool | None = None
    logo_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        if self.slug is not None and not _SLUG_RE.match(self.slug):
            raise ValueError(
                "slug must be lowercase alphanumeric with hyphens (no spaces / uppercase)"
            )


class FeatureFlagsUpdate(BaseModel):
    """All flags optional — partial update merges into existing values."""

    deliveries_enabled: bool | None = None
    rides_enabled: bool | None = None
    ride_pickup_buffer_enabled: bool | None = None
    ride_strict_lateness_enabled: bool | None = None
    ride_airport_features_enabled: bool | None = None
    ride_no_show_handling_enabled: bool | None = None
    ev_battery_awareness_enabled: bool | None = None
    ev_low_battery_alerts_enabled: bool | None = None
    ev_auto_charging_enabled: bool | None = None
    multi_depot_vehicles_enabled: bool | None = None
    multi_trip_enabled: bool | None = None
    traffic_aware_routing_enabled: bool | None = None


class UserInviteInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.OWNER


class UserInviteResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole


# ── Helpers ───────────────────────────────────────────────────────────

# Every SELECT in this router is intentionally cross-tenant. The global ORM
# event listener in `app.database._apply_tenant_scope` would otherwise scope
# them to the SUPERADMIN's own (platform) tenant and silently return zero
# rows for everyone else. The listener honours `skip_tenant_scope=True` as
# its escape hatch.
_X = {"skip_tenant_scope": True}


async def _ensure_unique_slug(db: AsyncSession, slug: str, exclude_id: uuid.UUID | None = None) -> None:
    stmt = select(Tenant.id).where(Tenant.slug == slug)
    if exclude_id is not None:
        stmt = stmt.where(Tenant.id != exclude_id)
    result = await db.execute(stmt, execution_options=_X)
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Slug '{slug}' is already taken",
        )


async def _ensure_unique_user_email(
    db: AsyncSession, tenant_id: uuid.UUID, email: str
) -> None:
    result = await db.execute(
        select(User.id).where(User.tenant_id == tenant_id, User.email == email),
        execution_options=_X,
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User '{email}' already exists in this tenant",
        )


async def _load_or_create_settings(
    db: AsyncSession, tenant_id: uuid.UUID
) -> SystemSettings:
    """Fetch the SystemSettings row for a tenant or create a default one."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.tenant_id == tenant_id),
        execution_options=_X,
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings(
            tenant_id=tenant_id,
            global_constraints={},
            operations_policy=default_operations_policy_dict(),
        )
        db.add(row)
        await db.flush()
    return row


async def _user_count(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count(User.id)).where(User.tenant_id == tenant_id),
        execution_options=_X,
    )
    return int(result.scalar_one())


def _flags_from_policy_dict(policy: dict[str, Any]) -> FeatureFlags:
    raw = policy.get("feature_flags") or {}
    return FeatureFlags.model_validate(raw)


def _cascade_flag_dependencies(flags: dict[str, Any]) -> dict[str, Any]:
    """If a flag is False, also turn off every flag that depends on it.

    Repeats until stable so chains (A -> B -> C) propagate. Mutates and
    returns *flags*. Mirrors the cascade the frontend already does on
    toggle, so the API is safe to call directly without a client-side
    cascade pass.
    """
    while True:
        changed = False
        for entry in FEATURE_FLAG_SCHEMA:
            name = entry["name"]
            for dep in entry.get("depends_on", []) or ():
                if not flags.get(dep) and flags.get(name):
                    flags[name] = False
                    changed = True
        if not changed:
            return flags


def _validated_policy(policy: dict[str, Any]) -> None:
    """Validate *policy* through Pydantic and re-raise as HTTP 422 on failure.

    Pydantic ValidationError is a subclass of ValueError, so without this
    wrapper FastAPI returns 500 for cross-flag dependency violations
    raised inside our manual `model_validate` call.

    `errors()` includes a `ctx` mapping that may contain the underlying
    exception object — pruning to just `loc` + `msg` keeps the response
    JSON-serializable and ergonomic for clients.
    """
    try:
        OperationsPolicy.model_validate(policy)
    except ValidationError as exc:
        cleaned = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg", "")}
            for err in exc.errors()
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=cleaned,
        ) from exc


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/tenants", response_model=list[TenantSummary])
async def list_tenants(
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[TenantSummary]:
    rows = (
        await db.execute(
            select(Tenant, SystemSettings.operations_policy)
            .outerjoin(SystemSettings, SystemSettings.tenant_id == Tenant.id)
            .order_by(Tenant.created_at.desc()),
            execution_options=_X,
        )
    ).all()
    summaries: list[TenantSummary] = []
    for tenant, policy in rows:
        flags = _flags_from_policy_dict(policy or {})
        summaries.append(
            TenantSummary(
                id=tenant.id,
                name=tenant.name,
                slug=tenant.slug,
                is_active=tenant.is_active,
                user_count=await _user_count(db, tenant.id),
                feature_flags=flags,
            )
        )
    return summaries


@router.get("/tenants/{tenant_id}", response_model=TenantDetail)
async def get_tenant(
    tenant_id: uuid.UUID,
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantDetail:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    settings_row = await _load_or_create_settings(db, tenant_id)
    flags = _flags_from_policy_dict(settings_row.operations_policy or {})
    return TenantDetail(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        is_active=tenant.is_active,
        logo_url=tenant.logo_url,
        primary_color=tenant.primary_color,
        secondary_color=tenant.secondary_color,
        user_count=await _user_count(db, tenant.id),
        feature_flags=flags,
        operations_policy=settings_row.operations_policy or default_operations_policy_dict(),
    )


@router.post("/tenants", response_model=TenantDetail, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreateInput,
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> TenantDetail:
    await _ensure_unique_slug(db, payload.slug)

    # 1. Tenant row
    tenant = Tenant(
        id=uuid.uuid4(),
        name=payload.name,
        slug=payload.slug,
        is_active=True,
        logo_url=payload.logo_url,
        primary_color=payload.primary_color,
        secondary_color=payload.secondary_color,
    )
    db.add(tenant)
    await db.flush()

    # 2. system_settings — start from defaults, overlay the requested
    #    feature_flags, and overlay any operations_policy fields from the
    #    payload (the wizard sends pickup_buffer / battery_threshold etc.
    #    as part of operations_policy when relevant).
    base_policy = default_operations_policy_dict()
    if payload.operations_policy:
        # shallow merge at the top level — caller should send full sub-objects
        for key, value in payload.operations_policy.items():
            base_policy[key] = value
    flags_dict = _cascade_flag_dependencies(payload.feature_flags.model_dump())
    base_policy["feature_flags"] = flags_dict
    # Validate the merged policy through Pydantic so any flag-dependency
    # violations surface as 422 here.
    _validated_policy(base_policy)

    settings_row = SystemSettings(
        tenant_id=tenant.id,
        global_constraints={},
        operations_policy=base_policy,
    )
    db.add(settings_row)

    # 3. Initial OWNER user
    await _ensure_unique_user_email(db, tenant.id, payload.initial_user.email)
    initial_user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=payload.initial_user.email,
        hashed_password=hash_password(payload.initial_user.password),
        role=payload.initial_user.role,
        is_active=True,
        email_verified=True,
    )
    db.add(initial_user)

    await db.flush()

    return TenantDetail(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        is_active=tenant.is_active,
        logo_url=tenant.logo_url,
        primary_color=tenant.primary_color,
        secondary_color=tenant.secondary_color,
        user_count=1,
        feature_flags=payload.feature_flags,
        operations_policy=base_policy,
    )


@router.patch("/tenants/{tenant_id}", response_model=TenantDetail)
async def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantUpdateInput,
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> TenantDetail:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if payload.slug is not None and payload.slug != tenant.slug:
        await _ensure_unique_slug(db, payload.slug, exclude_id=tenant_id)
        tenant.slug = payload.slug
    if payload.name is not None:
        tenant.name = payload.name
    if payload.is_active is not None:
        tenant.is_active = payload.is_active
    if payload.logo_url is not None:
        tenant.logo_url = payload.logo_url
    if payload.primary_color is not None:
        tenant.primary_color = payload.primary_color
    if payload.secondary_color is not None:
        tenant.secondary_color = payload.secondary_color
    await db.flush()
    return await get_tenant(tenant_id, _, db)  # type: ignore[arg-type]


@router.get("/tenants/{tenant_id}/feature-flags", response_model=FeatureFlags)
async def get_feature_flags(
    tenant_id: uuid.UUID,
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FeatureFlags:
    settings_row = await _load_or_create_settings(db, tenant_id)
    return _flags_from_policy_dict(settings_row.operations_policy or {})


@router.patch("/tenants/{tenant_id}/feature-flags", response_model=FeatureFlags)
async def update_feature_flags(
    tenant_id: uuid.UUID,
    update: FeatureFlagsUpdate,
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> FeatureFlags:
    settings_row = await _load_or_create_settings(db, tenant_id)
    policy = dict(settings_row.operations_policy or default_operations_policy_dict())
    current_flags = _flags_from_policy_dict(policy).model_dump()
    # Merge in only the fields the caller set (None means "leave alone").
    for key, value in update.model_dump(exclude_none=True).items():
        current_flags[key] = value
    # Cascade dependent flags off when their parent goes off — keeps the
    # tenant in a coherent state even if the caller only sent the parent.
    current_flags = _cascade_flag_dependencies(current_flags)
    policy["feature_flags"] = current_flags
    _validated_policy(policy)
    settings_row.operations_policy = policy
    await db.flush()
    return FeatureFlags.model_validate(current_flags)


@router.get("/tenants/{tenant_id}/operations-policy")
async def get_operations_policy(
    tenant_id: uuid.UUID,
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    settings_row = await _load_or_create_settings(db, tenant_id)
    return settings_row.operations_policy or default_operations_policy_dict()


@router.patch("/tenants/{tenant_id}/operations-policy")
async def update_operations_policy(
    tenant_id: uuid.UUID,
    payload: dict[str, Any],
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> dict[str, Any]:
    settings_row = await _load_or_create_settings(db, tenant_id)
    base = dict(settings_row.operations_policy or default_operations_policy_dict())
    # Top-level merge — caller sends the full sub-objects they want to update.
    for key, value in payload.items():
        base[key] = value
    if isinstance(base.get("feature_flags"), dict):
        base["feature_flags"] = _cascade_flag_dependencies(dict(base["feature_flags"]))
    _validated_policy(base)
    settings_row.operations_policy = base
    await db.flush()
    return base


@router.post(
    "/tenants/{tenant_id}/users",
    response_model=UserInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_tenant_user(
    tenant_id: uuid.UUID,
    payload: UserInviteInput,
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> UserInviteResponse:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if payload.role == UserRole.SUPERADMIN:
        # Platform-level role; not assignable to a customer tenant via this
        # endpoint. Seed migrations are the only path.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SUPERADMIN cannot be assigned to a customer tenant",
        )
    await _ensure_unique_user_email(db, tenant_id, payload.email)
    new_user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        is_active=True,
        email_verified=True,
    )
    db.add(new_user)
    await db.flush()
    return UserInviteResponse(id=new_user.id, email=new_user.email, role=new_user.role)


@router.get("/feature-flags/schema")
async def get_feature_flags_schema(_: SuperadminUser) -> list[dict[str, Any]]:
    """Return the feature-flag schema list for the admin UI to render."""
    return feature_flags_schema()


@router.post("/charging-stations/sync")
async def trigger_charging_station_sync(
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> dict[str, Any]:
    """Manually trigger an Open Charge Map refresh.

    Runs the same sync as the daily Celery beat task, but synchronously
    inside the request so the caller gets the result. Useful when first
    enabling auto-charging for a tenant or when investigating sync issues.
    """
    from app.services.charging_station_service import refresh_open_charge_map

    return await refresh_open_charge_map(db)


@router.get("/charging-stations/sync-status")
async def charging_station_sync_status(
    _: SuperadminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Last-sync timestamp + station-count summary across the platform.

    Cross-tenant; uses the same skip_tenant_scope bypass as the rest of
    this router so platform-shared rows (tenant_id NULL) are visible.
    """
    from app.models.charging_station import ChargingStation

    rows = (
        await db.execute(
            select(
                ChargingStation.provider,
                func.count(ChargingStation.id).label("total"),
                func.sum(
                    sa_case((ChargingStation.is_active.is_(True), 1), else_=0)
                ).label("active"),
                func.max(ChargingStation.last_synced_at).label("last_synced_at"),
            ).group_by(ChargingStation.provider),
            execution_options=_X,
        )
    ).all()

    return {
        "by_provider": [
            {
                "provider": r.provider,
                "total": int(r.total or 0),
                "active": int(r.active or 0),
                "last_synced_at": r.last_synced_at.isoformat() + "Z"
                if r.last_synced_at
                else None,
            }
            for r in rows
        ],
    }
