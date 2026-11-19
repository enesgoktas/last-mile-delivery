"""Append-only history of `RouteStop.status` transitions.

Captures the per-stop timeline that today lives only in a single mutable
status column. Includes the courier's lat/lon at transition time so
forensic replay can reconstruct "where was the truck when this stop was
marked arrived?" without having to reconcile against `LocationSnapshot`.

For a "why was order X late?" forensics query, this is the load-bearing
join: order → route_stops → stop_status_history.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import StopStatus
from app.utils.compat import utcnow_naive


class StopStatusHistory(Base):
    __tablename__ = "stop_status_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    stop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("route_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[StopStatus | None] = mapped_column(
        SAEnum(StopStatus), nullable=True
    )
    to_status: Mapped[StopStatus] = mapped_column(
        SAEnum(StopStatus), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Coordinates captured at transition time. NULL when the transition is
    # system-triggered (e.g. a cron auto-completing an old stop) — only set
    # when the courier's location was known and meaningful.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False, index=True
    )

    stop: Mapped["RouteStop"] = relationship("RouteStop", back_populates="status_history")
