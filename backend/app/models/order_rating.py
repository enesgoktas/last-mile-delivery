"""Customer satisfaction rating per delivered order.

Layer 3. The first CX signal we ever capture. Today only the endpoint
+ table land; the SMS / app flow that prompts customers to rate is a
separate ticket. Pre-pilot we'll surface ratings from any source via
the same POST /api/v1/orders/{id}/rating endpoint.

`rating` is a 1–5 integer; `feedback_text` is free text. We don't try
to model "topic" yet — the dashboard can run light NLP on free text
later if/when it's worth the cost. Unique on `order_id` so a duplicate
POST refreshes the existing rating instead of stacking.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class OrderRating(Base):
    __tablename__ = "order_ratings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False, index=True
    )
