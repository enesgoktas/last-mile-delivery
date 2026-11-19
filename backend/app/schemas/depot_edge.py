import uuid
from datetime import datetime
from datetime import time as dt_time

from pydantic import BaseModel, Field


class DepotEdgeBase(BaseModel):
    from_depot_id: uuid.UUID
    to_depot_id: uuid.UUID
    transit_minutes: int = Field(..., gt=0)
    cutoff_time: dt_time
    service_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    vehicle_class: str = Field(default="truck", min_length=1, max_length=32)


class DepotEdgeCreate(DepotEdgeBase):
    pass


class DepotEdgeUpdate(BaseModel):
    transit_minutes: int | None = Field(default=None, gt=0)
    cutoff_time: dt_time | None = None
    service_days: list[int] | None = None
    vehicle_class: str | None = Field(default=None, min_length=1, max_length=32)


class DepotEdgeResponse(DepotEdgeBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
