"""Per-(run, order) record of the dispatch decision the solver made.

Captures the *why* behind every assignment: which courier won, the
solver's objective score for that choice, and the top-N runner-up
couriers with their why-not reason. Three weeks later, the question
"why did the solver give order X to courier Y instead of Z?" becomes
answerable without re-running the solver or grep'ing log files.

Shared by all four dispatch paths via the `kind` column:
  - BATCH_DISPATCH        (daily flow)
  - RANGE_DISPATCH        (multi-day flow)
  - ON_DEMAND_INSERTION   (single new order inserted into an existing route)
  - REOPTIMIZATION        (courier offline / disruption-driven re-solve)
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.enums import DispatchDecisionKind
from app.utils.compat import utcnow_naive


class DispatchDecisionLog(Base):
    __tablename__ = "dispatch_decision_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    optimization_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("optimization_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chosen_courier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="SET NULL"),
        # NULL when the order was left unassigned. The `alternatives` JSONB
        # then explains why every courier was rejected.
        nullable=True,
    )
    chosen_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    kind: Mapped[DispatchDecisionKind] = mapped_column(
        SAEnum(DispatchDecisionKind), nullable=False, index=True
    )
    # Top-3 runner-up couriers with their (courier_id, score, reason).
    # JSONB is intentional here: this is genuinely variable-shape data
    # (different solvers expose different scoring dimensions; some paths
    # have no alternatives at all).
    alternatives: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False, index=True
    )

    __table_args__ = (
        Index(
            "ix_dispatch_decision_log_run_order",
            "optimization_run_id",
            "order_id",
        ),
    )
