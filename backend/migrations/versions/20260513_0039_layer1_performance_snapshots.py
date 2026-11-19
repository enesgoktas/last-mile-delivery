"""Layer 1 — materialize planned-vs-actual + cost rollups.

Three tables that move the dashboard's hot-path aggregations from
"re-scan every order row on each 60s poll" to "select N daily rows":

  - route_performance_snapshots   — one row per completed Route
  - daily_tenant_kpi              — one row per (tenant_id, operational_date)
  - operations_policy_snapshots   — weekly typed snapshot of policy JSONB

All three are additive. Layer 0's `dispatch_decision_log` joins to
`optimization_runs` and `route_performance_snapshots` joins to `routes`
via the existing FKs — no schema changes to those tables.

Revision ID: 20260513_0039
Revises: 20260513_0038
Create Date: 2026-05-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260513_0039"
down_revision = "20260513_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── route_performance_snapshots ──
    op.create_table(
        "route_performance_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "route_id",
            UUID(as_uuid=True),
            sa.ForeignKey("routes.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "courier_id",
            UUID(as_uuid=True),
            sa.ForeignKey("couriers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "optimization_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("optimization_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("operational_date", sa.Date(), nullable=False),
        sa.Column("planned_distance_m", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "planned_duration_s", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "planned_stop_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("planned_capacity_used_pct", sa.Float(), nullable=True),
        sa.Column("actual_distance_m", sa.Float(), nullable=True),
        sa.Column("actual_duration_s", sa.Integer(), nullable=True),
        sa.Column("actual_stop_count", sa.Integer(), nullable=True),
        sa.Column("delta_arrival_p50_s", sa.Integer(), nullable=True),
        sa.Column("delta_arrival_p95_s", sa.Integer(), nullable=True),
        sa.Column("delta_arrival_max_s", sa.Integer(), nullable=True),
        sa.Column("actual_fuel_liters", sa.Float(), nullable=True),
        sa.Column("fuel_unit_price_try", sa.Float(), nullable=True),
        sa.Column("actual_fuel_cost_try", sa.Float(), nullable=True),
        sa.Column("actual_distance_cost_try", sa.Float(), nullable=True),
        sa.Column("total_route_cost_try", sa.Float(), nullable=True),
        sa.Column("co2_kg", sa.Float(), nullable=True),
        sa.Column("baseline_fuel_cost_try", sa.Float(), nullable=True),
        sa.Column("baseline_distance_m", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index(
        "ix_route_performance_snapshots_tenant_id",
        "route_performance_snapshots",
        ["tenant_id"],
    )
    op.create_index(
        "ix_route_performance_snapshots_courier_id",
        "route_performance_snapshots",
        ["courier_id"],
    )
    op.create_index(
        "ix_route_performance_snapshots_optimization_run_id",
        "route_performance_snapshots",
        ["optimization_run_id"],
    )
    op.create_index(
        "ix_route_performance_snapshots_operational_date",
        "route_performance_snapshots",
        ["operational_date"],
    )
    op.create_index(
        "ix_route_performance_snapshots_created_at",
        "route_performance_snapshots",
        ["created_at"],
    )
    op.create_index(
        "ix_route_performance_snapshots_tenant_date",
        "route_performance_snapshots",
        ["tenant_id", "operational_date"],
    )

    # ── daily_tenant_kpi ──
    op.create_table(
        "daily_tenant_kpi",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operational_date", sa.Date(), nullable=False),
        sa.Column("orders_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders_delivered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders_pending", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders_assigned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "orders_in_transit", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("orders_cancelled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "orders_unassigned", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("on_time_rate", sa.Float(), nullable=True),
        sa.Column("first_attempt_success_rate", sa.Float(), nullable=True),
        sa.Column("avg_route_utilization_pct", sa.Float(), nullable=True),
        sa.Column("total_routes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_distance_m", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_duration_s", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "total_fuel_cost_try", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column("total_fuel_liters", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_co2_kg", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cost_per_delivery_try", sa.Float(), nullable=True),
        sa.Column("cost_per_km_try", sa.Float(), nullable=True),
        sa.Column("baseline_fuel_cost_try", sa.Float(), nullable=True),
        sa.Column("baseline_co2_kg", sa.Float(), nullable=True),
        sa.Column("baseline_distance_m", sa.Float(), nullable=True),
        sa.Column("fuel_cost_saved_try", sa.Float(), nullable=True),
        sa.Column("co2_saved_kg", sa.Float(), nullable=True),
        sa.Column("distance_saved_m", sa.Float(), nullable=True),
        sa.Column(
            "optimization_runs", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("avg_solver_quality_score", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.UniqueConstraint(
            "tenant_id", "operational_date", name="uq_daily_tenant_kpi_tenant_date"
        ),
    )
    op.create_index(
        "ix_daily_tenant_kpi_tenant_id", "daily_tenant_kpi", ["tenant_id"]
    )
    op.create_index(
        "ix_daily_tenant_kpi_operational_date",
        "daily_tenant_kpi",
        ["operational_date"],
    )

    # ── operations_policy_snapshots ──
    op.create_table(
        "operations_policy_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("multi_depot_vehicles_enabled", sa.Boolean(), nullable=True),
        sa.Column("rides_enabled", sa.Boolean(), nullable=True),
        sa.Column("ev_auto_charging_enabled", sa.Boolean(), nullable=True),
        sa.Column("economic_batch_optimizer_enabled", sa.Boolean(), nullable=True),
        sa.Column("manual_route_approval_required", sa.Boolean(), nullable=True),
        sa.Column("on_time_target_pct", sa.Float(), nullable=True),
        sa.Column("sla_window_minutes", sa.Integer(), nullable=True),
        sa.Column("raw_policy", JSONB, nullable=True),
        sa.Column("raw_global_constraints", JSONB, nullable=True),
        sa.Column(
            "captured_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "snapshot_date",
            name="uq_operations_policy_snapshots_tenant_date",
        ),
    )
    op.create_index(
        "ix_operations_policy_snapshots_tenant_id",
        "operations_policy_snapshots",
        ["tenant_id"],
    )
    op.create_index(
        "ix_operations_policy_snapshots_snapshot_date",
        "operations_policy_snapshots",
        ["snapshot_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operations_policy_snapshots_snapshot_date",
        "operations_policy_snapshots",
    )
    op.drop_index(
        "ix_operations_policy_snapshots_tenant_id", "operations_policy_snapshots"
    )
    op.drop_table("operations_policy_snapshots")

    op.drop_index("ix_daily_tenant_kpi_operational_date", "daily_tenant_kpi")
    op.drop_index("ix_daily_tenant_kpi_tenant_id", "daily_tenant_kpi")
    op.drop_table("daily_tenant_kpi")

    op.drop_index(
        "ix_route_performance_snapshots_tenant_date",
        "route_performance_snapshots",
    )
    op.drop_index(
        "ix_route_performance_snapshots_created_at", "route_performance_snapshots"
    )
    op.drop_index(
        "ix_route_performance_snapshots_operational_date",
        "route_performance_snapshots",
    )
    op.drop_index(
        "ix_route_performance_snapshots_optimization_run_id",
        "route_performance_snapshots",
    )
    op.drop_index(
        "ix_route_performance_snapshots_courier_id", "route_performance_snapshots"
    )
    op.drop_index(
        "ix_route_performance_snapshots_tenant_id", "route_performance_snapshots"
    )
    op.drop_table("route_performance_snapshots")
