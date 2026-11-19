"""Phase 2.3: charging_stations + vehicle_spec EV connector / battery fields

Revision ID: 20260511_0032
Revises: 20260510_0031
Create Date: 2026-05-11

Schema for the auto-charging-stops feature (gated by
features.ev_auto_charging_enabled per tenant):

* `charging_stations` — platform-shared (tenant_id NULL) by default,
  with optional per-tenant overrides (tenant_id set). Mirrors the
  `vehicle_specs` nullable-tenant pattern. Daily Open Charge Map sync
  upserts platform-shared rows.
* `vehicle_specs.supported_connector_types` (JSONB list) — what
  connectors a vehicle model supports (CCS, CHAdeMO, Type2, NACS).
* `vehicle_specs.battery_kwh` (float) — needed for charge-time
  estimation in the dispatch helper.

No data is seeded here; pre-seeding common Turkish EVs is in a
follow-up data migration so the structural change can be reviewed
independently.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260511_0032"
down_revision = "20260510_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. charging_stations table
    op.create_table(
        "charging_stations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("provider", sa.String(20), nullable=False, server_default="MANUAL"),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("operator", sa.String(128), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("province_code", sa.String(8), nullable=True),
        sa.Column("country_code", sa.String(4), nullable=False, server_default="TR"),
        sa.Column(
            "connector_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("max_kw", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_dc", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_24_7", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "operating_hours",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # `external_id` is unique per provider — same OCM ID may appear in
        # both OCM and a tenant-MANUAL row referencing the same physical
        # charger (rare but valid). Tenant-NULL platform rows must be
        # unique by (provider, external_id).
        sa.UniqueConstraint(
            "provider",
            "external_id",
            "tenant_id",
            name="uq_charging_stations_provider_external_tenant",
        ),
    )
    op.create_index(
        "ix_charging_stations_country_province_active",
        "charging_stations",
        ["country_code", "province_code", "is_active"],
    )
    # Geo lookup is done in app code via Haversine; a simple lat/lon BTREE
    # is enough for bbox pre-filtering. PostGIS would be nicer but adds
    # a deploy dependency we don't have today.
    op.create_index(
        "ix_charging_stations_lat_lon",
        "charging_stations",
        ["lat", "lon"],
    )
    op.create_index(
        "ix_charging_stations_operator",
        "charging_stations",
        ["operator"],
    )

    # 2. vehicle_specs EV fields
    op.add_column(
        "vehicle_specs",
        sa.Column(
            "supported_connector_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "vehicle_specs",
        sa.Column("battery_kwh", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vehicle_specs", "battery_kwh")
    op.drop_column("vehicle_specs", "supported_connector_types")
    op.drop_index("ix_charging_stations_operator", table_name="charging_stations")
    op.drop_index("ix_charging_stations_lat_lon", table_name="charging_stations")
    op.drop_index(
        "ix_charging_stations_country_province_active", table_name="charging_stations"
    )
    op.drop_table("charging_stations")
