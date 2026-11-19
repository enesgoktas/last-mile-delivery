import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_write_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.api_key import ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyResponse
from app.services import api_key_service
from app.utils.auth import require_role

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN))]


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    return await api_key_service.list_api_keys(db, current_user)


@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    client, raw_key = await api_key_service.create_api_key(db, payload, current_user)
    return ApiKeyCreateResponse(
        id=client.id,
        name=client.name,
        permissions=client.permissions,
        rate_limit_per_min=client.rate_limit_per_min,
        is_active=client.is_active,
        created_at=client.created_at,
        api_key=raw_key,
    )


@router.delete("/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    api_key_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    await api_key_service.revoke_api_key(db, api_key_id, current_user)
