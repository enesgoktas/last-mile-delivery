"""How much did the plan churn when we re-ran the optimizer?

Layer 2b. When the range planner re-runs over a date range that
overlaps a prior plan, we compare order assignments and compute
stability — the fraction of orders that stayed with the same
(courier, day).

Why this matters for the pilot:
  * Dispatcher experience: a thrashing solver creates "what changed?"
    confusion. A stable solver is dispatcher-friendly even if its
    distance is slightly worse.
  * Driver experience: drivers see consistent schedules. Last-minute
    reassignments hurt morale.
  * Tuning signal: low stability + small distance improvement means
    "the solver is chasing local minima"; tighten the stability
    penalty weight.

One row per (run, prior_run) pair. Today filled by the range planner;
extensible to the daily flow if/when it ever re-runs over an active
day.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class PlanStabilityScore(Base):
    __tablename__ = "plan_stability_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    optimization_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("optimization_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prior_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("optimization_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    orders_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_same_courier: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_same_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 0..1 — fraction of orders that kept their (courier, day) tuple.
    stability_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False, index=True
    )
