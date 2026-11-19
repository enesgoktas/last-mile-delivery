import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.utils.compat import utcnow_naive


class RouteScenarioComparison(Base):
    __tablename__ = "route_scenario_comparisons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="STARTED", index=True)
    task_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    selected_scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    current_policy_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(nullable=True)

    scenarios: Mapped[list["RouteScenario"]] = relationship(
        "RouteScenario",
        back_populates="comparison",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class RouteScenario(Base):
    __tablename__ = "route_scenarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comparison_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("route_scenario_comparisons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    routing_objective: Mapped[str] = mapped_column(String(30), nullable=False)
    fleet_utilization: Mapped[str] = mapped_column(String(30), nullable=False)
    solver_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="ortools_cvrptw")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    optimization_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    comparison: Mapped[RouteScenarioComparison] = relationship(
        "RouteScenarioComparison",
        back_populates="scenarios",
    )

    __table_args__ = (
        UniqueConstraint("comparison_id", "key", name="uq_route_scenarios_comparison_key"),
    )
