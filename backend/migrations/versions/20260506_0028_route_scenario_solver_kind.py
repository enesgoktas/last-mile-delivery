"""Add solver_kind column to route_scenarios

Revision ID: 20260506_0028
Revises: 20260504_0027
Create Date: 2026-05-06
"""
import sqlalchemy as sa
from alembic import op

revision = "20260506_0028"
down_revision = "20260504_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add column with server_default for backfill of existing rows
    op.add_column(
        "route_scenarios",
        sa.Column(
            "solver_kind",
            sa.String(length=40),
            nullable=False,
            server_default="ortools_cvrptw",
        ),
    )
    # Correctly tag the human-dispatcher rows
    op.execute(
        "UPDATE route_scenarios SET solver_kind = 'human_dispatcher' "
        "WHERE key = 'human_dispatcher_baseline'"
    )
    # Drop the server_default now that the backfill is done
    op.alter_column("route_scenarios", "solver_kind", server_default=None)


def downgrade() -> None:
    op.drop_column("route_scenarios", "solver_kind")
