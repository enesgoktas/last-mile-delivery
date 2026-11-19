"""failed delivery escalation fields

Revision ID: 20260224_0008
Revises: 20260224_0007
Create Date: 2026-02-24 16:30:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260224_0008"
down_revision: Union[str, None] = "20260224_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'failedordercustomerresponsestatus') THEN
                CREATE TYPE failedordercustomerresponsestatus AS ENUM ('PENDING', 'RESPONDED', 'ESCALATED');
            END IF;
        END
        $$;
        """
    )
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS failed_at TIMESTAMP NULL")
    op.execute(
        """
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS customer_response_status failedordercustomerresponsestatus NULL
        """
    )
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_response_due_at TIMESTAMP NULL")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMP NULL")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_orders_failed_response_status_due
        ON orders (customer_response_status, customer_response_due_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_orders_failed_response_status_due")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS escalated_at")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS customer_response_due_at")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS customer_response_status")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS failed_at")
    op.execute("DROP TYPE IF EXISTS failedordercustomerresponsestatus")
