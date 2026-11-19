"""Typed driver-shift event log — feeds HoS compliance + fairness analytics.

Layer 3. Complements `courier_availability_history` (which is just
status transitions) with semantically richer events: SHIFT_START with
a planned-end timestamp, BREAK_START + BREAK_END that we can join to
check compliance, REROUTE_RECEIVED so we know how often drivers got
their plans changed mid-shift, ORDER_REJECTED for the auction/refusal
case (future), IDLE_DETECTED from the geofencing service (future).

The metadata JSONB carries event-specific extras (e.g. break duration,
which route was rerouted) without schema churn for each new event type.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import StrEnum, utcnow_naive


class DriverEventType(StrEnum):
    """Categories of driver-side operational events.

    Today only SHIFT_START / SHIFT_END / BREAK_START / BREAK_END land
    automatically from the courier availability hook; the rest are
    schema-only until the relevant emit sites are wired."""

    SHIFT_START = "SHIFT_START"
    SHIFT_END = "SHIFT_END"
    BREAK_START = "BREAK_START"
    BREAK_END = "BREAK_END"
    IDLE_DETECTED = "IDLE_DETECTED"
    REROUTE_RECEIVED = "REROUTE_RECEIVED"
    ORDER_REJECTED = "ORDER_REJECTED"


class DriverEvent(Base):
    __tablename__ = "driver_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    courier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[DriverEventType] = mapped_column(
        SAEnum(DriverEventType), nullable=False, index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False, index=True
    )
