"""Hourly demand-by-zone feed for a future forecasting model.

Layer 3. Captures the input a demand-prediction model would need:
order counts by (tenant, geohash, hour, day-of-week). The model
itself is out of scope — we just want the *feed* to exist so by the
time someone builds a forecaster there's a year of historical demand
to train on.

Rolled up nightly by a cron task from raw `orders` (deferred — schema
+ types land now; the cron is its own ticket). For pilot demo, the
seed extension can populate plausible demand rows directly.

`weighted_demand` = sum of order priorities (NORMAL=1, HIGH=2,
URGENT=3, LOW=0.5). Lets the forecaster bake priority into volume
without losing the raw count column.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class DemandObservation(Base):
    __tablename__ = "demand_observations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observation_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # 6-char geohash ≈ 1.2 km × 0.6 km — neighborhood granularity.
    geohash_6: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    hour_bucket: Mapped[int] = mapped_column(Integer, nullable=False)  # 0..23
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon..6=Sun
    order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weighted_demand: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "observation_date",
            "geohash_6",
            "hour_bucket",
            name="uq_demand_observations_zone_hour",
        ),
    )
