"""Add sla_alerts table for time-window violation monitoring.

Revision ID: 20260314_0013
Revises: 20260311_0012
Create Date: 2026-03-14
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision = "20260314_0013"
down_revision = "20260311_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sla_alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("route_id", UUID(as_uuid=True), sa.ForeignKey("routes.id"), nullable=False),
        sa.Column("stop_id", UUID(as_uuid=True), sa.ForeignKey("route_stops.id"), nullable=True),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("courier_id", UUID(as_uuid=True), sa.ForeignKey("couriers.id"), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("estimated_delay_seconds", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dedupe_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("acknowledged_at", sa.DateTime, nullable=True),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_sla_alerts_status", "sla_alerts", ["status"])
    op.create_index("ix_sla_alerts_route_id", "sla_alerts", ["route_id"])
    op.create_index("ix_sla_alerts_severity", "sla_alerts", ["severity"])


def downgrade() -> None:
    op.drop_index("ix_sla_alerts_severity")
    op.drop_index("ix_sla_alerts_route_id")
    op.drop_index("ix_sla_alerts_status")
    op.drop_table("sla_alerts")
