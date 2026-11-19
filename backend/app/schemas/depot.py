import uuid
from datetime import datetime
from datetime import time as dt_time

from pydantic import BaseModel


class DepotCreate(BaseModel):
    name: str
    address: str
    province_code: str | None = None
    province_name: str | None = None
    lat: float
    lon: float
    operating_hours_start: dt_time
    operating_hours_end: dt_time
    is_active: bool = True


class DepotUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    province_code: str | None = None
    province_name: str | None = None
    lat: float | None = None
    lon: float | None = None
    operating_hours_start: dt_time | None = None
    operating_hours_end: dt_time | None = None
    is_active: bool | None = None


class DepotResponse(BaseModel):
    id: uuid.UUID
    name: str
    address: str
    province_code: str | None = None
    province_name: str | None = None
    lat: float
    lon: float
    operating_hours_start: dt_time
    operating_hours_end: dt_time
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
