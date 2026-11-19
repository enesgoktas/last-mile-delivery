"""add EXPIRED order status

Revision ID: 20260414_0021
Revises: 20260326_0020
Create Date: 2026-04-14

Changes:
- orderstatus enum: add EXPIRED value for orders whose delivery window has passed
  without ever being assigned
"""

from alembic import op

revision = "20260414_0021"
down_revision = "20260326_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'EXPIRED'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values directly.
    # To roll back, recreate the enum without EXPIRED and cast existing rows.
    op.execute(
        """
        UPDATE orders SET status = 'CANCELLED'
        WHERE status = 'EXPIRED'
        """
    )
    op.execute(
        """
        ALTER TABLE orders
          ALTER COLUMN status TYPE VARCHAR(50)
        """
    )
    op.execute("DROP TYPE orderstatus")
    op.execute(
        """
        CREATE TYPE orderstatus AS ENUM (
            'DRAFT', 'PENDING', 'ASSIGNED', 'PICKED_UP',
            'IN_TRANSIT', 'DELIVERED', 'FAILED', 'RESCHEDULED', 'CANCELLED'
        )
        """
    )
    op.execute(
        """
        ALTER TABLE orders
          ALTER COLUMN status TYPE orderstatus
          USING status::orderstatus
        """
    )
