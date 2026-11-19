import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import OrderLegKind, OrderLegStatus
from app.utils.compat import utcnow_naive


class OrderLeg(Base):
    __tablename__ = "order_legs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_kind: Mapped[OrderLegKind] = mapped_column(SAEnum(OrderLegKind), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    from_depot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("depots.id", ondelete="SET NULL"), nullable=True
    )
    to_depot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("depots.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[OrderLegStatus] = mapped_column(
        SAEnum(OrderLegStatus),
        default=OrderLegStatus.PENDING,
        nullable=False,
    )
    planned_pickup_at: Mapped[datetime | None] = mapped_column(nullable=True)
    planned_delivery_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    order: Mapped["Order"] = relationship("Order", back_populates="legs")
