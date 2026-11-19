import uuid
from datetime import time as dt_time

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import AvailabilityStatus, PushProvider, VehicleType


class Courier(Base):
    __tablename__ = "couriers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False)
    vehicle_type: Mapped[VehicleType] = mapped_column(SAEnum(VehicleType), nullable=False)
    max_weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        SAEnum(AvailabilityStatus), default=AvailabilityStatus.OFFLINE, nullable=False
    )
    zone_polygon: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    depot_lat: Mapped[float] = mapped_column(Float, nullable=False)
    depot_lon: Mapped[float] = mapped_column(Float, nullable=False)
    depot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("depots.id", ondelete="SET NULL"), nullable=True
    )
    # Optional shift-end depot. NULL means "end shift at start (home) depot",
    # which is the only behavior available pre-multi-depot. Consulted only
    # when `features.multi_depot_vehicles_enabled`.
    end_depot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("depots.id", ondelete="SET NULL"), nullable=True
    )
    max_volume_cm3: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_packages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    working_hours_start: Mapped[dt_time] = mapped_column(nullable=False)
    working_hours_end: Mapped[dt_time] = mapped_column(nullable=False)
    fcm_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    push_provider: Mapped[PushProvider | None] = mapped_column(
        SAEnum(PushProvider), nullable=True
    )
    push_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    constraints: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    vehicle_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicle_specs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Phase 1 ride-mode + EV fields ──
    # `max_passengers` is consulted only when features.rides_enabled — vehicles
    # missing it can't carry RIDE orders. Battery fields are consulted only
    # when features.ev_battery_awareness_enabled.
    max_passengers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_battery_range_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_battery_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    battery_low_threshold_pct: Mapped[int] = mapped_column(
        Integer, default=20, server_default="20", nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="courier")
    depot: Mapped["Depot | None"] = relationship(
        "Depot", back_populates="couriers", foreign_keys=[depot_id]
    )
    # Additional reload depots the courier is allowed to serve mid-shift.
    # Empty list means "only the home depot," matching pre-multi-depot
    # behavior. Consulted only when `features.multi_depot_vehicles_enabled`.
    additional_depots: Mapped[list["Depot"]] = relationship(
        "Depot",
        secondary="courier_depots",
        lazy="selectin",
    )
    end_depot: Mapped["Depot | None"] = relationship(
        "Depot", foreign_keys=[end_depot_id]
    )
    routes: Mapped[list["Route"]] = relationship("Route", back_populates="courier")
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="assigned_courier")
    availability_history: Mapped[list["CourierAvailabilityHistory"]] = relationship(
        "CourierAvailabilityHistory",
        back_populates="courier",
        order_by="CourierAvailabilityHistory.created_at",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    vehicle_spec: Mapped["VehicleSpec | None"] = relationship(
        "VehicleSpec", back_populates="couriers", lazy="selectin"
    )
    vehicle_tags: Mapped[list["VehicleTag"]] = relationship(
        "VehicleTag",
        secondary="courier_vehicle_tags",
        lazy="selectin",
    )

    @property
    def additional_depot_ids(self) -> list[uuid.UUID]:
        """Convenience accessor for response serialization. Mirrors the
        loaded `additional_depots` relationship as a plain UUID list."""
        return [depot.id for depot in self.additional_depots]
