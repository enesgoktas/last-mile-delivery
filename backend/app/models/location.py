import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class LocationSnapshot(Base):
    __tablename__ = "location_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    courier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="CASCADE"),
        nullable=False,
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    heading: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_kmh: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy_m: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow_naive)

    __table_args__ = (
        Index("ix_location_snapshots_courier_recorded", "courier_id", "recorded_at"),
    )
