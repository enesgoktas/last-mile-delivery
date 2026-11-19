import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.models.enums import UserRole


class UserCreate(BaseModel):
    email: str
    password: str
    role: UserRole


class UserInvite(BaseModel):
    email: EmailStr
    role: UserRole


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
