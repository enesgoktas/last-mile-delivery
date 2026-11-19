"""routing realism primitives

Revision ID: 20260427_0023
Revises: 20260416_0022
Create Date: 2026-04-27

Changes:
  - vehicle_specs: add physical dimension columns + osrm_profile
  - ordertype enum: add PICKUP_AND_DELIVERY, RETURN
  - inventoryeventtype enum: add RETURN_RECEIVED
  - orderlegkind / orderlegstatus enums: add for linehaul legs
  - orders: add return_of_order_id self-FK
  - depot_edges table: linehaul inter-depot connections
  - order_legs table: per-leg tracking for multi-depot orders
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB, UUID

revision = "20260427_0023"
down_revision = "20260416_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # vehicle_specs — physical dimensions + per-vehicle OSRM profile
    # ------------------------------------------------------------------
    op.add_column("vehicle_specs", sa.Column("height_cm", sa.Integer(), nullable=True))
    op.add_column("vehicle_specs", sa.Column("width_cm", sa.Integer(), nullable=True))
    op.add_column("vehicle_specs", sa.Column("length_cm", sa.Integer(), nullable=True))
    op.add_column("vehicle_specs", sa.Column("gross_weight_kg", sa.Integer(), nullable=True))
    op.add_column("vehicle_specs", sa.Column("axle_count", sa.Integer(), nullable=True))
    op.add_column("vehicle_specs", sa.Column("emission_class", sa.String(length=20), nullable=True))
    op.add_column(
        "vehicle_specs",
        sa.Column("hazmat_capable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "vehicle_specs",
        sa.Column("osrm_profile", sa.String(length=32), nullable=False, server_default="driving"),
    )
    op.create_index("ix_vehicle_specs_osrm_profile", "vehicle_specs", ["osrm_profile"])

    # ------------------------------------------------------------------
    # Enum extensions (must happen outside a transaction in Postgres)
    # ------------------------------------------------------------------
    op.execute("ALTER TYPE ordertype ADD VALUE IF NOT EXISTS 'PICKUP_AND_DELIVERY'")
    op.execute("ALTER TYPE ordertype ADD VALUE IF NOT EXISTS 'RETURN'")
    op.execute("ALTER TYPE inventoryeventtype ADD VALUE IF NOT EXISTS 'RETURN_RECEIVED'")

    # ------------------------------------------------------------------
    # Enums for linehaul legs
    # ------------------------------------------------------------------
    op.execute("CREATE TYPE orderlegkind AS ENUM ('FIRST_MILE', 'LINEHAUL', 'LAST_MILE')")
    op.execute(
        "CREATE TYPE orderlegstatus AS ENUM "
        "('PENDING', 'PLANNED', 'IN_TRANSIT', 'COMPLETED', 'CANCELLED')"
    )

    # ------------------------------------------------------------------
    # orders — return-order self-reference
    # ------------------------------------------------------------------
    op.add_column("orders", sa.Column("return_of_order_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_orders_return_of_order_id",
        "orders",
        "orders",
        ["return_of_order_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_orders_return_of_order_id", "orders", ["return_of_order_id"])

    # ------------------------------------------------------------------
    # depot_edges — linehaul inter-depot connection definitions
    # ------------------------------------------------------------------
    op.create_table(
        "depot_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_depot_id", UUID(as_uuid=True), sa.ForeignKey("depots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_depot_id", UUID(as_uuid=True), sa.ForeignKey("depots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transit_minutes", sa.Integer(), nullable=False),
        sa.Column("cutoff_time", sa.Time(), nullable=False),
        sa.Column("service_days", JSONB, nullable=False),
        sa.Column("vehicle_class", sa.String(32), nullable=False, server_default="truck"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("tenant_id", "from_depot_id", "to_depot_id", name="uq_depot_edges_tenant_pair"),
    )
    op.create_index("ix_depot_edges_tenant_id", "depot_edges", ["tenant_id"])
    op.create_index("ix_depot_edges_from_depot_id", "depot_edges", ["from_depot_id"])
    op.create_index("ix_depot_edges_to_depot_id", "depot_edges", ["to_depot_id"])

    # ------------------------------------------------------------------
    # order_legs — per-leg tracking for multi-depot orders
    # ------------------------------------------------------------------
    op.create_table(
        "order_legs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("leg_kind", PG_ENUM(name="orderlegkind", create_type=False), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("from_depot_id", UUID(as_uuid=True), sa.ForeignKey("depots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("to_depot_id", UUID(as_uuid=True), sa.ForeignKey("depots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", PG_ENUM(name="orderlegstatus", create_type=False), nullable=False, server_default="PENDING"),
        sa.Column("planned_pickup_at", sa.DateTime(), nullable=True),
        sa.Column("planned_delivery_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_order_legs_tenant_id", "order_legs", ["tenant_id"])
    op.create_index("ix_order_legs_order_id", "order_legs", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_order_legs_order_id", table_name="order_legs")
    op.drop_index("ix_order_legs_tenant_id", table_name="order_legs")
    op.drop_table("order_legs")
    op.drop_index("ix_depot_edges_to_depot_id", table_name="depot_edges")
    op.drop_index("ix_depot_edges_from_depot_id", table_name="depot_edges")
    op.drop_index("ix_depot_edges_tenant_id", table_name="depot_edges")
    op.drop_table("depot_edges")
    op.drop_index("ix_orders_return_of_order_id", table_name="orders")
    op.drop_constraint("fk_orders_return_of_order_id", "orders", type_="foreignkey")
    op.drop_column("orders", "return_of_order_id")
    op.execute("DROP TYPE IF EXISTS orderlegstatus")
    op.execute("DROP TYPE IF EXISTS orderlegkind")
    op.drop_index("ix_vehicle_specs_osrm_profile", table_name="vehicle_specs")
    op.drop_column("vehicle_specs", "osrm_profile")
    op.drop_column("vehicle_specs", "hazmat_capable")
    op.drop_column("vehicle_specs", "emission_class")
    op.drop_column("vehicle_specs", "axle_count")
    op.drop_column("vehicle_specs", "gross_weight_kg")
    op.drop_column("vehicle_specs", "length_cm")
    op.drop_column("vehicle_specs", "width_cm")
    op.drop_column("vehicle_specs", "height_cm")
