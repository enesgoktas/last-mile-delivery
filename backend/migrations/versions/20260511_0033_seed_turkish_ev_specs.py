"""Phase 2.3 data: seed common Turkish EV vehicle specs

Revision ID: 20260511_0033
Revises: 20260511_0032
Create Date: 2026-05-11

Pre-populates `vehicle_specs` with the EV models a Turkish operator
is likely to encounter, as platform-shared rows (tenant_id NULL).
Each row carries the connector list and battery_kwh needed by the
auto-charging dispatch path.

Idempotent: ON CONFLICT DO NOTHING so re-running is a no-op. Tenants
that override these (e.g. with a different battery_kwh for their
specific trim level) get their tenant-scoped row alongside the
platform one.

km/kWh efficiency is set in `fuel_efficiency_km_per_liter` per the
existing convention for ELECTRIC fuel_type rows (see Renault Kangoo
ZE seed).
"""
import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260511_0033"
down_revision = "20260511_0032"
branch_labels = None
depends_on = None


# Stable IDs so the platform-shared spec for each model survives across
# environments. Public, not secrets.
_EV_SEEDS = [
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000eee01"),
        "make": "Togg",
        "model": "T10X",
        "year": 2024,
        "vehicle_type": "CAR_VAN",
        "fuel_efficiency_km_per_kwh": 6.4,
        "co2_emissions_g_per_km": 0,
        "battery_kwh": 52.4,
        "connectors": ["CCS", "Type2"],
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000eee02"),
        "make": "Tesla",
        "model": "Model 3",
        "year": 2024,
        "vehicle_type": "CAR_VAN",
        "fuel_efficiency_km_per_kwh": 6.7,
        "co2_emissions_g_per_km": 0,
        "battery_kwh": 75.0,
        "connectors": ["NACS", "CCS"],
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000eee03"),
        "make": "Tesla",
        "model": "Model Y",
        "year": 2024,
        "vehicle_type": "CAR_VAN",
        "fuel_efficiency_km_per_kwh": 6.0,
        "co2_emissions_g_per_km": 0,
        "battery_kwh": 75.0,
        "connectors": ["NACS", "CCS"],
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000eee04"),
        "make": "Renault",
        "model": "Zoe",
        "year": 2023,
        "vehicle_type": "CAR_VAN",
        "fuel_efficiency_km_per_kwh": 6.3,
        "co2_emissions_g_per_km": 0,
        "battery_kwh": 52.0,
        "connectors": ["CCS", "Type2"],
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000eee05"),
        "make": "Hyundai",
        "model": "Kona Electric",
        "year": 2024,
        "vehicle_type": "CAR_VAN",
        "fuel_efficiency_km_per_kwh": 6.1,
        "co2_emissions_g_per_km": 0,
        "battery_kwh": 64.0,
        "connectors": ["CCS", "Type2"],
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000eee06"),
        "make": "BYD",
        "model": "Atto 3",
        "year": 2024,
        "vehicle_type": "CAR_VAN",
        "fuel_efficiency_km_per_kwh": 5.5,
        "co2_emissions_g_per_km": 0,
        "battery_kwh": 60.5,
        "connectors": ["CCS", "Type2"],
    },
]


def upgrade() -> None:
    # PostgreSQL treats NULL as distinct in unique constraints (the
    # uq_vehicle_spec_tenant_make_model_year index uses tenant_id which
    # is NULL for platform rows), so ON CONFLICT won't fire for repeat
    # platform inserts. Use WHERE NOT EXISTS for idempotency instead.
    insert = sa.text(
        """
        INSERT INTO vehicle_specs (
            id, tenant_id, make, model, year, vehicle_type, fuel_type,
            fuel_efficiency_km_per_liter, co2_emissions_g_per_km,
            source, osrm_profile, hazmat_capable,
            supported_connector_types, battery_kwh,
            created_at, updated_at
        )
        SELECT
            :id, NULL, :make, :model, :year,
            CAST(:vehicle_type AS vehicletype),
            CAST('ELECTRIC' AS fueltype),
            :efficiency, :co2,
            'PLATFORM_SEED', 'driving', FALSE,
            CAST(:connectors AS jsonb), :battery_kwh,
            NOW(), NOW()
        WHERE NOT EXISTS (
            SELECT 1 FROM vehicle_specs
            WHERE tenant_id IS NULL
              AND make = :make
              AND model = :model
              AND year = :year
        )
        """
    )
    for spec in _EV_SEEDS:
        op.execute(
            insert.bindparams(
                id=spec["id"],
                make=spec["make"],
                model=spec["model"],
                year=spec["year"],
                vehicle_type=spec["vehicle_type"],
                efficiency=spec["fuel_efficiency_km_per_kwh"],
                co2=spec["co2_emissions_g_per_km"],
                connectors=json.dumps(spec["connectors"]),
                battery_kwh=spec["battery_kwh"],
            )
        )


def downgrade() -> None:
    ids = [str(s["id"]) for s in _EV_SEEDS]
    op.execute(
        sa.text("DELETE FROM vehicle_specs WHERE id = ANY(:ids)").bindparams(ids=ids)
    )
