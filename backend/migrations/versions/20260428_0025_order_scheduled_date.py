"""orders.scheduled_for_date — actual scheduled day after assignment

Revision ID: 20260428_0025
Revises: 20260428_0024
Create Date: 2026-04-28

Adds `orders.scheduled_for_date` plus a btree index. The column is NULL
while an order is PENDING and gets stamped with the route's chosen day
when the dispatcher (daily or range) materialises the assignment, so
the orders UI can display each order on the day it will *actually* be
delivered, even if that day differs from the original `earliest_delivery`
when the range planner shuffles orders across their windows.

The migration is purely additive and idempotent.
"""
import sqlalchemy as sa
from alembic import op

revision = "20260428_0025"
down_revision = "20260428_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("scheduled_for_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_orders_scheduled_for_date",
        "orders",
        ["scheduled_for_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_orders_scheduled_for_date", table_name="orders")
    op.drop_column("orders", "scheduled_for_date")
