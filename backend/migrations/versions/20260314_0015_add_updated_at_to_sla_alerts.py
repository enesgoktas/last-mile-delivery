"""Add updated_at column to sla_alerts.

Revision ID: 20260314_0015
Revises: 20260314_0014
Create Date: 2026-03-14
"""
import sqlalchemy as sa
from alembic import op

revision = "20260314_0015"
down_revision = "20260314_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sla_alerts",
        sa.Column("updated_at", sa.DateTime, nullable=True, server_default=sa.func.now()),
    )
    op.execute("UPDATE sla_alerts SET updated_at = created_at WHERE updated_at IS NULL")
    op.alter_column("sla_alerts", "updated_at", nullable=False)


def downgrade() -> None:
    op.drop_column("sla_alerts", "updated_at")
