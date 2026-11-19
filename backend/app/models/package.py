import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import PackageStatus
from app.utils.compat import utcnow_naive


class Package(Base):
    __tablename__ = "packages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    length_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    width_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_cm3: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[PackageStatus] = mapped_column(
        SAEnum(PackageStatus), default=PackageStatus.AT_DEPOT, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    order: Mapped["Order"] = relationship("Order", back_populates="packages")
    inventory_events: Mapped[list["InventoryEvent"]] = relationship(
        "InventoryEvent", back_populates="package", order_by="InventoryEvent.created_at"
    )
