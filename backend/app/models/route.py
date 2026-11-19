import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import (
    FailureReason,
    RouteStatus,
    StopDelayCause,
    StopStatus,
    StopType,
)
from app.utils.compat import utcnow_naive


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    courier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("couriers.id"), nullable=False)
    status: Mapped[RouteStatus] = mapped_column(SAEnum(RouteStatus), default=RouteStatus.PLANNED, nullable=False)
    planned_distance_km: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actual_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    optimization_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    courier: Mapped["Courier"] = relationship("Courier", back_populates="routes")
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="route")
    stops: Mapped[list["RouteStop"]] = relationship(
        "RouteStop",
        back_populates="route",
        order_by="RouteStop.sequence_number",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    status_history: Mapped[list["RouteStatusHistory"]] = relationship(
        "RouteStatusHistory",
        back_populates="route",
        order_by="RouteStatusHistory.created_at",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RouteStop(Base):
    __tablename__ = "route_stops"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("routes.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_type: Mapped[StopType] = mapped_column(SAEnum(StopType), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    planned_arrival: Mapped[datetime] = mapped_column(nullable=False)
    # ``planned_departure`` is the arrival + service-time the solver
    # produced. Critical for CHARGING (the solver-allocated charge
    # duration) and DEPOT_RELOAD (loading time) stops where the stop's
    # "service time" carries operational meaning. NULL for legacy rows
    # written before this column existed.
    planned_departure: Mapped[datetime | None] = mapped_column(nullable=True)
    actual_arrival: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[StopStatus] = mapped_column(SAEnum(StopStatus), default=StopStatus.PENDING, nullable=False)
    failure_reason: Mapped[FailureReason | None] = mapped_column(SAEnum(FailureReason), nullable=True)
    failure_reason_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Typed cause for non-failure delays (stop completed but late). Distinct
    # from `failure_reason` which only applies when the stop status is FAILED.
    # See `StopDelayCause` for the canonical categorization.
    delay_cause: Mapped[StopDelayCause | None] = mapped_column(
        SAEnum(StopDelayCause), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatcher_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Phase 1 ride-mode passenger-wait fields ──
    # `wait_required` is set by the dispatch helper when the stop belongs
    # to a RIDE order and `features.ride_no_show_handling_enabled` — drivers
    # see a wait timer + no-show button on the mobile app. `passenger_arrived_at`
    # is recorded when the courier hits "Passenger boarded".
    wait_required: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.false(), nullable=False
    )
    max_wait_minutes: Mapped[int] = mapped_column(
        Integer, default=10, server_default="10", nullable=False
    )
    passenger_arrived_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    route: Mapped["Route"] = relationship("Route", back_populates="stops")
    order: Mapped["Order"] = relationship("Order", back_populates="route_stops")
    proof_of_delivery: Mapped["ProofOfDelivery"] = relationship("ProofOfDelivery", back_populates="route_stop", uselist=False)
    status_history: Mapped[list["StopStatusHistory"]] = relationship(
        "StopStatusHistory",
        back_populates="stop",
        order_by="StopStatusHistory.created_at",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def customer_phone(self) -> str | None:
        return self.order.customer_phone if self.order is not None else None
