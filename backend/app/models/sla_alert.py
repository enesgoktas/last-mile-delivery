import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import UTC


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SLAAlert(Base):
    __tablename__ = "sla_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("routes.id"), nullable=False, index=True)
    stop_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("route_stops.id"), nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)
    courier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("couriers.id"), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_delay_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
