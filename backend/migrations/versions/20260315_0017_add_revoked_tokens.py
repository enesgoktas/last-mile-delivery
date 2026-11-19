"""Add durable revoked token store.

Revision ID: 20260315_0017
Revises: 20260314_0016
Create Date: 2026-03-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260315_0017"
down_revision = "20260314_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index("ix_revoked_tokens_expires_at", "revoked_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_revoked_tokens_expires_at", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
