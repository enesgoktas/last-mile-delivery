"""Add DRAFT route status and PENDING_APPROVAL optimization run status.

Revision ID: 20260226_0010
Revises: 20260225_0009
Create Date: 2026-02-26
"""
from alembic import op

revision = "20260226_0010"
down_revision = "20260225_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE routestatus ADD VALUE IF NOT EXISTS 'DRAFT' BEFORE 'PLANNED'")
    op.execute("ALTER TYPE optimizationrunstatus ADD VALUE IF NOT EXISTS 'PENDING_APPROVAL'")


def downgrade() -> None:
    pass
