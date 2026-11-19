import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import NotificationChannel, NotificationStatus
from app.utils.compat import utcnow_naive


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    channel: Mapped[NotificationChannel] = mapped_column(SAEnum(NotificationChannel), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[NotificationStatus] = mapped_column(
        SAEnum(NotificationStatus), default=NotificationStatus.PENDING, nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)


class ProofOfDelivery(Base):
    __tablename__ = "proof_of_delivery"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    route_stop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("route_stops.id", ondelete="CASCADE"),
        nullable=False,
    )
    photo_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    photo_urls: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    signature_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    receiver_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    otp_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    order: Mapped["Order"] = relationship("Order", back_populates="proof_of_delivery")
    route_stop: Mapped["RouteStop"] = relationship("RouteStop", back_populates="proof_of_delivery")
