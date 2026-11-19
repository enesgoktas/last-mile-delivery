"""Layer 2b — replay journal + ETA prediction log + plan stability.

Three additive tables that close the remaining pilot-data gaps for the
optimization feedback loop:

  - optimization_run_replays   — full VRPInput per run so any historical
    problem can be re-solved with a future solver version.
  - eta_prediction_log         — every ETA we surface, paired against
    the actual arrival once the stop completes. Builds the ETA-accuracy
    dataset.
  - plan_stability_scores      — for re-runs over overlapping ranges,
    the fraction of orders that kept their (courier, day) tuple.

Revision ID: 20260513_0041
Revises: 20260513_0040
Create Date: 2026-05-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260513_0041"
down_revision = "20260513_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── eta_predicted_by enum
    eta_predicted_by = PG_ENUM(
        "PLANNER",
        "IN_FLIGHT_ETA",
        "REROUTING",
        name="etapredictedby",
        create_type=False,
    )
    eta_predicted_by.create(op.get_bind(), checkfirst=True)

    # ── optimization_run_replays
    op.create_table(
        "optimization_run_replays",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "optimization_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("optimization_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("adapter_version", sa.String(80), nullable=False),
        sa.Column("random_seed", sa.Integer(), nullable=True),
        sa.Column("time_budget_allowed_s", sa.Float(), nullable=True),
        sa.Column("time_budget_used_s", sa.Float(), nullable=True),
        sa.Column("vrp_input_payload", JSONB, nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_optimization_run_replays_tenant_id",
        "optimization_run_replays",
        ["tenant_id"],
    )
    op.create_index(
        "ix_optimization_run_replays_captured_at",
        "optimization_run_replays",
        ["captured_at"],
    )

    # ── eta_prediction_log
    op.create_table(
        "eta_prediction_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "stop_id",
            UUID(as_uuid=True),
            sa.ForeignKey("route_stops.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "optimization_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("optimization_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("predicted_at", sa.DateTime(), nullable=False),
        sa.Column("predicted_arrival", sa.DateTime(), nullable=False),
        sa.Column("actual_arrival", sa.DateTime(), nullable=True),
        sa.Column(
            "predicted_by",
            PG_ENUM(name="etapredictedby", create_type=False),
            nullable=False,
        ),
        sa.Column("prediction_horizon_s", sa.Integer(), nullable=True),
        sa.Column("error_s", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_eta_prediction_log_tenant_id", "eta_prediction_log", ["tenant_id"]
    )
    op.create_index(
        "ix_eta_prediction_log_stop_id", "eta_prediction_log", ["stop_id"]
    )
    op.create_index(
        "ix_eta_prediction_log_predicted_at",
        "eta_prediction_log",
        ["predicted_at"],
    )
    op.create_index(
        "ix_eta_prediction_log_predicted_by",
        "eta_prediction_log",
        ["predicted_by"],
    )
    op.create_index(
        "ix_eta_prediction_log_created_at",
        "eta_prediction_log",
        ["created_at"],
    )
    op.create_index(
        "ix_eta_prediction_log_stop_predicted_by",
        "eta_prediction_log",
        ["stop_id", "predicted_by"],
    )

    # ── plan_stability_scores
    op.create_table(
        "plan_stability_scores",
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
            sa.ForeignKey("optimization_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prior_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("optimization_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "orders_evaluated", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "orders_unchanged", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "orders_same_courier", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "orders_same_day", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("stability_pct", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_plan_stability_scores_tenant_id",
        "plan_stability_scores",
        ["tenant_id"],
    )
    op.create_index(
        "ix_plan_stability_scores_optimization_run_id",
        "plan_stability_scores",
        ["optimization_run_id"],
    )
    op.create_index(
        "ix_plan_stability_scores_created_at",
        "plan_stability_scores",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_plan_stability_scores_created_at", "plan_stability_scores"
    )
    op.drop_index(
        "ix_plan_stability_scores_optimization_run_id", "plan_stability_scores"
    )
    op.drop_index("ix_plan_stability_scores_tenant_id", "plan_stability_scores")
    op.drop_table("plan_stability_scores")

    op.drop_index("ix_eta_prediction_log_stop_predicted_by", "eta_prediction_log")
    op.drop_index("ix_eta_prediction_log_created_at", "eta_prediction_log")
    op.drop_index("ix_eta_prediction_log_predicted_by", "eta_prediction_log")
    op.drop_index("ix_eta_prediction_log_predicted_at", "eta_prediction_log")
    op.drop_index("ix_eta_prediction_log_stop_id", "eta_prediction_log")
    op.drop_index("ix_eta_prediction_log_tenant_id", "eta_prediction_log")
    op.drop_table("eta_prediction_log")

    op.drop_index(
        "ix_optimization_run_replays_captured_at", "optimization_run_replays"
    )
    op.drop_index(
        "ix_optimization_run_replays_tenant_id", "optimization_run_replays"
    )
    op.drop_table("optimization_run_replays")

    PG_ENUM(name="etapredictedby").drop(op.get_bind(), checkfirst=True)
