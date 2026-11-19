"""Replay journal — stores enough state to re-run any historical run.

For every solve we capture the `VRPInput` payload (orders, vehicles,
locations, distance/duration matrices) + adapter version + seed +
time budget. Enables three things that are otherwise impossible:

  1. **Solver A/B**: rerun a 3-month-old problem with the new ALNS
     version and measure the delta. Today the only way to test a
     solver change is to wait for new orders.
  2. **Forensic reproducibility**: a dispatcher asks "why was order X
     dropped on 2026-04-12?" — pull the replay row, re-solve, inspect.
  3. **Training corpus**: a corpus of historical VRP problems is the
     ingredient for solver-specific ML models (future work).

The payload is JSONB — large but compressible, and Postgres TOASTs it
automatically beyond the inline threshold. Retention: 1 year hot, then
candidates for cold-storage migration (deferred).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class OptimizationRunReplay(Base):
    __tablename__ = "optimization_run_replays"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    optimization_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("optimization_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    adapter_version: Mapped[str] = mapped_column(String(80), nullable=False)
    random_seed: Mapped[int | None] = mapped_column(nullable=True)
    time_budget_allowed_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_budget_used_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The full VRPInput as a JSON-serializable dict. Postgres TOASTs
    # large rows automatically; we don't pre-compress at the app layer.
    vrp_input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False, index=True
    )
