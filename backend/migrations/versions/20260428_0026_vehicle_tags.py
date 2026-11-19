"""vehicle_tags — tenant-scoped tag catalog for vehicles and orders

Revision ID: 20260428_0026
Revises: 20260428_0025
Create Date: 2026-04-28

Adds:
  * vehicle_tags              — tag catalog (id, tenant_id, name, color)
  * courier_vehicle_tags      — couriers ↔ tags (many-to-many)
  * order_required_vehicle_tags           — orders ↔ required tags
  * recurring_order_required_vehicle_tags — templates ↔ required tags

All tables are purely additive; existing rows are unaffected.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260428_0026"
down_revision = "20260428_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vehicle_tags",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("color", sa.String(20), nullable=False, server_default="#6366f1"),
    )
    op.create_index("ix_vehicle_tags_tenant_id", "vehicle_tags", ["tenant_id"])

    op.create_table(
        "courier_vehicle_tags",
        sa.Column(
            "courier_id",
            UUID(as_uuid=True),
            sa.ForeignKey("couriers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vehicle_tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "order_required_vehicle_tags",
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vehicle_tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "recurring_order_required_vehicle_tags",
        sa.Column(
            "template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recurring_order_templates.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vehicle_tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("recurring_order_required_vehicle_tags")
    op.drop_table("order_required_vehicle_tags")
    op.drop_table("courier_vehicle_tags")
    op.drop_index("ix_vehicle_tags_tenant_id", table_name="vehicle_tags")
    op.drop_table("vehicle_tags")
