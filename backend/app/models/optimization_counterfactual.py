"""Counterfactual baseline persisted alongside every optimization run.

Pilot data strategy Layer 2 — the *load-bearing* table that lets us
answer "did our solver save money?" without dispatcher action. For every
real solve, a fast `human_dispatcher_baseline` runs over the same
`VRPInput`. We record what the baseline cost would have been; the
dashboard SUMs `baseline_*` minus `actual_*` over any date range to get
the saving — never recomputes.

One row per (optimization_run_id, baseline_kind). Today only one kind
ships (`GREEDY_NEAREST_NEIGHBOR`); the column is forward-compatible
with future baselines (e.g. last-week's plan, a human-edited reference).

Per-route denormalization onto `route_performance_snapshots.baseline_*`
columns happens at the same materialization time so dashboards never
need to join three tables.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import StrEnum, utcnow_naive


class CounterfactualBaselineKind(StrEnum):
    """Which baseline the counterfactual row represents.

    `GREEDY_NEAREST_NEIGHBOR` is the v1 default — fast, deterministic,
    captures "what a naive plan would have cost." Future kinds:
    - `HUMAN_DISPATCHER`: the operator's hand-built plan if one exists
    - `LAST_WEEK_SAME_DAY`: rolling reference for KPI continuity
    """

    GREEDY_NEAREST_NEIGHBOR = "GREEDY_NEAREST_NEIGHBOR"
    HUMAN_DISPATCHER = "HUMAN_DISPATCHER"
    LAST_WEEK_SAME_DAY = "LAST_WEEK_SAME_DAY"


class OptimizationCounterfactual(Base):
    __tablename__ = "optimization_counterfactuals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    optimization_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("optimization_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    baseline_kind: Mapped[CounterfactualBaselineKind] = mapped_column(
        SAEnum(CounterfactualBaselineKind), nullable=False
    )

    # ── Baseline solution shape
    baseline_route_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_distance_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    baseline_duration_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_assigned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_unassigned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_fuel_liters: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_fuel_cost_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_co2_kg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Real-solve aggregates for fast comparison (denormalized for query
    # speed; canonical numbers live on `route_performance_snapshots`).
    actual_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_fuel_cost_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_co2_kg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Deltas (signed: positive means real < baseline = we saved).
    distance_saved_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    fuel_cost_saved_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    co2_saved_kg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Telemetry
    baseline_elapsed_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_status: Mapped[str | None] = mapped_column(nullable=True)

    captured_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "optimization_run_id",
            "baseline_kind",
            name="uq_optimization_counterfactual_run_kind",
        ),
    )
