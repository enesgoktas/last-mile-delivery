"""add constraints and indexes

Revision ID: 20260224_0002
Revises: 20260224_0001
Create Date: 2026-02-24 00:31:00.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260224_0002"
down_revision = "20260224_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_route_stops_route_sequence'
            ) THEN
                ALTER TABLE route_stops
                ADD CONSTRAINT uq_route_stops_route_sequence
                UNIQUE (route_id, sequence_number);
            END IF;
        END
        $$;
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orders_status_type_priority_created_at ON orders (status, type, priority, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_route_stops_route_status_sequence ON route_stops (route_id, status, sequence_number)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_order_status_history_order_created_at ON order_status_history (order_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_actor_action_created_at ON audit_logs (actor_user_id, action, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_actor_action_created_at")
    op.execute("DROP INDEX IF EXISTS ix_order_status_history_order_created_at")
    op.execute("DROP INDEX IF EXISTS ix_route_stops_route_status_sequence")
    op.execute("DROP INDEX IF EXISTS ix_orders_status_type_priority_created_at")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_route_stops_route_sequence'
            ) THEN
                ALTER TABLE route_stops
                DROP CONSTRAINT uq_route_stops_route_sequence;
            END IF;
        END
        $$;
        """
    )
