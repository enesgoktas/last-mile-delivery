from __future__ import annotations

import random
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import FailureReason, OrderType, UserRole
from app.models.system_settings import SystemSettings
from app.schemas.operations_policy import (
    CustomOrderFieldDefinition,
    NotificationChannelPreferences,
    OperationsPolicy,
    OptimizationPresetKey,
    OptimizationSettings,
    PolicyPreset,
    ProofOfDeliveryRequirements,
    RoutingObjective,
    deep_merge_dicts,
    default_operations_policy_dict,
    optimization_settings_for_preset,
)
from app.utils.compat import UTC, utcnow_naive
from app.utils.tenant_context import get_current_tenant_id


def _parcel_preset() -> PolicyPreset:
    policy = OperationsPolicy.model_validate(default_operations_policy_dict())
    policy.optimization = optimization_settings_for_preset("BALANCED")
    policy.proof_of_delivery.defaults.require_photo = True
    policy.proof_of_delivery.defaults.require_signature = False
    policy.failure_handling.max_attempts = 2
    policy.failure_handling.self_service_mode = "RESCHEDULE_ONLY"
    policy.notifications.branding_name = "Parcel Delivery"
    return PolicyPreset(
        key="parcel",
        label="Parcel",
        description="Photo-based parcel delivery with limited retries and customer reschedule support.",
        policy=policy,
    )


def _food_preset() -> PolicyPreset:
    policy = OperationsPolicy.model_validate(default_operations_policy_dict())
    policy.optimization = optimization_settings_for_preset("SERVICE_FIRST")
    policy.dispatch.dispatch_mode = "AUTOMATIC"
    policy.delivery_workflow.pre_arrival_call = "OFF"
    policy.delivery_workflow.wait_time_minutes = 5
    policy.proof_of_delivery.defaults.require_photo = False
    policy.proof_of_delivery.defaults.require_signature = False
    policy.proof_of_delivery.defaults.notes_mode = "OPTIONAL"
    policy.failure_handling.max_attempts = 1
    policy.failure_handling.post_failure_action = "DISPATCH_REVIEW"
    policy.failure_handling.self_service_mode = "OFF"
    policy.notifications.eta_visibility = "LIVE_MAP"
    return PolicyPreset(
        key="food",
        label="Food",
        description="Fast handoff workflow with minimal proof requirements and no self-service retry flow.",
        policy=policy,
    )


def _pharmacy_preset() -> PolicyPreset:
    policy = OperationsPolicy.model_validate(default_operations_policy_dict())
    policy.optimization = optimization_settings_for_preset("SERVICE_FIRST")
    policy.delivery_workflow.safe_drop_policy = "DISALLOWED"
    policy.proof_of_delivery.defaults.require_photo = True
    policy.proof_of_delivery.defaults.require_signature = True
    policy.proof_of_delivery.defaults.require_otp = True
    policy.proof_of_delivery.defaults.receiver_name_mode = "REQUIRED"
    policy.failure_handling.max_attempts = 1
    policy.failure_handling.post_failure_action = "DISPATCH_REVIEW"
    policy.failure_handling.self_service_mode = "OFF"
    return PolicyPreset(
        key="pharmacy",
        label="Pharmacy",
        description="Strict handoff controls with signature, OTP, and receiver capture.",
        policy=policy,
    )


def _b2b_preset() -> PolicyPreset:
    policy = OperationsPolicy.model_validate(default_operations_policy_dict())
    policy.optimization = optimization_settings_for_preset("BALANCED")
    policy.dispatch.dispatch_mode = "MANUAL"
    policy.dispatch.route_approval.mode = "ALL_ROUTES"
    policy.delivery_workflow.pre_arrival_call = "REQUIRED"
    policy.delivery_workflow.wait_time_minutes = 15
    policy.proof_of_delivery.defaults.require_signature = True
    policy.proof_of_delivery.defaults.receiver_name_mode = "REQUIRED"
    policy.order_form.standard_fields.external_ref = "REQUIRED"
    policy.order_form.custom_fields = [
        CustomOrderFieldDefinition(
            key="dock_number",
            label="Dock Number",
            type="TEXT",
            required=False,
            applies_to_order_types=[OrderType.SCHEDULED, OrderType.BULK],
        ),
        CustomOrderFieldDefinition(
            key="invoice_number",
            label="Invoice Number",
            type="TEXT",
            required=True,
            applies_to_order_types=[OrderType.SCHEDULED, OrderType.BULK],
        ),
    ]
    return PolicyPreset(
        key="b2b",
        label="B2B Distribution",
        description="Manual review workflow with business-delivery metadata and stronger proof rules.",
        policy=policy,
    )


