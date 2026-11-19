"""Append-only history of `Route.status` transitions.

Mirrors `OrderStatusHistory` for routes. Today the route lifecycle
(DRAFT → PLANNED → ACTIVE → COMPLETED / ABORTED) lives only in
`Route.status`; transitions vanish into log lines. This table makes the
"when did this route start, complete, abort" timeline reconstructible
months later.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import RouteStatus
from app.utils.compat import utcnow_naive


class RouteStatusHistory(Base):
    __tablename__ = "route_status_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("routes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[RouteStatus | None] = mapped_column(
        SAEnum(RouteStatus), nullable=True
    )
    to_status: Mapped[RouteStatus] = mapped_column(
        SAEnum(RouteStatus), nullable=False, index=True
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

    route: Mapped["Route"] = relationship("Route", back_populates="status_history")
