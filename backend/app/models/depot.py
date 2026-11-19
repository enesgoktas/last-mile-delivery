import uuid
from datetime import datetime
from datetime import time as dt_time

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.utils.compat import utcnow_naive


class Depot(Base):
    __tablename__ = "depots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    province_code: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    province_name: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    operating_hours_start: Mapped[dt_time] = mapped_column(nullable=False)
    operating_hours_end: Mapped[dt_time] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    # `Courier` now has two FKs to `depots` — `depot_id` (home) and
    # `end_depot_id` (optional shift-end). Pin this back-ref to the home
    # depot only; reverse-lookup of end_depot is intentionally absent
    # because we don't need it.
    couriers: Mapped[list["Courier"]] = relationship(
        "Courier", back_populates="depot", foreign_keys="Courier.depot_id"
    )
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="depot")
