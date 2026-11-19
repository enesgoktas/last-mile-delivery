"""Per-(courier, address) empirical service time — the compounding moat.

Layer 3. Today the solver uses a static "10 min per stop" estimate.
Every real stop completion tells us the true service duration. After a
few months we've got a real distribution of how long each courier
takes at each address, and the solver's plan time becomes accurate
instead of optimistic.

Why per-(courier, address) and not just per-address: drivers have
personal rhythms. Some are faster, some are more careful with fragile
goods. Per-courier means the solver doesn't punish a fast driver with
a generic estimate.

The address key is a deterministic hash of `(rounded_lat, rounded_lon)`
to ~10 m precision — close enough that "deliveries to the same flat"
collapse, far enough that "deliveries on the same street" don't. We
keep a rolling median (cheap) and sample count (gate solver use until
we have ≥3 observations).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class EmpiricalServiceTime(Base):
    __tablename__ = "empirical_service_times"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    courier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Geohash of the delivery point. 8 chars ≈ 19 m × 38 m precision —
    # tight enough that two flats in different buildings don't collapse,
    # loose enough that GPS jitter on the same door doesn't fragment.
    address_hash: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # Rolling median of observed service durations.
    service_duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Standard deviation, rough confidence band the solver could use to
    # over/under-weight (future work — today we just expose it).
    confidence_band_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    last_observed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "courier_id",
            "address_hash",
            name="uq_empirical_service_times_tcp",
        ),
    )
