"""vehicle specs catalog

Revision ID: 20260416_0022
Revises: 20260414_0021
Create Date: 2026-04-16

Changes:
- fueltype enum: new GASOLINE, DIESEL, LPG, ELECTRIC, HYBRID values
- vehicle_specs: new table for make/model/year fuel efficiency catalog
- couriers: add vehicle_spec_id FK (nullable, SET NULL on delete)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, UUID

revision = "20260416_0022"
down_revision = "20260414_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the new fueltype enum using raw SQL so it stays within the
    # Alembic transaction and does not re-fire when referenced in create_table.
    op.execute(
        "CREATE TYPE fueltype AS ENUM ('GASOLINE', 'DIESEL', 'LPG', 'ELECTRIC', 'HYBRID')"
    )

    # Both enum columns use PG_ENUM with create_type=False so that
    # op.create_table does NOT trigger a second CREATE TYPE via _on_table_create.
    op.create_table(
        "vehicle_specs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("make", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("year", sa.Integer, nullable=True),
        sa.Column("vehicle_type", PG_ENUM(name="vehicletype", create_type=False), nullable=False),
        sa.Column("fuel_type", PG_ENUM(name="fueltype", create_type=False), nullable=False),
        sa.Column("fuel_efficiency_km_per_liter", sa.Float, nullable=False),
        sa.Column("co2_emissions_g_per_km", sa.Float, nullable=True),
        sa.Column("epa_vehicle_id", sa.String(64), nullable=True, unique=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="MANUAL"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("tenant_id", "make", "model", "year", name="uq_vehicle_spec_tenant_make_model_year"),
    )
    op.create_index("ix_vehicle_specs_tenant_id", "vehicle_specs", ["tenant_id"])
    op.create_index("ix_vehicle_specs_make", "vehicle_specs", ["make"])
    op.create_index("ix_vehicle_specs_model", "vehicle_specs", ["model"])

    op.add_column(
        "couriers",
        sa.Column(
            "vehicle_spec_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vehicle_specs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_couriers_vehicle_spec_id", "couriers", ["vehicle_spec_id"])


def downgrade() -> None:
    op.drop_index("ix_couriers_vehicle_spec_id", table_name="couriers")
    op.drop_column("couriers", "vehicle_spec_id")

    op.drop_index("ix_vehicle_specs_model", table_name="vehicle_specs")
    op.drop_index("ix_vehicle_specs_make", table_name="vehicle_specs")
    op.drop_index("ix_vehicle_specs_tenant_id", table_name="vehicle_specs")
    op.drop_table("vehicle_specs")

    op.execute("DROP TYPE IF EXISTS fueltype")
