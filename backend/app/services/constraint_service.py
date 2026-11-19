"""
Constraint resolution service.

Manages global default constraints and per-courier overrides.
At solve time, constraints are resolved by merging:
  hardcoded defaults <- global overrides <- per-courier overrides
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courier import Courier
from app.models.system_settings import SystemSettings
from app.utils.compat import utcnow_naive
from app.utils.tenant_context import get_current_tenant_id, tenant_filter

CONSTRAINT_DEFAULTS: dict[str, Any] = {
    "max_route_distance_km": None,
    "max_stops_per_route": 50,
    "max_weight_kg": 50.0,
    "max_volume_cm3": None,
    "max_packages": None,
    "max_trips_per_day": 3,
    "depot_reload_time_minutes": 15,
    "max_shift_hours": None,
    "break_duration_minutes": 30,
    "break_earliest_offset_hours": 3.0,
    "break_latest_offset_hours": 6.0,
    "break_required": True,
    "service_time_minutes": 5,
    "allow_fragile": True,
    "allow_temp_sensitive": True,
}

CONSTRAINT_TYPES: dict[str, tuple[type, ...]] = {
    "max_route_distance_km": (int, float, type(None)),
    "max_stops_per_route": (int, type(None)),
    "max_weight_kg": (int, float, type(None)),
    "max_volume_cm3": (int, float, type(None)),
    "max_packages": (int, type(None)),
    "max_trips_per_day": (int,),
    "depot_reload_time_minutes": (int,),
    "max_shift_hours": (int, float, type(None)),
    "break_duration_minutes": (int,),
    "break_earliest_offset_hours": (int, float),
    "break_latest_offset_hours": (int, float),
    "break_required": (bool,),
    "service_time_minutes": (int,),
    "allow_fragile": (bool,),
    "allow_temp_sensitive": (bool,),
}


def validate_constraint_values(constraints: dict[str, Any]) -> None:
    for key, value in constraints.items():
        if key not in CONSTRAINT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown constraint key: {key}",
            )
        allowed = CONSTRAINT_TYPES[key]
        if not isinstance(value, allowed):
            type_names = [t.__name__ for t in allowed]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Constraint '{key}' must be one of types: {type_names}, got {type(value).__name__}",
            )


async def _get_or_create_settings(db: AsyncSession, principal) -> SystemSettings:
    tenant_id = get_current_tenant_id(principal)
    result = await db.execute(select(SystemSettings).where(SystemSettings.tenant_id == tenant_id))
    settings_row = result.scalar_one_or_none()
    if not settings_row:
        settings_row = SystemSettings(tenant_id=tenant_id, global_constraints={})
        db.add(settings_row)
        await db.flush()
        await db.refresh(settings_row)
    return settings_row


async def get_global_constraints(db: AsyncSession, principal) -> dict[str, Any]:
    """Return fully resolved global constraints (hardcoded defaults + DB overrides)."""
    settings_row = await _get_or_create_settings(db, principal)
    merged = {**CONSTRAINT_DEFAULTS}
    for key, value in (settings_row.global_constraints or {}).items():
        if key in CONSTRAINT_DEFAULTS:
            merged[key] = value
    return merged


async def get_global_overrides(db: AsyncSession, principal) -> dict[str, Any]:
    """Return only the user-set global overrides (not merged with defaults)."""
    settings_row = await _get_or_create_settings(db, principal)
    return dict(settings_row.global_constraints or {})


async def set_global_constraints(db: AsyncSession, constraints: dict[str, Any], principal) -> dict[str, Any]:
    """Validate and persist global constraint overrides."""
    clean = {k: v for k, v in constraints.items() if k in CONSTRAINT_DEFAULTS}
    validate_constraint_values(clean)
    settings_row = await _get_or_create_settings(db, principal)
    settings_row.global_constraints = clean
    settings_row.updated_at = utcnow_naive()
    await db.flush()
    merged = {**CONSTRAINT_DEFAULTS, **clean}
    return merged


def resolve_courier_constraints(
    global_constraints: dict[str, Any],
    courier: Courier,
) -> dict[str, Any]:
    """Merge global constraints with per-courier overrides. Per-courier wins.

    The courier's model-level fields (max_weight_kg, max_volume_cm3,
    max_packages) are used as the effective defaults for their respective
    constraints, bridging model fields with the constraint system.
    """
    resolved = {**global_constraints}
    if courier.max_weight_kg:
        resolved["max_weight_kg"] = courier.max_weight_kg
    if courier.max_volume_cm3 is not None:
        resolved["max_volume_cm3"] = courier.max_volume_cm3
    if courier.max_packages is not None:
        resolved["max_packages"] = courier.max_packages
    courier_overrides = courier.constraints or {}
    for key, value in courier_overrides.items():
        if key in CONSTRAINT_DEFAULTS:
            resolved[key] = value
    return resolved


async def get_resolved_constraints_for_courier(
    db: AsyncSession,
    courier: Courier,
) -> dict[str, Any]:
    """Full resolution: hardcoded -> global -> per-courier."""
    global_c = await get_global_constraints(db, courier.tenant_id)
    return resolve_courier_constraints(global_c, courier)


async def set_courier_constraints(
    db: AsyncSession,
    courier_id,
    constraints: dict[str, Any],
    principal,
) -> dict[str, Any]:
    """Validate and merge per-courier constraint overrides with existing ones."""
    clean = {k: v for k, v in constraints.items() if k in CONSTRAINT_DEFAULTS}
    validate_constraint_values(clean)
    result = await db.execute(
        select(Courier).where(
            Courier.id == courier_id,
            tenant_filter(Courier, principal),
        )
    )
    courier = result.scalar_one_or_none()
    if not courier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Courier not found")
    existing = courier.constraints or {}
    merged = {**existing, **clean}
    courier.constraints = merged if merged else None
    await db.flush()
    return merged


async def get_courier_overrides(db: AsyncSession, courier_id, principal) -> dict[str, Any]:
    """Return only the per-courier overrides (not merged with global)."""
    result = await db.execute(
        select(Courier).where(
            Courier.id == courier_id,
            tenant_filter(Courier, principal),
        )
    )
    courier = result.scalar_one_or_none()
    if not courier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Courier not found")
    return dict(courier.constraints or {})


async def clear_courier_constraints(db: AsyncSession, courier_id, principal) -> None:
    """Remove all per-courier overrides."""
    result = await db.execute(
        select(Courier).where(
            Courier.id == courier_id,
            tenant_filter(Courier, principal),
        )
    )
    courier = result.scalar_one_or_none()
    if not courier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Courier not found")
    courier.constraints = None
    await db.flush()
