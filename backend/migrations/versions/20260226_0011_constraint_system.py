"""Add constraint system: system_settings table + courier constraints column.

Revision ID: 20260226_0011
Revises: 20260226_0010
Create Date: 2026-02-26
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "20260226_0011"
down_revision = "20260226_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer, primary_key=True, default=1),
        sa.Column("global_constraints", JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.execute("INSERT INTO system_settings (id, global_constraints) VALUES (1, '{}')")

    op.add_column("couriers", sa.Column("constraints", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("couriers", "constraints")
    op.drop_table("system_settings")