def _returns_preset() -> PolicyPreset:
    policy = OperationsPolicy.model_validate(default_operations_policy_dict())
    policy.optimization = optimization_settings_for_preset("COST_SAVER")
    policy.proof_of_delivery.by_order_type = {
        OrderType.REVERSE: ProofOfDeliveryRequirements(
            require_photo=True,
            photo_count=2,
            require_signature=True,
            receiver_name_mode="REQUIRED",
            notes_mode="REQUIRED",
        )
    }
    policy.failure_handling.post_failure_action = "RETURN_TO_DEPOT"
    policy.order_form.custom_fields = [
        CustomOrderFieldDefinition(
            key="return_condition",
            label="Return Condition",
            type="SELECT",
            required=True,
            options=["Unopened", "Opened", "Damaged"],
            applies_to_order_types=[OrderType.REVERSE],
        )
    ]
    return PolicyPreset(
        key="returns",
        label="Returns",
        description="Reverse-logistics preset with extra condition capture and depot return handling.",
        policy=policy,
    )


PRESETS: dict[str, PolicyPreset] = {
    preset.key: preset
    for preset in (
        _parcel_preset(),
        _food_preset(),
        _pharmacy_preset(),
        _b2b_preset(),
        _returns_preset(),
    )
}


PUBLIC_ROUTING_OBJECTIVE_SETTINGS: dict[RoutingObjective, dict[str, Any]] = {
    "FUEL_AWARE": {
        "preset_key": "COST_SAVER",
        "economic_batch_optimizer_enabled": True,
        "fuel_priority_multiplier": 1.0,
        "shadow_mode_enabled": False,
    },
    "SERVICE_FIRST": {
        "preset_key": "SERVICE_FIRST",
        "economic_batch_optimizer_enabled": True,
        "fuel_priority_multiplier": 0.0,
        "shadow_mode_enabled": False,
    },
}


async def _get_or_create_settings(db: AsyncSession, principal) -> SystemSettings:
    tenant_id = get_current_tenant_id(principal)
    result = await db.execute(select(SystemSettings).where(SystemSettings.tenant_id == tenant_id))
    settings_row = result.scalar_one_or_none()
    if settings_row is None:
        settings_row = SystemSettings(tenant_id=tenant_id, global_constraints={}, operations_policy={})
        db.add(settings_row)
        await db.flush()
        await db.refresh(settings_row)
    return settings_row


def _normalize_policy(raw_policy: dict[str, Any] | None) -> OperationsPolicy:
    merged = deep_merge_dicts(default_operations_policy_dict(), raw_policy or {})
    return OperationsPolicy.model_validate(merged)


def derive_routing_objective(settings: OptimizationSettings | None) -> RoutingObjective:
    if settings is None:
        return "FUEL_AWARE"
    if settings.routing_objective in PUBLIC_ROUTING_OBJECTIVE_SETTINGS:
        return settings.routing_objective
    if settings.preset_key == "SERVICE_FIRST" and settings.fuel_priority_multiplier <= 0:
        return "SERVICE_FIRST"
    return "FUEL_AWARE"


def optimization_settings_for_public_controls(
    routing_objective: RoutingObjective,
    *,
    fleet_utilization: str = "FULL_FLEET",
    fuel_prices: dict | None = None,
) -> OptimizationSettings:
    config = PUBLIC_ROUTING_OBJECTIVE_SETTINGS[routing_objective]
    settings = optimization_settings_for_preset(
        config["preset_key"],
        economic_batch_optimizer_enabled=config["economic_batch_optimizer_enabled"],
        fuel_priority_multiplier=config["fuel_priority_multiplier"],
        shadow_mode_enabled=config["shadow_mode_enabled"],
        internal_override_enabled=False,
    )
    return settings.model_copy(
        update={
            "routing_objective": routing_objective,
            "fleet_utilization": fleet_utilization,
            **({"fuel_prices": fuel_prices} if fuel_prices is not None else {}),
        }
    )


def _merge_public_optimization_settings(
    *,
    current: OptimizationSettings | None,
    incoming: OptimizationSettings,
) -> OptimizationSettings:
    current = current or optimization_settings_for_preset()
    routing_objective = derive_routing_objective(incoming)
    config = PUBLIC_ROUTING_OBJECTIVE_SETTINGS[routing_objective]

    if current.internal_override_enabled and derive_routing_objective(current) == routing_objective:
        return current.model_copy(
            update={
                "routing_objective": routing_objective,
                "fleet_utilization": incoming.fleet_utilization,
                "economic_batch_optimizer_enabled": config["economic_batch_optimizer_enabled"],
                "fuel_priority_multiplier": config["fuel_priority_multiplier"],
                "shadow_mode_enabled": config["shadow_mode_enabled"],
            }
        )

    return optimization_settings_for_public_controls(
        routing_objective,
        fleet_utilization=incoming.fleet_utilization,
        fuel_prices=incoming.fuel_prices,
    )


