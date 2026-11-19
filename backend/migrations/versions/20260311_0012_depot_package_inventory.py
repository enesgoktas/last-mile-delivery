"""Add depots, packages, inventory_events tables; extend couriers and orders.

Revision ID: 20260311_0012
Revises: 20260226_0011
Create Date: 2026-03-11
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision = "20260311_0012"
down_revision = "20260226_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Depots ---
    op.create_table(
        "depots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(500), nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("operating_hours_start", sa.Time, nullable=False),
        sa.Column("operating_hours_end", sa.Time, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # --- Couriers: add depot_id, max_volume_cm3, max_packages ---
    op.add_column("couriers", sa.Column("depot_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_couriers_depot_id",
        "couriers",
        "depots",
        ["depot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("couriers", sa.Column("max_volume_cm3", sa.Float, nullable=True))
    op.add_column("couriers", sa.Column("max_packages", sa.Integer, nullable=True))

    # --- Orders: add depot_id ---
    op.add_column("orders", sa.Column("depot_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_orders_depot_id",
        "orders",
        "depots",
        ["depot_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- Route stops: make order_id nullable (for depot return/reload stops) ---
    op.alter_column("route_stops", "order_id", existing_type=UUID(as_uuid=True), nullable=True)

    # --- Packages ---
    op.create_table(
        "packages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(100), unique=True, nullable=False),
        sa.Column("weight_kg", sa.Float, nullable=False),
        sa.Column("length_cm", sa.Float, nullable=True),
        sa.Column("width_cm", sa.Float, nullable=True),
        sa.Column("height_cm", sa.Float, nullable=True),
        sa.Column("volume_cm3", sa.Float, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "AT_DEPOT", "LOADED", "IN_TRANSIT", "DELIVERED",
                "RETURNED_TO_DEPOT", "LOST",
                name="packagestatus",
            ),
            nullable=False,
            server_default="AT_DEPOT",
        ),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_packages_order_id", "packages", ["order_id"])

    # --- Inventory Events ---
    op.create_table(
        "inventory_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("package_id", UUID(as_uuid=True), sa.ForeignKey("packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "RECEIVED_AT_DEPOT", "LOADED_ONTO_VEHICLE", "DEPARTED_DEPOT",
                "DELIVERED_TO_CUSTOMER", "PICKED_UP_FROM_CUSTOMER",
                "RETURNED_TO_DEPOT", "MARKED_LOST",
                name="inventoryeventtype",
            ),
            nullable=False,
        ),
        sa.Column("courier_id", UUID(as_uuid=True), sa.ForeignKey("couriers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("depot_id", UUID(as_uuid=True), sa.ForeignKey("depots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_inventory_events_package_id", "inventory_events", ["package_id"])
    op.create_index("ix_inventory_events_courier_id", "inventory_events", ["courier_id"])

    # --- Add new StopType enum values ---
    op.execute("ALTER TYPE stoptype ADD VALUE IF NOT EXISTS 'DEPOT_RETURN'")
    op.execute("ALTER TYPE stoptype ADD VALUE IF NOT EXISTS 'DEPOT_RELOAD'")


def downgrade() -> None:
    op.drop_index("ix_inventory_events_courier_id", table_name="inventory_events")
    op.drop_index("ix_inventory_events_package_id", table_name="inventory_events")
    op.drop_table("inventory_events")
    op.execute("DROP TYPE IF EXISTS inventoryeventtype")

    op.drop_index("ix_packages_order_id", table_name="packages")
    op.drop_table("packages")
    op.execute("DROP TYPE IF EXISTS packagestatus")

    op.alter_column("route_stops", "order_id", existing_type=UUID(as_uuid=True), nullable=False)

    op.drop_constraint("fk_orders_depot_id", "orders", type_="foreignkey")
    op.drop_column("orders", "depot_id")

    op.drop_column("couriers", "max_packages")
    op.drop_column("couriers", "max_volume_cm3")
    op.drop_constraint("fk_couriers_depot_id", "couriers", type_="foreignkey")
    op.drop_column("couriers", "depot_id")

    op.drop_table("depots")
