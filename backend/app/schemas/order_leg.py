import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import OrderLegKind, OrderLegStatus


class OrderLegResponse(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    leg_kind: OrderLegKind
    sequence_number: int
    from_depot_id: uuid.UUID | None
    to_depot_id: uuid.UUID | None
    status: OrderLegStatus
    planned_pickup_at: datetime | None
    planned_delivery_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
