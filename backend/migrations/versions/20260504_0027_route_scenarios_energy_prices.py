"""route scenario comparisons and Turkey energy prices

Revision ID: 20260504_0027
Revises: 20260428_0026
Create Date: 2026-05-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260504_0027"
down_revision = "20260428_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("depots", sa.Column("province_code", sa.String(2), nullable=True))
    op.add_column("depots", sa.Column("province_name", sa.String(80), nullable=True))
    op.create_index("ix_depots_province_code", "depots", ["province_code"])
    op.create_index("ix_depots_province_name", "depots", ["province_name"])

    op.create_table(
        "energy_price_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("province_code", sa.String(2), nullable=True),
        sa.Column("province_name", sa.String(80), nullable=True),
        sa.Column("fuel_type", PG_ENUM(name="fueltype", create_type=False), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("price_try", sa.Float(), nullable=False),
        sa.Column("source_name", sa.String(120), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("freshness_status", sa.String(20), nullable=False, server_default="FRESH"),
        sa.Column("raw_payload", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint(
            "country",
            "province_code",
            "fuel_type",
            "effective_date",
            "source_name",
            name="uq_energy_price_snapshot_source_day",
        ),
    )
    op.create_index("ix_energy_price_snapshots_country", "energy_price_snapshots", ["country"])
    op.create_index("ix_energy_price_snapshots_province_code", "energy_price_snapshots", ["province_code"])
    op.create_index("ix_energy_price_snapshots_province_name", "energy_price_snapshots", ["province_name"])
    op.create_index("ix_energy_price_snapshots_fuel_type", "energy_price_snapshots", ["fuel_type"])
    op.create_index("ix_energy_price_snapshots_effective_date", "energy_price_snapshots", ["effective_date"])

    op.create_table(
        "route_scenario_comparisons",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="STARTED"),
        sa.Column("task_id", sa.String(120), nullable=True),
        sa.Column("selected_scenario_id", UUID(as_uuid=True), nullable=True),
        sa.Column("current_policy_snapshot", JSONB, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_route_scenario_comparisons_tenant_id", "route_scenario_comparisons", ["tenant_id"])
    op.create_index("ix_route_scenario_comparisons_target_date", "route_scenario_comparisons", ["target_date"])
    op.create_index("ix_route_scenario_comparisons_status", "route_scenario_comparisons", ["status"])
    op.create_index("ix_route_scenario_comparisons_task_id", "route_scenario_comparisons", ["task_id"])
    op.create_index("ix_route_scenario_comparisons_created_at", "route_scenario_comparisons", ["created_at"])

    op.create_table(
        "route_scenarios",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "comparison_id",
            UUID(as_uuid=True),
            sa.ForeignKey("route_scenario_comparisons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("routing_objective", sa.String(30), nullable=False),
        sa.Column("fleet_utilization", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("optimization_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("summary", JSONB, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("comparison_id", "key", name="uq_route_scenarios_comparison_key"),
    )
    op.create_index("ix_route_scenarios_comparison_id", "route_scenarios", ["comparison_id"])
    op.create_index("ix_route_scenarios_tenant_id", "route_scenarios", ["tenant_id"])
    op.create_index("ix_route_scenarios_status", "route_scenarios", ["status"])
    op.create_index("ix_route_scenarios_optimization_run_id", "route_scenarios", ["optimization_run_id"])


def downgrade() -> None:
    op.drop_index("ix_route_scenarios_optimization_run_id", table_name="route_scenarios")
    op.drop_index("ix_route_scenarios_status", table_name="route_scenarios")
    op.drop_index("ix_route_scenarios_tenant_id", table_name="route_scenarios")
    op.drop_index("ix_route_scenarios_comparison_id", table_name="route_scenarios")
    op.drop_table("route_scenarios")

    op.drop_index("ix_route_scenario_comparisons_created_at", table_name="route_scenario_comparisons")
    op.drop_index("ix_route_scenario_comparisons_task_id", table_name="route_scenario_comparisons")
    op.drop_index("ix_route_scenario_comparisons_status", table_name="route_scenario_comparisons")
    op.drop_index("ix_route_scenario_comparisons_target_date", table_name="route_scenario_comparisons")
    op.drop_index("ix_route_scenario_comparisons_tenant_id", table_name="route_scenario_comparisons")
    op.drop_table("route_scenario_comparisons")

    op.drop_index("ix_energy_price_snapshots_effective_date", table_name="energy_price_snapshots")
    op.drop_index("ix_energy_price_snapshots_fuel_type", table_name="energy_price_snapshots")
    op.drop_index("ix_energy_price_snapshots_province_name", table_name="energy_price_snapshots")
    op.drop_index("ix_energy_price_snapshots_province_code", table_name="energy_price_snapshots")
    op.drop_index("ix_energy_price_snapshots_country", table_name="energy_price_snapshots")
    op.drop_table("energy_price_snapshots")

    op.drop_index("ix_depots_province_name", table_name="depots")
    op.drop_index("ix_depots_province_code", table_name="depots")
    op.drop_column("depots", "province_name")
    op.drop_column("depots", "province_code")
