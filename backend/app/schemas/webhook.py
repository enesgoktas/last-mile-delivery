import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import WebhookDeliveryStatus


class WebhookEndpointCreate(BaseModel):
    client_id: uuid.UUID
    url: str
    events: list[str] = Field(default_factory=list)
    secret: str | None = Field(default=None, min_length=8, max_length=128)


class WebhookEndpointUpdate(BaseModel):
    url: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None


class WebhookEndpointResponse(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class WebhookEndpointCreatedResponse(WebhookEndpointResponse):
    # Secret is only returned on creation so clients can persist it safely.
    secret: str


class WebhookDeliveryLogResponse(BaseModel):
    id: uuid.UUID
    webhook_endpoint_id: uuid.UUID
    event_type: str
    payload: dict
    attempt_number: int
    delivery_status: WebhookDeliveryStatus
    response_status: int | None
    error_message: str | None
    sent_at: datetime
    next_retry_at: datetime | None

    model_config = {"from_attributes": True}


class WebhookReplayResponse(BaseModel):
    webhook_id: uuid.UUID
    requeued_count: int
