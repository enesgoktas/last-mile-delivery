"""ChargingStation — platform-shared with optional per-tenant overrides.

Mirrors the `vehicle_specs` nullable-tenant pattern: rows with
`tenant_id IS NULL` are platform-wide (synced from Open Charge Map),
rows with a tenant set are tenant-private (e.g. the depot's own
wallbox, or a manually-curated list).

The auto-charging dispatch path (`features.ev_auto_charging_enabled`)
queries platform + own-tenant rows together when picking candidate
chargers around a planned route.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.compat import utcnow_naive


class ChargingStation(Base):
    __tablename__ = "charging_stations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Provenance. "OCM" = Open Charge Map sync, "MANUAL" = added by a
    # superadmin or tenant admin via the API.
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="MANUAL")
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    operator: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    province_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    country_code: Mapped[str] = mapped_column(String(4), nullable=False, default="TR")

    # Connectors and capability. `connector_types` is a JSONB list of strings
    # using a small canonical set: "CCS", "CHAdeMO", "Type2", "NACS", "Type1".
    # `max_kw` is the highest kW among the station's connectors (the value
    # actually used for charge-time estimation when planning a route).
    connector_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    max_kw: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_dc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Hours. `is_24_7=True` short-circuits the operating_hours check; only
    # populate `operating_hours` when a station has restricted hours.
    is_24_7: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    operating_hours: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_id",
            "tenant_id",
            name="uq_charging_stations_provider_external_tenant",
        ),
    )
