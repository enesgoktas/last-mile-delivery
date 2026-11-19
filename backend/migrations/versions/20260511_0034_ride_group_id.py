"""Phase post-2.3: ride_group_id column on orders for split rides

Revision ID: 20260511_0034
Revises: 20260511_0033
Create Date: 2026-05-11

When a dispatcher creates a large RIDE order with `allow_split=True`,
the order_service splits it into N sibling orders. All siblings share
the same `ride_group_id` (which equals the first sibling's id) so the
RidesPage list and tracking page can group them visually.

Self-FK to `orders.id` for referential integrity. Indexed for the
list-grouping query. Nullable since most orders aren't split.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260511_0034"
down_revision = "20260511_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "ride_group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_orders_ride_group_id",
        "orders",
        ["ride_group_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_orders_ride_group_id", table_name="orders")
    op.drop_column("orders", "ride_group_id")
