"""Append-only history of `Courier.availability_status` transitions.

Today the courier state machine (ONLINE ↔ OFFLINE ↔ ON_BREAK) stores
only the current value. Without this table, "how many hours was this
courier online last week?" requires inferring from LocationSnapshot
density (lossy and slow). With it, the analytics rollup can compute
shift hours, online-time per courier, break compliance, and attrition
warning signals (sudden drop in online hours).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import AvailabilityStatus
from app.utils.compat import utcnow_naive


class CourierAvailabilityHistory(Base):
    __tablename__ = "courier_availability_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    courier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[AvailabilityStatus | None] = mapped_column(
        SAEnum(AvailabilityStatus), nullable=True
    )
    to_status: Mapped[AvailabilityStatus] = mapped_column(
        SAEnum(AvailabilityStatus), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False, index=True
    )

    courier: Mapped["Courier"] = relationship(
        "Courier", back_populates="availability_history"
    )