def _prepare_policy_for_persistence(
    payload: dict[str, Any],
    *,
    current_policy: OperationsPolicy | None,
    allow_internal_optimizer_override: bool = False,
) -> OperationsPolicy:
    validated = OperationsPolicy.model_validate(payload)
    if allow_internal_optimizer_override:
        return validated
    validated.optimization = _merge_public_optimization_settings(
        current=current_policy.optimization if current_policy else None,
        incoming=validated.optimization,
    )
    return validated


async def get_operations_policy(db: AsyncSession, principal) -> OperationsPolicy:
    settings_row = await _get_or_create_settings(db, principal)
    return _normalize_policy(settings_row.operations_policy)


async def set_operations_policy(db: AsyncSession, principal, payload: dict[str, Any]) -> OperationsPolicy:
    settings_row = await _get_or_create_settings(db, principal)
    current_policy = _normalize_policy(settings_row.operations_policy)
    validated = _prepare_policy_for_persistence(payload, current_policy=current_policy)
    settings_row.operations_policy = validated.model_dump(mode="json")
    await db.flush()
    await db.refresh(settings_row)
    return validated


async def patch_operations_policy(db: AsyncSession, principal, partial_payload: dict[str, Any]) -> OperationsPolicy:
    """Deep-merge *partial_payload* into the current policy and persist the result."""
    current = await get_operations_policy(db, principal)
    merged = deep_merge_dicts(current.model_dump(mode="json"), partial_payload)
    return await set_operations_policy(db, principal, merged)


async def apply_preset(db: AsyncSession, principal, preset_key: str) -> OperationsPolicy:
    preset = PRESETS.get(preset_key)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operations preset not found")
    return await set_operations_policy(db, principal, preset.policy.model_dump(mode="json"))


def list_presets() -> list[PolicyPreset]:
    return list(PRESETS.values())


def list_optimization_presets() -> list[dict[str, str]]:
    return [
        {
            "key": "COST_SAVER",
            "label": "Cost Saver",
            "description": "Bias toward lower operating cost while still protecting hard service commitments.",
        },
        {
            "key": "BALANCED",
            "label": "Balanced",
            "description": "Balance operating cost against lateness and overtime. Recommended default.",
        },
        {
            "key": "SERVICE_FIRST",
            "label": "Service First",
            "description": "Spend more to protect ETA commitments and reduce late deliveries.",
        },
    ]


def build_internal_optimization_settings(
    preset_key: OptimizationPresetKey,
    *,
    current: OptimizationSettings | None = None,
    overrides: dict[str, Any] | None = None,
) -> OptimizationSettings:
    base = optimization_settings_for_preset(
        preset_key,
        economic_batch_optimizer_enabled=(
            current.economic_batch_optimizer_enabled if current else False
        ),
        fuel_priority_multiplier=(current.fuel_priority_multiplier if current else 1.0),
        shadow_mode_enabled=(current.shadow_mode_enabled if current else True),
        internal_override_enabled=True,
    )
    merged = deep_merge_dicts(base.model_dump(mode="json"), overrides or {})
    merged["preset_key"] = preset_key
    merged["internal_override_enabled"] = True
    merged["routing_objective"] = (
        "SERVICE_FIRST"
        if preset_key == "SERVICE_FIRST" and merged.get("fuel_priority_multiplier", 1.0) <= 0
        else "FUEL_AWARE"
    )
    return OptimizationSettings.model_validate(merged)


async def set_internal_optimization_settings(
    db: AsyncSession,
    tenant_id,
    payload: dict[str, Any],
) -> OperationsPolicy:
    settings_row = await _get_or_create_settings(db, tenant_id)
    current_policy = _normalize_policy(settings_row.operations_policy)
    preset_key = payload.get("preset_key", current_policy.optimization.preset_key)
    current_policy.optimization = build_internal_optimization_settings(
        preset_key,
        current=current_policy.optimization,
        overrides=payload,
    )
    settings_row.operations_policy = current_policy.model_dump(mode="json")
    await db.flush()
    await db.refresh(settings_row)
    return current_policy


def get_preset(preset_key: str) -> PolicyPreset:
    preset = PRESETS.get(preset_key)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operations preset not found")
    return preset


def resolve_pod_requirements(policy: OperationsPolicy, order_type: OrderType) -> ProofOfDeliveryRequirements:
    override = policy.proof_of_delivery.by_order_type.get(order_type)
    if override is None:
        return policy.proof_of_delivery.defaults
    merged = deep_merge_dicts(
        policy.proof_of_delivery.defaults.model_dump(mode="json"),
        override.model_dump(mode="json"),
    )
    return ProofOfDeliveryRequirements.model_validate(merged)


