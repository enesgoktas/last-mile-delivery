import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.enums import WebhookDeliveryStatus
from app.utils.compat import UTC


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ApiClient(Base):
    __tablename__ = "api_clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    permissions: Mapped[list] = mapped_column(ARRAY(String), nullable=False, default=list)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    events: Mapped[list] = mapped_column(ARRAY(String), nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)


class WebhookDeliveryLog(Base):
    __tablename__ = "webhook_delivery_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    webhook_endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_status: Mapped[WebhookDeliveryStatus] = mapped_column(
        SAEnum(WebhookDeliveryStatus), nullable=False
    )
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(nullable=True)
