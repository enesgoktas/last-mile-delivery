"""Layer 0 — close the data black holes.

Pilot data strategy, Layer 0 (pre-pilot). Adds four append-only tables
that capture state changes which today happen invisibly:

  - `route_status_history`            — Route.status transitions
  - `stop_status_history`             — RouteStop.status transitions (w/ lat/lon)
  - `courier_availability_history`    — Courier.availability_status transitions
  - `dispatch_decision_log`           — per-(run, order) solver decision audit

Also extends `route_stops.delay_cause` (typed enum) for non-failure
delays, and extends `failurereason` with operational categories that the
existing enum was missing.

All additive — no behavioral change for existing data paths until the
service-layer hooks write rows.

Revision ID: 20260513_0038
Revises: 20260513_0037
Create Date: 2026-05-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260513_0038"
down_revision = "20260513_0037"
branch_labels = None
depends_on = None


_NEW_FAILURE_REASONS = (
    "COURIER_RAN_LATE",
    "VEHICLE_BREAKDOWN",
    "WEATHER",
    "SYSTEM_ERROR",
)


def upgrade() -> None:
    # ── Enum extensions ──
    # `failurereason` already exists; ALTER ... ADD VALUE is non-transactional
    # in Postgres, so each value lands in its own statement.
    for value in _NEW_FAILURE_REASONS:
        op.execute(
            f"ALTER TYPE failurereason ADD VALUE IF NOT EXISTS '{value}'"
        )

    # Two brand-new enums.
    stop_delay_cause = PG_ENUM(
        "TRAFFIC",
        "LONG_SERVICE",
        "WAITING_AT_PICKUP",
        "REROUTE",
        "UPSTREAM_DELAY",
        "OTHER",
        name="stopdelaycause",
        create_type=False,
    )
    stop_delay_cause.create(op.get_bind(), checkfirst=True)

    dispatch_decision_kind = PG_ENUM(
        "BATCH_DISPATCH",
        "RANGE_DISPATCH",
        "ON_DEMAND_INSERTION",
        "REOPTIMIZATION",
        name="dispatchdecisionkind",
        create_type=False,
    )
    dispatch_decision_kind.create(op.get_bind(), checkfirst=True)

    # ── route_stops.delay_cause column ──
    op.add_column(
        "route_stops",
        sa.Column(
            "delay_cause",
            PG_ENUM(name="stopdelaycause", create_type=False),
            nullable=True,
        ),
    )

    # ── route_status_history ──
    op.create_table(
        "route_status_history",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, nullable=False,
        ),
        sa.Column(
            "route_id",
            UUID(as_uuid=True),
            sa.ForeignKey("routes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_status",
            PG_ENUM(name="routestatus", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            PG_ENUM(name="routestatus", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_route_status_history_route_id",
        "route_status_history",
        ["route_id"],
    )
    op.create_index(
        "ix_route_status_history_to_status",
        "route_status_history",
        ["to_status"],
    )
    op.create_index(
        "ix_route_status_history_created_at",
        "route_status_history",
        ["created_at"],
    )

    # ── stop_status_history ──
    op.create_table(
        "stop_status_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "stop_id",
            UUID(as_uuid=True),
            sa.ForeignKey("route_stops.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_status",
            PG_ENUM(name="stopstatus", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            PG_ENUM(name="stopstatus", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_stop_status_history_stop_id", "stop_status_history", ["stop_id"]
    )
    op.create_index(
        "ix_stop_status_history_to_status",
        "stop_status_history",
        ["to_status"],
    )
    op.create_index(
        "ix_stop_status_history_created_at",
        "stop_status_history",
        ["created_at"],
    )

    # ── courier_availability_history ──
    op.create_table(
        "courier_availability_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "courier_id",
            UUID(as_uuid=True),
            sa.ForeignKey("couriers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_status",
            PG_ENUM(name="availabilitystatus", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            PG_ENUM(name="availabilitystatus", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_courier_availability_history_courier_id",
        "courier_availability_history",
        ["courier_id"],
    )
    op.create_index(
        "ix_courier_availability_history_to_status",
        "courier_availability_history",
        ["to_status"],
    )
    op.create_index(
        "ix_courier_availability_history_created_at",
        "courier_availability_history",
        ["created_at"],
    )

    # ── dispatch_decision_log ──
    op.create_table(
        "dispatch_decision_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "optimization_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("optimization_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chosen_courier_id",
            UUID(as_uuid=True),
            sa.ForeignKey("couriers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chosen_score", sa.Float(), nullable=True),
        sa.Column(
            "kind",
            PG_ENUM(name="dispatchdecisionkind", create_type=False),
            nullable=False,
        ),
        sa.Column("alternatives", JSONB, nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_dispatch_decision_log_tenant_id",
        "dispatch_decision_log",
        ["tenant_id"],
    )
    op.create_index(
        "ix_dispatch_decision_log_optimization_run_id",
        "dispatch_decision_log",
        ["optimization_run_id"],
    )
    op.create_index(
        "ix_dispatch_decision_log_order_id",
        "dispatch_decision_log",
        ["order_id"],
    )
    op.create_index(
        "ix_dispatch_decision_log_kind",
        "dispatch_decision_log",
        ["kind"],
    )
    op.create_index(
        "ix_dispatch_decision_log_created_at",
        "dispatch_decision_log",
        ["created_at"],
    )
    op.create_index(
        "ix_dispatch_decision_log_run_order",
        "dispatch_decision_log",
        ["optimization_run_id", "order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_dispatch_decision_log_run_order", "dispatch_decision_log")
    op.drop_index("ix_dispatch_decision_log_created_at", "dispatch_decision_log")
    op.drop_index("ix_dispatch_decision_log_kind", "dispatch_decision_log")
    op.drop_index("ix_dispatch_decision_log_order_id", "dispatch_decision_log")
    op.drop_index(
        "ix_dispatch_decision_log_optimization_run_id", "dispatch_decision_log"
    )
    op.drop_index("ix_dispatch_decision_log_tenant_id", "dispatch_decision_log")
    op.drop_table("dispatch_decision_log")

    op.drop_index(
        "ix_courier_availability_history_created_at",
        "courier_availability_history",
    )
    op.drop_index(
        "ix_courier_availability_history_to_status",
        "courier_availability_history",
    )
    op.drop_index(
        "ix_courier_availability_history_courier_id",
        "courier_availability_history",
    )
    op.drop_table("courier_availability_history")

    op.drop_index("ix_stop_status_history_created_at", "stop_status_history")
    op.drop_index("ix_stop_status_history_to_status", "stop_status_history")
    op.drop_index("ix_stop_status_history_stop_id", "stop_status_history")
    op.drop_table("stop_status_history")

    op.drop_index("ix_route_status_history_created_at", "route_status_history")
    op.drop_index("ix_route_status_history_to_status", "route_status_history")
    op.drop_index("ix_route_status_history_route_id", "route_status_history")
    op.drop_table("route_status_history")

    op.drop_column("route_stops", "delay_cause")

    PG_ENUM(name="dispatchdecisionkind").drop(op.get_bind(), checkfirst=True)
    PG_ENUM(name="stopdelaycause").drop(op.get_bind(), checkfirst=True)
    # We don't remove the new failurereason values on downgrade — Postgres
    # doesn't support ALTER TYPE ... DROP VALUE cleanly. Leaving them as
    # additive-only is harmless.