def get_failure_reason_options(policy: OperationsPolicy) -> list[dict[str, str]]:
    options = [
        {"value": reason.value, "label": reason.value.replace("_", " ").title()}
        for reason in policy.failure_handling.allowed_standard_reasons
    ]
    for label in policy.failure_handling.custom_reasons:
        options.append(
            {
                "value": FailureReason.OTHER.value,
                "label": label,
                "custom_reason_label": label,
            }
        )
    return options


def generate_delivery_otp() -> str:
    return f"{random.randint(0, 999999):06d}"


def should_auto_dispatch_on_demand(policy: OperationsPolicy) -> bool:
    return policy.dispatch.dispatch_mode in {"AUTOMATIC", "HYBRID"}


def should_require_manual_route_approval(policy: OperationsPolicy) -> bool:
    return policy.dispatch.route_approval.mode in {"LARGE_ROUTES_ONLY", "ALL_ROUTES"}


def can_customer_reschedule(policy: OperationsPolicy) -> bool:
    return policy.failure_handling.self_service_mode != "OFF"


def can_customer_update_instructions(policy: OperationsPolicy) -> bool:
    return policy.failure_handling.self_service_mode == "RESCHEDULE_AND_INSTRUCTIONS"


def get_role_permissions(policy: OperationsPolicy, role: UserRole | str | None):
    if role == UserRole.OWNER or role == "OWNER":
        return policy.team_permissions.OWNER
    return policy.team_permissions.for_role(role)


async def ensure_permission(db: AsyncSession, principal, permission_name: str) -> None:
    policy = await get_operations_policy(db, principal)
    permissions = get_role_permissions(policy, getattr(principal, "role", None))
    if not getattr(permissions, permission_name, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role is not allowed to {permission_name.replace('_', ' ')} for this workspace",
        )


def is_notification_channel_enabled(
    policy: OperationsPolicy,
    *,
    event_type: str,
    channel: str,
) -> bool:
    prefs = policy.notifications.event_channels.get(event_type)
    if prefs is None:
        prefs = NotificationChannelPreferences()
    return bool(getattr(prefs, channel.lower(), False))


def _parse_clock(value: str) -> time:
    parts = value.split(":")
    if len(parts) != 2:
        return time(0, 0)
    try:
        return time(int(parts[0]), int(parts[1]))
    except ValueError:
        return time(0, 0)


def is_within_quiet_hours(policy: OperationsPolicy, *, now: datetime | None = None) -> bool:
    if not policy.notifications.quiet_hours_enabled:
        return False
    now = now or utcnow_naive()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    try:
        localized_now = now.astimezone(ZoneInfo(policy.notifications.timezone))
    except Exception:
        localized_now = now

    current = localized_now.time()
    start = _parse_clock(policy.notifications.quiet_hours_start)
    end = _parse_clock(policy.notifications.quiet_hours_end)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def get_custom_field_definitions_for_order_type(
    policy: OperationsPolicy,
    order_type: OrderType,
) -> list[CustomOrderFieldDefinition]:
    fields: list[CustomOrderFieldDefinition] = []
    for field in policy.order_form.custom_fields:
        if not field.applies_to_order_types or order_type in field.applies_to_order_types:
            fields.append(field)
    return fields


def validate_custom_field_values(
    field_definitions: list[CustomOrderFieldDefinition],
    values: dict[str, Any] | None,
) -> dict[str, Any]:
    values = dict(values or {})
    validated: dict[str, Any] = {}
    definition_by_key = {field.key: field for field in field_definitions}

    missing_required = [field.label for field in field_definitions if field.required and field.key not in values]
    if missing_required:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required custom fields: {', '.join(missing_required)}",
        )

    for key, value in values.items():
        field = definition_by_key.get(key)
        if field is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown custom field '{key}'",
            )
        if value is None:
            if field.required:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Custom field '{field.label}' is required",
                )
            continue

        if field.type == "TEXT":
            if not isinstance(value, str):
                raise HTTPException(status_code=422, detail=f"Custom field '{field.label}' must be text")
            validated[key] = value.strip()
        elif field.type == "NUMBER":
            if not isinstance(value, (int, float)):
                raise HTTPException(status_code=422, detail=f"Custom field '{field.label}' must be numeric")
            validated[key] = value
        elif field.type == "BOOLEAN":
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail=f"Custom field '{field.label}' must be true/false")
            validated[key] = value
        elif field.type == "DATE":
            if not isinstance(value, str):
                raise HTTPException(status_code=422, detail=f"Custom field '{field.label}' must be an ISO date string")
            validated[key] = value
        else:
            if not isinstance(value, str) or value not in field.options:
                raise HTTPException(
                    status_code=422,
                    detail=f"Custom field '{field.label}' must be one of: {', '.join(field.options)}",
                )
            validated[key] = value

    return validated
