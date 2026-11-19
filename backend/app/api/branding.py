"""Tenant branding API — admin-only PATCH and public GET."""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_write_db
from app.models.enums import UserRole
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.auth import require_role
from app.utils.tenant_context import get_current_tenant_id

router = APIRouter(prefix="/branding", tags=["branding"])

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN))]


class BrandingUpdate(BaseModel):
    logo_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None

    @field_validator("logo_url")
    @classmethod
    def _validate_logo_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("https://", "http://")):
            raise ValueError("logo_url must be an absolute HTTPS (or HTTP) URL")
        return v

    @field_validator("primary_color", "secondary_color")
    @classmethod
    def _validate_hex_color(cls, v: str | None) -> str | None:
        if v is not None and not _HEX_RE.match(v):
            raise ValueError("Color must be a 6-digit hex string, e.g. '#1a73e8'")
        return v


class BrandingResponse(BaseModel):
    logo_url: str | None
    primary_color: str | None
    secondary_color: str | None

    model_config = {"from_attributes": True}


async def _get_tenant(db: AsyncSession, current_user: User) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.id == get_current_tenant_id(current_user))
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


@router.get("", response_model=BrandingResponse)
async def get_branding(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: AdminDep,
) -> BrandingResponse:
    """Return the current branding settings for the caller's tenant."""
    tenant = await _get_tenant(db, current_user)
    return BrandingResponse.model_validate(tenant)


@router.patch("", response_model=BrandingResponse)
async def update_branding(
    payload: BrandingUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
) -> BrandingResponse:
    """Partially update tenant branding. Omitted fields are left unchanged."""
    tenant = await _get_tenant(db, current_user)

    if "logo_url" in payload.model_fields_set:
        tenant.logo_url = payload.logo_url
    if "primary_color" in payload.model_fields_set:
        tenant.primary_color = payload.primary_color
    if "secondary_color" in payload.model_fields_set:
        tenant.secondary_color = payload.secondary_color

    await db.flush()
    await db.refresh(tenant)
    return BrandingResponse.model_validate(tenant)
