"""Add ride-specific fields to recurring_order_templates.

Revision ID: 20260512_0035
Revises: 20260511_0034
Create Date: 2026-05-12

When a tenant has rides_enabled, dispatchers want to schedule recurring
RIDE pickups (e.g. "every weekday morning, take Erman from Raffles to
the HQ"). The recurring-order template gets the same passenger-only
fields the regular Order/OrderCreate has, plus the split toggle.

`weight_kg` becomes nullable so RIDE templates can leave it 0 / NULL.
Existing delivery templates always passed it through, so the column
default of 0 keeps them working.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260512_0035"
down_revision = "20260511_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Allow weight_kg to be NULL / 0 for RIDE templates.
    op.alter_column(
        "recurring_order_templates",
        "weight_kg",
        existing_type=sa.Float(),
        nullable=True,
    )

    op.add_column(
        "recurring_order_templates",
        sa.Column("passenger_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "recurring_order_templates",
        sa.Column("passenger_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "recurring_order_templates",
        sa.Column("passenger_phone", sa.String(50), nullable=True),
    )
    op.add_column(
        "recurring_order_templates",
        sa.Column(
            "flight_info",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "recurring_order_templates",
        sa.Column("special_requirements", sa.Text(), nullable=True),
    )
    op.add_column(
        "recurring_order_templates",
        sa.Column("pickup_buffer_minutes_override", sa.Integer(), nullable=True),
    )
    op.add_column(
        "recurring_order_templates",
        sa.Column(
            "allow_split", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "recurring_order_templates",
        sa.Column("passengers_per_vehicle", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recurring_order_templates", "passengers_per_vehicle")
    op.drop_column("recurring_order_templates", "allow_split")
    op.drop_column("recurring_order_templates", "pickup_buffer_minutes_override")
    op.drop_column("recurring_order_templates", "special_requirements")
    op.drop_column("recurring_order_templates", "flight_info")
    op.drop_column("recurring_order_templates", "passenger_phone")
    op.drop_column("recurring_order_templates", "passenger_name")
    op.drop_column("recurring_order_templates", "passenger_count")
    op.alter_column(
        "recurring_order_templates",
        "weight_kg",
        existing_type=sa.Float(),
        nullable=False,
    )
