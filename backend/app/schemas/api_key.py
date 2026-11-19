import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    permissions: list[str] = Field(default_factory=list)
    rate_limit_per_min: int = Field(default=60, ge=1, le=2000)


class ApiKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    permissions: list[str]
    rate_limit_per_min: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreateResponse(ApiKeyResponse):
    api_key: str
