import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import FuelType, VehicleType
from app.utils.compat import utcnow_naive


class VehicleSpec(Base):
    __tablename__ = "vehicle_specs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    make: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vehicle_type: Mapped[VehicleType] = mapped_column(SAEnum(VehicleType), nullable=False)
    fuel_type: Mapped[FuelType] = mapped_column(SAEnum(FuelType), nullable=False)
    fuel_efficiency_km_per_liter: Mapped[float] = mapped_column(Float, nullable=False)
    co2_emissions_g_per_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    epa_vehicle_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="MANUAL")
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    length_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_weight_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    axle_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    emission_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    hazmat_capable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    osrm_profile: Mapped[str] = mapped_column(String(32), nullable=False, default="driving", index=True)
    # ── Phase 2.3 EV fields ──
    # `supported_connector_types` lists the connectors this vehicle model
    # accepts (e.g. ["CCS", "Type2"]). Auto-charging dispatch intersects
    # this with each candidate ChargingStation.connector_types.
    # `battery_kwh` drives charge-time estimation. Both are nullable for
    # non-EVs / legacy specs — the EV constraint paths skip vehicles
    # without these fields populated.
    supported_connector_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    battery_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    # Relationships
    couriers: Mapped[list["Courier"]] = relationship("Courier", back_populates="vehicle_spec")

    __table_args__ = (
        UniqueConstraint("tenant_id", "make", "model", "year", name="uq_vehicle_spec_tenant_make_model_year"),
    )
