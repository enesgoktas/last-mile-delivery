import uuid
from datetime import datetime

from sqlalchemy import Boolean, Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.enums import OptimizationRunStatus, OptimizationRunType
from app.utils.compat import utcnow_naive


class OptimizationRun(Base):
    __tablename__ = "optimization_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    run_type: Mapped[OptimizationRunType] = mapped_column(SAEnum(OptimizationRunType), nullable=False)
    status: Mapped[OptimizationRunStatus] = mapped_column(
        SAEnum(OptimizationRunStatus),
        nullable=False,
        default=OptimizationRunStatus.STARTED,
        index=True,
    )
    routes_created: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unassigned_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    solver_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # ── Layer 2: promoted from the opaque `metrics` JSONB into typed
    # columns so the optimization-quality dashboard can filter + aggregate
    # in SQL. JSONB stays for solver-specific extras.
    solver_kind: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    iterations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feasibility_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    fallback_invoked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decomposition_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    warm_start_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    time_budget_used_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_budget_allowed_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
