"""recurring orders + weekly bulk planning

Revision ID: 20260428_0024
Revises: 20260427_0023
Create Date: 2026-04-28

Changes:
  - recurring_order_templates: blueprint rows for recurring orders
  - recurring_order_occurrences: idempotency-fence rows linking templates to
    their generated orders (one row per (template, occurrence_date))
  - optimization_runs.run_type enum: add WEEKLY_BATCH for the new
    "Optimize Week" flow

These changes are additive — no existing rows or columns are touched.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB, UUID

revision = "20260428_0024"
down_revision = "20260427_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # New enum: recurrencecadence
    # ------------------------------------------------------------------
    cadence_enum = PG_ENUM(
        "DAILY",
        "WEEKLY",
        "BIWEEKLY",
        "MONTHLY",
        "CUSTOM",
        name="recurrencecadence",
        create_type=False,
    )
    cadence_enum.create(op.get_bind(), checkfirst=True)

    # Reuse existing ordertype / orderpriority enums (already defined elsewhere)
    ordertype_enum = PG_ENUM(name="ordertype", create_type=False)
    orderpriority_enum = PG_ENUM(name="orderpriority", create_type=False)

    # ------------------------------------------------------------------
    # recurring_order_templates
    # ------------------------------------------------------------------
    op.create_table(
        "recurring_order_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("cadence", cadence_enum, nullable=False),
        sa.Column(
            "interval_days",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "day_of_week_mask",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("max_occurrences", sa.Integer(), nullable=True),
        sa.Column(
            "occurrences_generated",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("delivery_window_start", sa.Time(), nullable=False),
        sa.Column("delivery_window_end", sa.Time(), nullable=False),
        sa.Column("pickup_window_start", sa.Time(), nullable=True),
        sa.Column("pickup_window_end", sa.Time(), nullable=True),
        sa.Column(
            "lead_time_days",
            sa.Integer(),
            nullable=False,
            server_default="7",
        ),
        sa.Column("order_type", ordertype_enum, nullable=False),
        sa.Column(
            "priority",
            orderpriority_enum,
            nullable=False,
            server_default="NORMAL",
        ),
        sa.Column("pickup_address", sa.String(500), nullable=False),
        sa.Column("pickup_lat", sa.Float(), nullable=False),
        sa.Column("pickup_lon", sa.Float(), nullable=False),
        sa.Column("delivery_address", sa.String(500), nullable=False),
        sa.Column("delivery_lat", sa.Float(), nullable=False),
        sa.Column("delivery_lon", sa.Float(), nullable=False),
        sa.Column(
            "depot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("depots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column(
            "is_fragile",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_temp_sensitive",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("length_cm", sa.Float(), nullable=True),
        sa.Column("width_cm", sa.Float(), nullable=True),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("volume_cm3", sa.Float(), nullable=True),
        sa.Column("customer_email", sa.String(255), nullable=False),
        sa.Column("customer_phone", sa.String(50), nullable=True),
        sa.Column("external_ref_prefix", sa.String(120), nullable=True),
        sa.Column("custom_fields", JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_generated_for", sa.Date(), nullable=True),
        sa.Column("next_generation_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_recurring_order_templates_tenant_name",
        ),
    )
    op.create_index(
        "ix_recurring_order_templates_tenant_id",
        "recurring_order_templates",
        ["tenant_id"],
    )
    op.create_index(
        "ix_recurring_order_templates_active_next",
        "recurring_order_templates",
        ["is_active", "next_generation_at"],
    )

    # ------------------------------------------------------------------
    # recurring_order_occurrences (idempotency log)
    # ------------------------------------------------------------------
    op.create_table(
        "recurring_order_occurrences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recurring_order_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "template_id",
            "occurrence_date",
            name="uq_recurring_order_occurrences_template_date",
        ),
    )
    op.create_index(
        "ix_recurring_order_occurrences_template_id",
        "recurring_order_occurrences",
        ["template_id"],
    )
    op.create_index(
        "ix_recurring_order_occurrences_tenant_id",
        "recurring_order_occurrences",
        ["tenant_id"],
    )

    # ------------------------------------------------------------------
    # optimization_runs.run_type — extend enum to include WEEKLY_BATCH
    # ------------------------------------------------------------------
    op.execute("ALTER TYPE optimizationruntype ADD VALUE IF NOT EXISTS 'WEEKLY_BATCH'")


def downgrade() -> None:
    op.drop_index(
        "ix_recurring_order_occurrences_tenant_id",
        table_name="recurring_order_occurrences",
    )
    op.drop_index(
        "ix_recurring_order_occurrences_template_id",
        table_name="recurring_order_occurrences",
    )
    op.drop_table("recurring_order_occurrences")

    op.drop_index(
        "ix_recurring_order_templates_active_next",
        table_name="recurring_order_templates",
    )
    op.drop_index(
        "ix_recurring_order_templates_tenant_id",
        table_name="recurring_order_templates",
    )
    op.drop_table("recurring_order_templates")

    cadence_enum = PG_ENUM(name="recurrencecadence", create_type=False)
    cadence_enum.drop(op.get_bind(), checkfirst=True)

    # Postgres can't drop a single enum value cleanly; leave WEEKLY_BATCH in place.
