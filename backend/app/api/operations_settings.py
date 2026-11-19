from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_write_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.operations_policy import FeatureFlags, OperationsPolicy, PolicyPreset
from app.services import operations_policy_service
from app.services.audit_service import log_audit_event
from app.utils.auth import require_role
from app.utils.tenant_context import get_current_tenant_id

router = APIRouter(prefix="/settings/operations-policy", tags=["operations-settings"])
features_router = APIRouter(prefix="/settings/features", tags=["operations-settings"])

AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.OWNER))]
DispatcherDep = Annotated[
    User, Depends(require_role(UserRole.OWNER, UserRole.ADMIN, UserRole.DISPATCHER))
]


@features_router.get("", response_model=FeatureFlags)
async def get_my_feature_flags(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
) -> FeatureFlags:
    """Read-only view of the caller's tenant feature flags.

    Lower auth bar than the full operations-policy endpoint — any
    backoffice user (OWNER / ADMIN / DISPATCHER) can read the flags so
    the frontend can gate nav items, page access, etc. Writes still
    flow through the SUPERADMIN /admin pages or through the AdminDep
    operations-policy endpoint.
    """
    policy = await operations_policy_service.get_operations_policy(db, current_user)
    return policy.feature_flags


@router.get("", response_model=OperationsPolicy)
async def get_operations_policy(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
) -> OperationsPolicy:
    await operations_policy_service.ensure_permission(db, current_user, "manage_settings")
    return await operations_policy_service.get_operations_policy(db, current_user)


@router.put("", response_model=OperationsPolicy)
async def save_operations_policy(
    payload: OperationsPolicy,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
) -> OperationsPolicy:
    await operations_policy_service.ensure_permission(db, current_user, "manage_settings")
    result = await operations_policy_service.set_operations_policy(
        db,
        current_user,
        payload.model_dump(mode="json"),
    )
    await log_audit_event(
        db,
        action="policy.update",
        actor_user_id=current_user.id,
        tenant_id=get_current_tenant_id(current_user),
        resource_type="operations_policy",
    )
    return result


@router.patch("", response_model=OperationsPolicy)
async def patch_operations_policy(
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
) -> OperationsPolicy:
    """Partially update the operations policy by deep-merging the supplied fields into the current policy."""
    await operations_policy_service.ensure_permission(db, current_user, "manage_settings")
    result = await operations_policy_service.patch_operations_policy(db, current_user, payload)
    await log_audit_event(
        db,
        action="policy.patch",
        actor_user_id=current_user.id,
        tenant_id=get_current_tenant_id(current_user),
        resource_type="operations_policy",
        details={"patched_keys": list(payload.keys())},
    )
    return result


@router.get("/presets", response_model=list[PolicyPreset])
async def list_operations_policy_presets(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
) -> list[PolicyPreset]:
    await operations_policy_service.ensure_permission(db, current_user, "manage_settings")
    return operations_policy_service.list_presets()


@router.post("/presets/{preset_key}", response_model=OperationsPolicy)
async def apply_operations_policy_preset(
    preset_key: str,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
) -> OperationsPolicy:
    await operations_policy_service.ensure_permission(db, current_user, "manage_settings")
    result = await operations_policy_service.apply_preset(db, current_user, preset_key)
    await log_audit_event(
        db,
        action="policy.apply_preset",
        actor_user_id=current_user.id,
        tenant_id=get_current_tenant_id(current_user),
        resource_type="operations_policy",
        details={"preset_key": preset_key},
    )
    return result
