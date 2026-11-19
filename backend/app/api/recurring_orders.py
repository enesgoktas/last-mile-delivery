"""Recurring order template CRUD + materialise endpoints."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_write_db
from app.models.enums import UserRole
from app.models.recurring_order import RecurringOrderOccurrence, RecurringOrderTemplate
from app.models.user import User
from app.models.vehicle_tag import VehicleTag
from app.schemas.recurring_order import (
    RecurringOrderOccurrenceResponse,
    RecurringOrderPreviewResponse,
    RecurringOrderRunResponse,
    RecurringOrderTemplateCreate,
    RecurringOrderTemplateResponse,
    RecurringOrderTemplateUpdate,
)
from app.services import recurring_order_service
from app.utils.auth import require_role
from app.utils.tenant_context import tenant_filter

router = APIRouter(prefix="/recurring-orders", tags=["Recurring Orders"])

DispatcherDep = Annotated[
    User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))
]


async def _get_template(
    db: AsyncSession, tenant_id: uuid.UUID, template_id: uuid.UUID
) -> RecurringOrderTemplate:
    template = (
        await db.execute(
            select(RecurringOrderTemplate).where(
                RecurringOrderTemplate.id == template_id,
                tenant_filter(RecurringOrderTemplate, tenant_id),
            )
        )
    ).scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Recurring template not found")
    return template


@router.get(
    "",
    response_model=list[RecurringOrderTemplateResponse],
    summary="List recurring order templates",
)
async def list_templates(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
    is_active: bool | None = Query(None, description="Filter by active flag"),
):
    filters = [tenant_filter(RecurringOrderTemplate, current_user.tenant_id)]
    if is_active is not None:
        filters.append(RecurringOrderTemplate.is_active.is_(is_active))
    rows = (
        await db.execute(
            select(RecurringOrderTemplate)
            .where(*filters)
            .order_by(RecurringOrderTemplate.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


@router.post(
    "",
    response_model=RecurringOrderTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a recurring order template",
)
async def create_template(
    payload: RecurringOrderTemplateCreate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    fields = payload.model_dump()
    tag_ids = fields.pop("required_vehicle_tag_ids", None) or []
    template = RecurringOrderTemplate(
        tenant_id=current_user.tenant_id,
        **fields,
    )
    db.add(template)
    await db.flush()
    if tag_ids:
        result = await db.execute(
            select(VehicleTag).where(
                VehicleTag.id.in_(tag_ids),
                tenant_filter(VehicleTag, current_user),
            )
        )
        template.required_vehicle_tags = list(result.scalars().all())
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A recurring template with this name already exists",
        ) from exc
    await db.refresh(template)
    return template


@router.get(
    "/{template_id}",
    response_model=RecurringOrderTemplateResponse,
    summary="Get a recurring order template",
)
async def get_template(
    template_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    return await _get_template(db, current_user.tenant_id, template_id)


@router.put(
    "/{template_id}",
    response_model=RecurringOrderTemplateResponse,
    summary="Update a recurring order template",
)
async def update_template(
    template_id: uuid.UUID,
    payload: RecurringOrderTemplateUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    template = await _get_template(db, current_user.tenant_id, template_id)
    update_fields = payload.model_dump(exclude_unset=True)
    tag_ids = update_fields.pop("required_vehicle_tag_ids", None)
    for field, value in update_fields.items():
        setattr(template, field, value)
    if tag_ids is not None:
        if tag_ids:
            result = await db.execute(
                select(VehicleTag).where(
                    VehicleTag.id.in_(tag_ids),
                    tenant_filter(VehicleTag, current_user),
                )
            )
            template.required_vehicle_tags = list(result.scalars().all())
        else:
            template.required_vehicle_tags = []

    if (
        template.delivery_window_end is not None
        and template.delivery_window_start is not None
        and template.delivery_window_end <= template.delivery_window_start
    ):
        raise HTTPException(
            status_code=422,
            detail="delivery_window_end must be after delivery_window_start",
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A recurring template with this name already exists",
        ) from exc
    await db.refresh(template)
    return template


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a recurring order template",
)
async def delete_template(
    template_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    template = await _get_template(db, current_user.tenant_id, template_id)
    await db.delete(template)
    await db.commit()
    return None


@router.get(
    "/{template_id}/preview",
    response_model=RecurringOrderPreviewResponse,
    summary="Preview the next occurrence dates this template will produce",
)
async def preview_template(
    template_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
    horizon_days: int = Query(60, ge=1, le=365),
):
    template = await _get_template(db, current_user.tenant_id, template_id)
    range_start = max(template.start_date, date.today())
    range_end = date.today() + timedelta(days=horizon_days)
    dates = recurring_order_service.occurrences_in_range(
        template, range_start=range_start, range_end=range_end
    )
    return RecurringOrderPreviewResponse(
        template_id=template.id, occurrence_dates=dates
    )


@router.get(
    "/{template_id}/occurrences",
    response_model=list[RecurringOrderOccurrenceResponse],
    summary="List materialised occurrences for a template",
)
async def list_occurrences(
    template_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
    limit: int = Query(50, ge=1, le=500),
):
    template = await _get_template(db, current_user.tenant_id, template_id)
    rows = (
        await db.execute(
            select(RecurringOrderOccurrence)
            .where(RecurringOrderOccurrence.template_id == template.id)
            .order_by(RecurringOrderOccurrence.occurrence_date.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


@router.post(
    "/{template_id}/run",
    response_model=RecurringOrderRunResponse,
    summary="Materialise due occurrences for a single template right now",
)
async def run_template(
    template_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    template = await _get_template(db, current_user.tenant_id, template_id)
    if not template.is_active:
        raise HTTPException(
            status_code=409,
            detail="Cannot run a paused template — re-activate it first",
        )
    stats = await recurring_order_service.materialise_due_occurrences(
        db, tenant_id=current_user.tenant_id, template_ids=[template.id]
    )
    await db.commit()
    return RecurringOrderRunResponse(
        templates_evaluated=stats.templates_evaluated,
        occurrences_created=stats.occurrences_created,
        occurrences_skipped_duplicate=stats.occurrences_skipped_duplicate,
        occurrences_skipped_capped=stats.occurrences_skipped_capped,
    )


@router.post(
    "/run",
    response_model=RecurringOrderRunResponse,
    summary="Materialise due occurrences across every active template",
)
async def run_all_templates(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: DispatcherDep,
):
    stats = await recurring_order_service.materialise_due_occurrences(
        db, tenant_id=current_user.tenant_id
    )
    await db.commit()
    return RecurringOrderRunResponse(
        templates_evaluated=stats.templates_evaluated,
        occurrences_created=stats.occurrences_created,
        occurrences_skipped_duplicate=stats.occurrences_skipped_duplicate,
        occurrences_skipped_capped=stats.occurrences_skipped_capped,
    )
