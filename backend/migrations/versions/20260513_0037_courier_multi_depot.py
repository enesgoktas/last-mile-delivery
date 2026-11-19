"""Add courier_depots association + couriers.end_depot_id for multi-depot vehicles.

Revision ID: 20260513_0037
Revises: 20260512_0036
Create Date: 2026-05-13

Adds support for couriers to serve multiple depots within a single shift:
  - `courier_depots` many-to-many table holds *additional* reload depots
    beyond the courier's primary `depot_id`.
  - `couriers.end_depot_id` is the optional shift-end depot; NULL keeps
    the existing behavior (end at start depot).

Both are additive and have no behavioral effect until the per-tenant
`features.multi_depot_vehicles_enabled` flag is turned on. Existing
single-depot couriers are unaffected.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260513_0037"
down_revision = "20260512_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "courier_depots",
        sa.Column(
            "courier_id",
            UUID(as_uuid=True),
            sa.ForeignKey("couriers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "depot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("depots.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.add_column(
        "couriers",
        sa.Column(
            "end_depot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("depots.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("couriers", "end_depot_id")
    op.drop_table("courier_depots")
