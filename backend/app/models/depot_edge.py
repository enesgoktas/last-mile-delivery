import uuid
from datetime import datetime
from datetime import time as dt_time

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class DepotEdge(Base):
    __tablename__ = "depot_edges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_depot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("depots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_depot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("depots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transit_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    cutoff_time: Mapped[dt_time] = mapped_column(nullable=False)
    service_days: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    vehicle_class: Mapped[str] = mapped_column(String(32), nullable=False, default="truck")
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "from_depot_id", "to_depot_id", name="uq_depot_edges_tenant_pair"),
    )
