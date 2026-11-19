"""add api key lookup prefix

Revision ID: 20260224_0006
Revises: 20260224_0005
Create Date: 2026-02-24 03:20:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260224_0006"
down_revision = "20260224_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE api_clients
        ADD COLUMN IF NOT EXISTS api_key_prefix VARCHAR(16)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_api_clients_api_key_prefix
        ON api_clients (api_key_prefix)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_api_clients_api_key_prefix")
    op.execute(
        """
        ALTER TABLE api_clients
        DROP COLUMN IF EXISTS api_key_prefix
        """
    )
