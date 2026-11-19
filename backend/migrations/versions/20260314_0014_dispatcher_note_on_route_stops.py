"""Add dispatcher_note column to route_stops.

Revision ID: 20260314_0014
Revises: 20260314_0013
Create Date: 2026-03-14
"""
import sqlalchemy as sa
from alembic import op

revision = "20260314_0014"
down_revision = "20260314_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("route_stops", sa.Column("dispatcher_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("route_stops", "dispatcher_note")
