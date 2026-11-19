"""Layer 3 — compounders + product moats.

Adds the schema for the pilot's strategic data products:

  - empirical_service_times    — per-(courier, address) median service
    duration. Compounds in value — the solver gradually uses real
    numbers instead of static estimates.
  - driver_events              — typed shift/break/HoS event log on top
    of the courier_availability_history Layer 0 already provides.
  - order_ratings              — 1–5 customer satisfaction rating.
    Endpoint + table now; SMS flow deferred.
  - demand_observations        — hourly (geohash × hour × day) order
    counts. Feed for a future demand-forecasting model.
  - orders.failure_cause_category + failure_cause_subcategory
    — typed exception taxonomy. The flat FailureReason enum stays
    for stop-level UX; this pair powers trend dashboards.

Revision ID: 20260513_0042
Revises: 20260513_0041
Create Date: 2026-05-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260513_0042"
down_revision = "20260513_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Enums ──
    failure_cause_category = PG_ENUM(
        "CUSTOMER",
        "ADDRESS",
        "COURIER",
        "VEHICLE",
        "WEATHER",
        "SYSTEM",
        "OTHER",
        name="failurecausecategory",
        create_type=False,
    )
    failure_cause_category.create(op.get_bind(), checkfirst=True)

    driver_event_type = PG_ENUM(
        "SHIFT_START",
        "SHIFT_END",
        "BREAK_START",
        "BREAK_END",
        "IDLE_DETECTED",
        "REROUTE_RECEIVED",
        "ORDER_REJECTED",
        name="drivereventtype",
        create_type=False,
    )
    driver_event_type.create(op.get_bind(), checkfirst=True)

    # ── orders.failure_cause_category + subcategory ──
    op.add_column(
        "orders",
        sa.Column(
            "failure_cause_category",
            PG_ENUM(name="failurecausecategory", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "orders",
        sa.Column("failure_cause_subcategory", sa.String(80), nullable=True),
    )
    op.create_index(
        "ix_orders_failure_cause_category",
        "orders",
        ["failure_cause_category"],
    )

    # ── empirical_service_times ──
    op.create_table(
        "empirical_service_times",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "courier_id",
            UUID(as_uuid=True),
            sa.ForeignKey("couriers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("address_hash", sa.String(16), nullable=False),
        sa.Column("service_duration_s", sa.Float(), nullable=False),
        sa.Column(
            "sample_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("confidence_band_s", sa.Float(), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "courier_id",
            "address_hash",
            name="uq_empirical_service_times_tcp",
        ),
    )
    op.create_index(
        "ix_empirical_service_times_tenant_id",
        "empirical_service_times",
        ["tenant_id"],
    )
    op.create_index(
        "ix_empirical_service_times_courier_id",
        "empirical_service_times",
        ["courier_id"],
    )
    op.create_index(
        "ix_empirical_service_times_address_hash",
        "empirical_service_times",
        ["address_hash"],
    )

    # ── driver_events ──
    op.create_table(
        "driver_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "courier_id",
            UUID(as_uuid=True),
            sa.ForeignKey("couriers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            PG_ENUM(name="drivereventtype", create_type=False),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("metadata_payload", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_driver_events_tenant_id", "driver_events", ["tenant_id"])
    op.create_index("ix_driver_events_courier_id", "driver_events", ["courier_id"])
    op.create_index("ix_driver_events_event_type", "driver_events", ["event_type"])
    op.create_index("ix_driver_events_occurred_at", "driver_events", ["occurred_at"])
    op.create_index("ix_driver_events_created_at", "driver_events", ["created_at"])

    # ── order_ratings ──
    op.create_table(
        "order_ratings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("feedback_text", sa.Text(), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_order_ratings_tenant_id", "order_ratings", ["tenant_id"])
    op.create_index(
        "ix_order_ratings_captured_at", "order_ratings", ["captured_at"]
    )

    # ── demand_observations ──
    op.create_table(
        "demand_observations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("geohash_6", sa.String(8), nullable=False),
        sa.Column("hour_bucket", sa.Integer(), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "weighted_demand", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "observation_date",
            "geohash_6",
            "hour_bucket",
            name="uq_demand_observations_zone_hour",
        ),
    )
    op.create_index(
        "ix_demand_observations_tenant_id", "demand_observations", ["tenant_id"]
    )
    op.create_index(
        "ix_demand_observations_observation_date",
        "demand_observations",
        ["observation_date"],
    )
    op.create_index(
        "ix_demand_observations_geohash_6", "demand_observations", ["geohash_6"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_demand_observations_geohash_6", "demand_observations"
    )
    op.drop_index(
        "ix_demand_observations_observation_date", "demand_observations"
    )
    op.drop_index("ix_demand_observations_tenant_id", "demand_observations")
    op.drop_table("demand_observations")

    op.drop_index("ix_order_ratings_captured_at", "order_ratings")
    op.drop_index("ix_order_ratings_tenant_id", "order_ratings")
    op.drop_table("order_ratings")

    op.drop_index("ix_driver_events_created_at", "driver_events")
    op.drop_index("ix_driver_events_occurred_at", "driver_events")
    op.drop_index("ix_driver_events_event_type", "driver_events")
    op.drop_index("ix_driver_events_courier_id", "driver_events")
    op.drop_index("ix_driver_events_tenant_id", "driver_events")
    op.drop_table("driver_events")

    op.drop_index(
        "ix_empirical_service_times_address_hash", "empirical_service_times"
    )
    op.drop_index(
        "ix_empirical_service_times_courier_id", "empirical_service_times"
    )
    op.drop_index(
        "ix_empirical_service_times_tenant_id", "empirical_service_times"
    )
    op.drop_table("empirical_service_times")

    op.drop_index("ix_orders_failure_cause_category", "orders")
    op.drop_column("orders", "failure_cause_subcategory")
    op.drop_column("orders", "failure_cause_category")

    PG_ENUM(name="drivereventtype").drop(op.get_bind(), checkfirst=True)
    PG_ENUM(name="failurecausecategory").drop(op.get_bind(), checkfirst=True)
