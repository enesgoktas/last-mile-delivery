"""Recurring order templates.

A `RecurringOrderTemplate` is a saved, parameterised order that the system
materialises into real `Order` rows on a cadence (daily / weekly / biweekly /
monthly / custom interval in days). Templates do not produce a courier
assignment themselves — generated orders enter the normal PENDING queue and
are picked up by the next dispatch / "Assign Optimally" run.

Design choices (kept boring and audit-friendly):
  * Only the order *template* is stored; generated occurrences live in `orders`.
  * `(template_id, occurrence_date)` is unique → re-running the generator is
    idempotent and safe to schedule on a celery beat.
  * Every cadence is encoded as `(cadence, interval, day_of_week_mask, day_of_month)`
    so we can pre-compute the next occurrence without parsing RRULE strings.
  * All windows on the template are stored as a *time-of-day + duration*; on
    materialisation we combine them with the occurrence date in the tenant's
    timezone (UTC for now — TODO when multi-timezone is needed).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import OrderPriority, OrderType
from app.utils.compat import StrEnum, utcnow_naive


class RecurrenceCadence(StrEnum):
    """How frequently the template fires.

    `interval_days` is consulted for `CUSTOM` only; the other values are
    self-describing.  WEEKLY honours `day_of_week_mask` (bitmask Mon=1<<0 ..
    Sun=1<<6); MONTHLY honours `day_of_month` (1-31, capped at month length).
    BIWEEKLY is "every 14 days from `start_date`".
    """
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    MONTHLY = "MONTHLY"
    CUSTOM = "CUSTOM"


class RecurringOrderTemplate(Base):
    """A blueprint for recurring orders.

    The materialiser walks active templates, computes due occurrence dates
    inside the lookahead window (`lead_time_days`), and creates one `Order`
    per occurrence — provided no `Order` already exists for that
    `(template_id, occurrence_date)` pair.
    """

    __tablename__ = "recurring_order_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_recurring_order_templates_tenant_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ------------------------------------------------------------------
    # Cadence
    # ------------------------------------------------------------------
    cadence: Mapped[RecurrenceCadence] = mapped_column(
        SAEnum(RecurrenceCadence, name="recurrencecadence"),
        nullable=False,
    )
    # Only used when cadence == CUSTOM. Inclusive minimum of 1 day.
    interval_days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # WEEKLY: bitmask of weekdays Mon=1<<0 .. Sun=1<<6. 0 = use start_date weekday.
    day_of_week_mask: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # MONTHLY: 1..31 (capped to month length when shorter).  NULL = day-of-month
    # of `start_date`.
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # First eligible occurrence date (inclusive). Defaults to today UTC.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Optional last eligible occurrence (inclusive). NULL = open-ended.
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Optional cap on total generated orders; NULL = unlimited.
    max_occurrences: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurrences_generated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Time-of-day windows (UTC). Service combines these with the occurrence date.
    delivery_window_start: Mapped[time] = mapped_column(Time, nullable=False)
    delivery_window_end: Mapped[time] = mapped_column(Time, nullable=False)
    pickup_window_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    pickup_window_end: Mapped[time | None] = mapped_column(Time, nullable=True)

    # How far ahead of "now" the materialiser will generate orders (1..30 days).
    # Higher = customers see future deliveries earlier; safe to keep at 7.
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)

    # ------------------------------------------------------------------
    # Order template payload — mirrors OrderCreate fields.  When materialising
    # we copy these onto the new Order, plus inject the resolved windows and a
    # generated tracking_token.
    # ------------------------------------------------------------------
    order_type: Mapped[OrderType] = mapped_column(SAEnum(OrderType), nullable=False)
    priority: Mapped[OrderPriority] = mapped_column(
        SAEnum(OrderPriority), default=OrderPriority.NORMAL, nullable=False
    )

    pickup_address: Mapped[str] = mapped_column(String(500), nullable=False)
    pickup_lat: Mapped[float] = mapped_column(Float, nullable=False)
    pickup_lon: Mapped[float] = mapped_column(Float, nullable=False)
    delivery_address: Mapped[str] = mapped_column(String(500), nullable=False)
    delivery_lat: Mapped[float] = mapped_column(Float, nullable=False)
    delivery_lon: Mapped[float] = mapped_column(Float, nullable=False)
    depot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("depots.id", ondelete="SET NULL"), nullable=True
    )

    # NULL/0 for RIDE templates; required > 0 for non-RIDE.
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_fragile: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_temp_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    length_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    width_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_cm3: Mapped[float | None] = mapped_column(Float, nullable=True)

    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_ref_prefix: Mapped[str | None] = mapped_column(String(120), nullable=True)
    custom_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Ride-specific fields (only meaningful when order_type == RIDE) ──
    # Mirror Order columns so the materialiser can copy them straight onto
    # the generated Order without extra transformation.
    passenger_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passenger_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    passenger_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    flight_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    special_requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    pickup_buffer_minutes_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allow_split: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    passengers_per_vehicle: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ------------------------------------------------------------------
    # Bookkeeping (updated by the materialiser; never the API).
    # ------------------------------------------------------------------
    last_generated_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_generation_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    # Reverse relationship: a generated Order keeps a pointer to its template
    # via custom_fields["recurring_template_id"]; we don't add a hard FK on
    # Order to keep that table churn-free, but we do want quick lookups.
    # Quoted forward-ref because RecurringOrderOccurrence is defined below.
    occurrences: Mapped[list["RecurringOrderOccurrence"]] = relationship(  # noqa: UP037
        "RecurringOrderOccurrence",
        back_populates="template",
        cascade="all, delete-orphan",
    )
    required_vehicle_tags: Mapped[list["VehicleTag"]] = relationship(
        "VehicleTag",
        secondary="recurring_order_required_vehicle_tags",
        lazy="selectin",
    )


class RecurringOrderOccurrence(Base):
    """Audit row recording a single template→order materialisation.

    The unique `(template_id, occurrence_date)` constraint is the idempotency
    fence: re-running the generator with overlapping windows can never produce
    duplicate orders for the same date.
    """

    __tablename__ = "recurring_order_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "occurrence_date",
            name="uq_recurring_order_occurrences_template_date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recurring_order_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    occurrence_date: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)

    template: Mapped[RecurringOrderTemplate] = relationship(
        "RecurringOrderTemplate", back_populates="occurrences"
    )
