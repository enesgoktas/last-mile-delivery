"""create optimization_runs table

Revision ID: 20260224_0005
Revises: 20260224_0004
Create Date: 2026-02-24 02:05:00.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260224_0005"
down_revision = "20260224_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'optimizationruntype') THEN
                CREATE TYPE optimizationruntype AS ENUM ('BATCH', 'INSERTION', 'REOPTIMIZATION');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'optimizationrunstatus') THEN
                CREATE TYPE optimizationrunstatus AS ENUM ('STARTED', 'SUCCEEDED', 'FAILED', 'LOCKED', 'NO_OP');
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS optimization_runs (
            id UUID PRIMARY KEY,
            task_id VARCHAR(120) NULL,
            run_type optimizationruntype NOT NULL,
            status optimizationrunstatus NOT NULL DEFAULT 'STARTED',
            routes_created INTEGER NULL,
            unassigned_count INTEGER NULL,
            solver_status VARCHAR(80) NULL,
            metrics JSONB NULL,
            error_message TEXT NULL,
            started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMP WITHOUT TIME ZONE NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_optimization_runs_task_id ON optimization_runs (task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_optimization_runs_status ON optimization_runs (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_optimization_runs_started_at ON optimization_runs (started_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_optimization_runs_started_at")
    op.execute("DROP INDEX IF EXISTS ix_optimization_runs_status")
    op.execute("DROP INDEX IF EXISTS ix_optimization_runs_task_id")
    op.execute("DROP TABLE IF EXISTS optimization_runs")
    op.execute("DROP TYPE IF EXISTS optimizationrunstatus")
    op.execute("DROP TYPE IF EXISTS optimizationruntype")
