"""Every ETA we promise — paired against the actual arrival.

Layer 2b. The planner writes one row per delivery stop at the moment it
emits the route. The reconciliation cron (fires on stop completion via
the lifecycle helper) updates `actual_arrival` once the courier arrives.

After a month of pilot data this answers questions the platform can't
answer today:

  * Are our ETAs accurate? By how many minutes on average?
  * Does ETA accuracy degrade with the prediction horizon (planner-time
    ETA vs in-flight ETA vs rerouting ETA)?
  * Do specific zones / courier types / order categories have
    systematically worse ETA error?

`prediction_horizon_s` is `predicted_arrival - predicted_at`; lets us
bucket "ETAs predicted 4+ hours out" vs "ETAs predicted just before
arrival."
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import StrEnum, utcnow_naive


class ETAPredictedBy(StrEnum):
    PLANNER = "PLANNER"
    IN_FLIGHT_ETA = "IN_FLIGHT_ETA"
    REROUTING = "REROUTING"


class ETAPredictionLog(Base):
    __tablename__ = "eta_prediction_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("route_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    optimization_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("optimization_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    predicted_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    predicted_arrival: Mapped[datetime] = mapped_column(nullable=False)
    actual_arrival: Mapped[datetime | None] = mapped_column(nullable=True)
    predicted_by: Mapped[ETAPredictedBy] = mapped_column(
        SAEnum(ETAPredictedBy), nullable=False, index=True
    )
    # Convenience: how far in the future the prediction was. Lets queries
    # filter "ETAs ≥4 h horizon" without computing `predicted_arrival -
    # predicted_at` in SQL on every read.
    prediction_horizon_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Signed seconds: positive = late, negative = early. NULL until
    # `actual_arrival` is filled.
    error_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, nullable=False, index=True
    )

    __table_args__ = (
        Index(
            "ix_eta_prediction_log_stop_predicted_by",
            "stop_id",
            "predicted_by",
        ),
    )
