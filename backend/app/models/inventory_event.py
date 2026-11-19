import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import InventoryEventType
from app.utils.compat import utcnow_naive


class InventoryEvent(Base):
    __tablename__ = "inventory_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("packages.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[InventoryEventType] = mapped_column(SAEnum(InventoryEventType), nullable=False)
    courier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("couriers.id", ondelete="SET NULL"), nullable=True
    )
    depot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("depots.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)

    package: Mapped["Package"] = relationship("Package", back_populates="inventory_events")
    courier: Mapped["Courier | None"] = relationship("Courier")
    depot: Mapped["Depot | None"] = relationship("Depot")
