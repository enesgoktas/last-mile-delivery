"""create import_jobs table

Revision ID: 20260224_0003
Revises: 20260224_0002
Create Date: 2026-02-24 00:55:00.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260224_0003"
down_revision = "20260224_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS import_jobs (
            id UUID PRIMARY KEY,
            created_by_user_id UUID NOT NULL REFERENCES users(id),
            status VARCHAR(32) NOT NULL,
            total_rows INTEGER NOT NULL DEFAULT 0,
            valid_rows INTEGER NOT NULL DEFAULT 0,
            invalid_rows INTEGER NOT NULL DEFAULT 0,
            payload JSONB NOT NULL DEFAULT '[]'::jsonb,
            invalid_details JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            published_at TIMESTAMP WITHOUT TIME ZONE NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_import_jobs_created_by_user_id ON import_jobs (created_by_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_import_jobs_status ON import_jobs (status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_import_jobs_status")
    op.execute("DROP INDEX IF EXISTS ix_import_jobs_created_by_user_id")
    op.execute("DROP TABLE IF EXISTS import_jobs")
