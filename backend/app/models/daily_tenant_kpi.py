"""Daily per-tenant rollup of headline operational + cost KPIs.

Replaces the on-the-fly aggregation in `analytics_service.get_summary`
for the multi-day trend queries that today re-scan every order row on
each 60-second dashboard poll. One row per (tenant_id, operational_date)
plus a nightly cron that materializes from the raw tables.

Cost columns (`total_fuel_cost_try`, `total_co2_kg`, etc.) are summed
from the per-route `RoutePerformanceSnapshot` table — those snapshots
themselves get filled at route completion, so by midnight on day N the
rollup is final.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class DailyTenantKPI(Base):
    __tablename__ = "daily_tenant_kpi"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operational_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # ── Order funnel counts
    orders_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_pending: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_assigned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_in_transit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_cancelled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_unassigned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Rates (0..1)
    on_time_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_attempt_success_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_route_utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Route / fleet counts
    total_routes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_distance_m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_duration_s: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Cost rollups (the headliner KPIs)
    total_fuel_cost_try: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_fuel_liters: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_co2_kg: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cost_per_delivery_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_per_km_try: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Counterfactual savings (filled by Layer 2 — counterfactual baseline)
    baseline_fuel_cost_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_co2_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    fuel_cost_saved_try: Mapped[float | None] = mapped_column(Float, nullable=True)
    co2_saved_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_saved_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Solver activity
    optimization_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_solver_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "operational_date", name="uq_daily_tenant_kpi_tenant_date"
        ),
    )
