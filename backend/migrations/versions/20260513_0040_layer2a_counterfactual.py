"""Layer 2a — counterfactual baseline + typed solver-quality metrics.

Adds:
  - `optimization_counterfactuals` table — one row per
    (optimization_run, baseline_kind) holding the greedy baseline cost
    + saved-vs-real deltas. Lets the cost-savings KPI become a
    SUM-over-stored-columns query instead of a re-solve.
  - 7 typed columns on `optimization_runs` for the solver-quality
    fields that live in `metrics` JSONB today (solver_kind,
    iterations, feasibility_ratio, fallback_invoked, decomposition_used,
    warm_start_hit, time_budget_used_s + time_budget_allowed_s).
  - `counterfactualbaselinekind` PG enum.

JSONB `metrics` stays in place for solver-specific extras; the seven
typed columns are the load-bearing ones.

Revision ID: 20260513_0040
Revises: 20260513_0039
Create Date: 2026-05-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID

revision = "20260513_0040"
down_revision = "20260513_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Enum
    baseline_kind = PG_ENUM(
        "GREEDY_NEAREST_NEIGHBOR",
        "HUMAN_DISPATCHER",
        "LAST_WEEK_SAME_DAY",
        name="counterfactualbaselinekind",
        create_type=False,
    )
    baseline_kind.create(op.get_bind(), checkfirst=True)

    # ── Typed solver-metric columns on optimization_runs
    op.add_column(
        "optimization_runs",
        sa.Column("solver_kind", sa.String(40), nullable=True),
    )
    op.add_column(
        "optimization_runs", sa.Column("iterations", sa.Integer(), nullable=True)
    )
    op.add_column(
        "optimization_runs", sa.Column("feasibility_ratio", sa.Float(), nullable=True)
    )
    op.add_column(
        "optimization_runs", sa.Column("fallback_invoked", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "optimization_runs", sa.Column("decomposition_used", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "optimization_runs", sa.Column("warm_start_hit", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "optimization_runs", sa.Column("time_budget_used_s", sa.Float(), nullable=True)
    )
    op.add_column(
        "optimization_runs",
        sa.Column("time_budget_allowed_s", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_optimization_runs_solver_kind",
        "optimization_runs",
        ["solver_kind"],
    )

    # ── optimization_counterfactuals
    op.create_table(
        "optimization_counterfactuals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "optimization_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("optimization_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "baseline_kind",
            PG_ENUM(name="counterfactualbaselinekind", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "baseline_route_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "baseline_distance_m", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "baseline_duration_s", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "baseline_assigned_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "baseline_unassigned_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("baseline_fuel_liters", sa.Float(), nullable=True),
        sa.Column("baseline_fuel_cost_try", sa.Float(), nullable=True),
        sa.Column("baseline_co2_kg", sa.Float(), nullable=True),
        sa.Column("actual_distance_m", sa.Float(), nullable=True),
        sa.Column("actual_fuel_cost_try", sa.Float(), nullable=True),
        sa.Column("actual_co2_kg", sa.Float(), nullable=True),
        sa.Column("distance_saved_m", sa.Float(), nullable=True),
        sa.Column("fuel_cost_saved_try", sa.Float(), nullable=True),
        sa.Column("co2_saved_kg", sa.Float(), nullable=True),
        sa.Column("baseline_elapsed_s", sa.Float(), nullable=True),
        sa.Column("baseline_status", sa.String(40), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "optimization_run_id",
            "baseline_kind",
            name="uq_optimization_counterfactual_run_kind",
        ),
    )
    op.create_index(
        "ix_optimization_counterfactuals_optimization_run_id",
        "optimization_counterfactuals",
        ["optimization_run_id"],
    )
    op.create_index(
        "ix_optimization_counterfactuals_tenant_id",
        "optimization_counterfactuals",
        ["tenant_id"],
    )
    op.create_index(
        "ix_optimization_counterfactuals_captured_at",
        "optimization_counterfactuals",
        ["captured_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_optimization_counterfactuals_captured_at",
        "optimization_counterfactuals",
    )
    op.drop_index(
        "ix_optimization_counterfactuals_tenant_id", "optimization_counterfactuals"
    )
    op.drop_index(
        "ix_optimization_counterfactuals_optimization_run_id",
        "optimization_counterfactuals",
    )
    op.drop_table("optimization_counterfactuals")

    op.drop_index("ix_optimization_runs_solver_kind", "optimization_runs")
    op.drop_column("optimization_runs", "time_budget_allowed_s")
    op.drop_column("optimization_runs", "time_budget_used_s")
    op.drop_column("optimization_runs", "warm_start_hit")
    op.drop_column("optimization_runs", "decomposition_used")
    op.drop_column("optimization_runs", "fallback_invoked")
    op.drop_column("optimization_runs", "feasibility_ratio")
    op.drop_column("optimization_runs", "iterations")
    op.drop_column("optimization_runs", "solver_kind")

    PG_ENUM(name="counterfactualbaselinekind").drop(
        op.get_bind(), checkfirst=True
    )
