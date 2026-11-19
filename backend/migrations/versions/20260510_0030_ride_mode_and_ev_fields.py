"""Phase 1: ride mode + EV battery awareness — model extensions

Revision ID: 20260510_0030
Revises: 20260510_0029
Create Date: 2026-05-10

Adds (all nullable / defaulted; existing rows unaffected):
  * orders.passenger_count, passenger_name, passenger_phone,
    flight_info, special_requirements,
    pickup_buffer_minutes_override, service_time_pickup_seconds_override
  * couriers.max_passengers,
    max_battery_range_km, current_battery_pct, battery_low_threshold_pct
  * route_stops.wait_required, max_wait_minutes, passenger_arrived_at
  * Extends `ordertype` enum with RIDE
  * Extends `stoptype` enum with CHARGING

Every behaviour gated by these fields is also gated by the per-tenant
feature flags introduced in Phase 0 (rides_enabled, ev_battery_awareness_
enabled, etc.). Tenants without those flags see no behavioural change.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260510_0030"
down_revision = "20260510_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Extend enums (must run outside a transaction block).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ordertype ADD VALUE IF NOT EXISTS 'RIDE'")
        op.execute("ALTER TYPE stoptype ADD VALUE IF NOT EXISTS 'CHARGING'")

    # 2. orders — passenger / ride fields.
    op.add_column(
        "orders",
        sa.Column("passenger_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("orders", sa.Column("passenger_name", sa.String(255), nullable=True))
    op.add_column("orders", sa.Column("passenger_phone", sa.String(50), nullable=True))
    op.add_column(
        "orders",
        sa.Column("flight_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "orders", sa.Column("special_requirements", sa.Text(), nullable=True)
    )
    op.add_column(
        "orders",
        sa.Column("pickup_buffer_minutes_override", sa.Integer(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column(
            "service_time_pickup_seconds_override", sa.Integer(), nullable=True
        ),
    )

    # 3. couriers — passenger capacity + EV fields.
    op.add_column("couriers", sa.Column("max_passengers", sa.Integer(), nullable=True))
    op.add_column(
        "couriers", sa.Column("max_battery_range_km", sa.Integer(), nullable=True)
    )
    op.add_column(
        "couriers", sa.Column("current_battery_pct", sa.Integer(), nullable=True)
    )
    op.add_column(
        "couriers",
        sa.Column(
            "battery_low_threshold_pct",
            sa.Integer(),
            nullable=False,
            server_default="20",
        ),
    )

    # 4. route_stops — passenger wait flow.
    op.add_column(
        "route_stops",
        sa.Column(
            "wait_required", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "route_stops",
        sa.Column("max_wait_minutes", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "route_stops", sa.Column("passenger_arrived_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("route_stops", "passenger_arrived_at")
    op.drop_column("route_stops", "max_wait_minutes")
    op.drop_column("route_stops", "wait_required")
    op.drop_column("couriers", "battery_low_threshold_pct")
    op.drop_column("couriers", "current_battery_pct")
    op.drop_column("couriers", "max_battery_range_km")
    op.drop_column("couriers", "max_passengers")
    op.drop_column("orders", "service_time_pickup_seconds_override")
    op.drop_column("orders", "pickup_buffer_minutes_override")
    op.drop_column("orders", "special_requirements")
    op.drop_column("orders", "flight_info")
    op.drop_column("orders", "passenger_phone")
    op.drop_column("orders", "passenger_name")
    op.drop_column("orders", "passenger_count")
    # Note: PostgreSQL doesn't support DROP VALUE on enum types without
    # recreating the type; leaving the unused enum values is harmless.
