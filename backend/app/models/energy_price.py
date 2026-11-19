import uuid
from datetime import date, datetime

from sqlalchemy import Date, Enum as SAEnum, Float, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.enums import FuelType
from app.utils.compat import utcnow_naive


class EnergyPriceSnapshot(Base):
    __tablename__ = "energy_price_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="TR", index=True)
    province_code: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    province_name: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    fuel_type: Mapped[FuelType] = mapped_column(SAEnum(FuelType), nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    price_try: Mapped[float] = mapped_column(Float, nullable=False)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(20), nullable=False, default="FRESH")
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "country",
            "province_code",
            "fuel_type",
            "effective_date",
            "source_name",
            name="uq_energy_price_snapshot_source_day",
        ),
    )
