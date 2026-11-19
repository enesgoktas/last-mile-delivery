import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import InventoryEventType, PackageStatus


class PackageResponse(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    label: str
    weight_kg: float
    length_cm: float | None
    width_cm: float | None
    height_cm: float | None
    volume_cm3: float | None
    status: PackageStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PackageUpdate(BaseModel):
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    volume_cm3: float | None = None


class InventoryEventResponse(BaseModel):
    id: uuid.UUID
    package_id: uuid.UUID
    event_type: InventoryEventType
    courier_id: uuid.UUID | None
    depot_id: uuid.UUID | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CurrentLoadItem(BaseModel):
    package_id: uuid.UUID
    order_id: uuid.UUID
    label: str
    weight_kg: float
    volume_cm3: float | None
    status: PackageStatus


class CourierCurrentLoadResponse(BaseModel):
    courier_id: uuid.UUID
    total_weight_kg: float
    total_volume_cm3: float
    package_count: int
    packages: list[CurrentLoadItem]
