"""Add tenant operations policy and customizable order/POD fields.

Revision ID: 20260315_0018
Revises: 20260315_0017
Create Date: 2026-03-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260315_0018"
down_revision = "20260315_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column("operations_policy", JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
    )

    op.add_column("orders", sa.Column("custom_fields", JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("orders", sa.Column("delivery_otp_code", sa.String(length=12), nullable=True))

    op.add_column("route_stops", sa.Column("failure_reason_detail", sa.String(length=255), nullable=True))

    op.add_column("proof_of_delivery", sa.Column("photo_urls", JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("proof_of_delivery", sa.Column("receiver_name", sa.String(length=255), nullable=True))
    op.add_column("proof_of_delivery", sa.Column("otp_code", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("proof_of_delivery", "otp_code")
    op.drop_column("proof_of_delivery", "receiver_name")
    op.drop_column("proof_of_delivery", "photo_urls")

    op.drop_column("route_stops", "failure_reason_detail")

    op.drop_column("orders", "delivery_otp_code")
    op.drop_column("orders", "custom_fields")

    op.drop_column("system_settings", "operations_policy")
