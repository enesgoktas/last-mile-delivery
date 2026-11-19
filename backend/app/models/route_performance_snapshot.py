"""One row per completed Route — planned vs actual + materialized fuel cost.

Layer 1 of the pilot data strategy. The analytics endpoints used to
recompute these deltas from raw orders/stops on every dashboard poll.
By materializing the per-route summary at route-completion time, the
dashboard reads a single indexed table and the "did our solver save
fuel?" answer becomes a simple SUM over a date range.

The fuel/cost columns are filled from existing inputs we already have
but never joined together:

  * actual_distance_m + `Courier.vehicle_spec.fuel_efficiency_km_per_liter`
    → `actual_fuel_liters`
  * `EnergyPriceSnapshot.price_try` for the depot's province at run-time
    → `fuel_unit_price_try`
  * the product → `actual_fuel_cost_try` (the pilot's headliner KPI)
  * actual_fuel_liters × 2.31 kg/L → `co2_kg` (sustainability secondary)

Per-stop arrival delta percentiles surface "how late did we get?" in a
single column instead of requiring a windowed SQL over `route_stops`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, Float, ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.utils.compat import utcnow_naive


class RoutePerformanceSnapshot(Base):
    __tablename__ = "route_performance_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("routes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    courier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    optimization_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("optimization_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    operational_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # ── Planned (from the solver's output)
    planned_distance_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    planned_duration_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    planned_stop_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    planned_capacity_used_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Actual (from execution)
    actual_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_stop_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Per-stop arrival delta percentiles (seconds)
    delta_arrival_p50_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delta_arrival_p95_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delta_arrival_max_s: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Fuel + cost (THE headliner)
    actual_fuel_liters: Mapped[float | None] = mapped_column(Float, nullable=True)
    fuel_unit_price_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_fuel_cost_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_distance_cost_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_route_cost_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    co2_kg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Counterfactual link (Layer 2 will fill this; Layer 1 leaves NULL)
    baseline_fuel_cost_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False, index=True
    )

    route: Mapped["Route"] = relationship("Route")

    __table_args__ = (
        Index(
            "ix_route_performance_snapshots_tenant_date",
            "tenant_id",
            "operational_date",
        ),
    )
