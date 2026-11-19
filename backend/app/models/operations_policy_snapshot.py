"""Weekly typed snapshot of `SystemSettings.operations_policy`.

The live policy lives as an opaque JSONB blob on `system_settings`.
6 months from now, when an operator asks "what was our SLA policy on
April 1?", we need a typed history they can read. This table is the
write-only journal: one row per (tenant_id, snapshot_date) materialized
weekly by a cron task that parses the JSONB into denormalized columns.

The full JSONB is still kept (raw_policy) so future code can re-derive
any field we didn't break out — schema additions don't require a
backfill.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class OperationsPolicySnapshot(Base):
    __tablename__ = "operations_policy_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # ── Headline feature flags (denormalized for trivial SQL filtering)
    multi_depot_vehicles_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rides_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ev_auto_charging_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    economic_batch_optimizer_enabled: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    manual_route_approval_required: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )

    # ── SLA / time windows (the bits trend-tracking really wants)
    on_time_target_pct: Mapped[float | None] = mapped_column(nullable=True)
    sla_window_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Raw policy + constraints for everything we didn't break out
    raw_policy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_global_constraints: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    captured_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "snapshot_date",
            name="uq_operations_policy_snapshots_tenant_date",
        ),
    )
