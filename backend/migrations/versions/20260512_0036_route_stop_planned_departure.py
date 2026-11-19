"""Add planned_departure column to route_stops.

Revision ID: 20260512_0036
Revises: 20260512_0035
Create Date: 2026-05-12

Stops where the service time carries operational meaning — CHARGING
(EV charge duration), DEPOT_RELOAD (loading time), even long-service
DELIVERY/PICKUP stops — need to expose the *departure* time the solver
chose, not just the arrival time. The dispatcher UI and mobile app
both use this to show how long a driver is expected to spend at the
stop. Backfill is intentionally NULL so existing rows remain valid;
the next dispatch run repopulates them.
"""
import sqlalchemy as sa
from alembic import op

revision = "20260512_0036"
down_revision = "20260512_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "route_stops",
        sa.Column("planned_departure", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("route_stops", "planned_departure")
