"""notification and webhook hardening

Revision ID: 20260224_0004
Revises: 20260224_0003
Create Date: 2026-02-24 01:20:00.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260224_0004"
down_revision = "20260224_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE notificationstatus ADD VALUE IF NOT EXISTS 'SUPPRESSED'")

    op.execute(
        """
        ALTER TABLE notifications
        ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(128),
        ADD COLUMN IF NOT EXISTS payload JSONB,
        ADD COLUMN IF NOT EXISTS provider_message_id VARCHAR(255),
        ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notifications_dedupe_key ON notifications (dedupe_key)"
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'webhookdeliverystatus') THEN
                CREATE TYPE webhookdeliverystatus AS ENUM ('SUCCESS', 'RETRY_SCHEDULED', 'DEAD_LETTER');
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        ALTER TABLE webhook_delivery_log
        ADD COLUMN IF NOT EXISTS delivery_status webhookdeliverystatus,
        ADD COLUMN IF NOT EXISTS error_message TEXT
        """
    )

    op.execute(
        """
        UPDATE webhook_delivery_log
        SET delivery_status = CASE
            WHEN response_status BETWEEN 200 AND 299 THEN 'SUCCESS'::webhookdeliverystatus
            WHEN next_retry_at IS NOT NULL THEN 'RETRY_SCHEDULED'::webhookdeliverystatus
            ELSE 'DEAD_LETTER'::webhookdeliverystatus
        END
        WHERE delivery_status IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE webhook_delivery_log
        ALTER COLUMN delivery_status SET NOT NULL
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_delivery_log_delivery_status ON webhook_delivery_log (delivery_status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_webhook_delivery_log_delivery_status")
    op.execute(
        """
        ALTER TABLE webhook_delivery_log
        DROP COLUMN IF EXISTS error_message,
        DROP COLUMN IF EXISTS delivery_status
        """
    )
    op.execute("DROP TYPE IF EXISTS webhookdeliverystatus")

    op.execute("DROP INDEX IF EXISTS ix_notifications_dedupe_key")
    op.execute(
        """
        ALTER TABLE notifications
        DROP COLUMN IF EXISTS created_at,
        DROP COLUMN IF EXISTS provider_message_id,
        DROP COLUMN IF EXISTS payload,
        DROP COLUMN IF EXISTS dedupe_key
        """
    )
