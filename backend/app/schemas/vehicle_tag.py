import uuid

from pydantic import BaseModel, Field


class VehicleTagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")


class VehicleTagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class VehicleTagResponse(BaseModel):
    id: uuid.UUID
    name: str
    color: str

    model_config = {"from_attributes": True}
