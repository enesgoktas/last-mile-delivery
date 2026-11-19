"""schema parity indexes constraints and foreign-key delete behavior

Revision ID: 20260224_0007
Revises: 20260224_0006
Create Date: 2026-02-24 14:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260224_0007"
down_revision = "20260224_0006"
branch_labels = None
depends_on = None


def _constraint_exists(name: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_constraint WHERE conname = :name LIMIT 1"),
        {"name": name},
    ).first()
    return row is not None


def _index_exists(name: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'i'
              AND c.relname = :name
              AND n.nspname = current_schema()
            LIMIT 1
            """
        ),
        {"name": name},
    ).first()
    return row is not None


def _drop_fk_for_columns(table_name: str, columns: Sequence[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    target = list(columns)
    for fk in inspector.get_foreign_keys(table_name):
        if fk.get("constrained_columns") == target:
            op.drop_constraint(fk["name"], table_name, type_="foreignkey")
            return


def _recreate_fk(
    *,
    table_name: str,
    constrained_columns: Sequence[str],
    referred_table: str,
    referred_columns: Sequence[str],
    name: str,
    ondelete: str | None = None,
) -> None:
    _drop_fk_for_columns(table_name, constrained_columns)
    op.create_foreign_key(
        name,
        table_name,
        referred_table,
        list(constrained_columns),
        list(referred_columns),
        ondelete=ondelete,
    )


def upgrade() -> None:
    if not _constraint_exists("ck_orders_attempt_count_non_negative"):
        op.create_check_constraint(
            "ck_orders_attempt_count_non_negative",
            "orders",
            "attempt_count >= 0",
        )
    if not _constraint_exists("ck_orders_max_attempts_positive"):
        op.create_check_constraint(
            "ck_orders_max_attempts_positive",
            "orders",
            "max_attempts >= 1",
        )
    if not _constraint_exists("ck_orders_attempt_count_lte_max_attempts"):
        op.create_check_constraint(
            "ck_orders_attempt_count_lte_max_attempts",
            "orders",
            "attempt_count <= max_attempts",
        )
    if not _constraint_exists("ck_orders_delivery_window_valid"):
        op.create_check_constraint(
            "ck_orders_delivery_window_valid",
            "orders",
            "earliest_delivery <= latest_delivery",
        )
    if not _constraint_exists("ck_orders_pickup_window_valid"):
        op.create_check_constraint(
            "ck_orders_pickup_window_valid",
            "orders",
            "(earliest_pickup IS NULL OR latest_pickup IS NULL OR earliest_pickup <= latest_pickup)",
        )

    if not _index_exists("uq_orders_external_ref_not_null"):
        op.create_index(
            "uq_orders_external_ref_not_null",
            "orders",
            ["external_ref"],
            unique=True,
            postgresql_where=sa.text("external_ref IS NOT NULL"),
        )
    if not _index_exists("uq_proof_of_delivery_order_id"):
        op.create_index(
            "uq_proof_of_delivery_order_id",
            "proof_of_delivery",
            ["order_id"],
            unique=True,
        )
    if not _index_exists("uq_proof_of_delivery_route_stop_id"):
        op.create_index(
            "uq_proof_of_delivery_route_stop_id",
            "proof_of_delivery",
            ["route_stop_id"],
            unique=True,
        )
    if not _index_exists("uq_webhook_endpoints_client_url"):
        op.create_index(
            "uq_webhook_endpoints_client_url",
            "webhook_endpoints",
            ["client_id", "url"],
            unique=True,
        )

    if not _index_exists("ix_orders_assigned_courier_status_created_at"):
        op.create_index(
            "ix_orders_assigned_courier_status_created_at",
            "orders",
            ["assigned_courier_id", "status", "created_at"],
        )
    if not _index_exists("ix_routes_courier_status_created_at"):
        op.create_index(
            "ix_routes_courier_status_created_at",
            "routes",
            ["courier_id", "status", "created_at"],
        )
    if not _index_exists("ix_route_stops_order_id"):
        op.create_index("ix_route_stops_order_id", "route_stops", ["order_id"])
    if not _index_exists("ix_webhook_delivery_log_endpoint_sent_at"):
        op.create_index(
            "ix_webhook_delivery_log_endpoint_sent_at",
            "webhook_delivery_log",
            ["webhook_endpoint_id", "sent_at"],
        )
    if not _index_exists("ix_notifications_order_event_created_at"):
        op.create_index(
            "ix_notifications_order_event_created_at",
            "notifications",
            ["order_id", "event_type", "created_at"],
        )
    if not _index_exists("ix_import_jobs_created_at"):
        op.create_index("ix_import_jobs_created_at", "import_jobs", ["created_at"])
    if not _index_exists("ix_location_snapshots_courier_recorded_desc"):
        op.execute(
            """
            CREATE INDEX ix_location_snapshots_courier_recorded_desc
            ON location_snapshots (courier_id, recorded_at DESC)
            """
        )

    _recreate_fk(
        table_name="orders",
        constrained_columns=["assigned_courier_id"],
        referred_table="couriers",
        referred_columns=["id"],
        name="fk_orders_assigned_courier_id",
        ondelete="SET NULL",
    )
    _recreate_fk(
        table_name="orders",
        constrained_columns=["route_id"],
        referred_table="routes",
        referred_columns=["id"],
        name="fk_orders_route_id",
        ondelete="SET NULL",
    )
    _recreate_fk(
        table_name="route_stops",
        constrained_columns=["route_id"],
        referred_table="routes",
        referred_columns=["id"],
        name="fk_route_stops_route_id",
        ondelete="CASCADE",
    )
    _recreate_fk(
        table_name="location_snapshots",
        constrained_columns=["courier_id"],
        referred_table="couriers",
        referred_columns=["id"],
        name="fk_location_snapshots_courier_id",
        ondelete="CASCADE",
    )
    _recreate_fk(
        table_name="order_status_history",
        constrained_columns=["order_id"],
        referred_table="orders",
        referred_columns=["id"],
        name="fk_order_status_history_order_id",
        ondelete="CASCADE",
    )
    _recreate_fk(
        table_name="order_status_history",
        constrained_columns=["actor_user_id"],
        referred_table="users",
        referred_columns=["id"],
        name="fk_order_status_history_actor_user_id",
        ondelete="SET NULL",
    )
    _recreate_fk(
        table_name="proof_of_delivery",
        constrained_columns=["order_id"],
        referred_table="orders",
        referred_columns=["id"],
        name="fk_proof_of_delivery_order_id",
        ondelete="CASCADE",
    )
    _recreate_fk(
        table_name="proof_of_delivery",
        constrained_columns=["route_stop_id"],
        referred_table="route_stops",
        referred_columns=["id"],
        name="fk_proof_of_delivery_route_stop_id",
        ondelete="CASCADE",
    )
    _recreate_fk(
        table_name="webhook_endpoints",
        constrained_columns=["client_id"],
        referred_table="api_clients",
        referred_columns=["id"],
        name="fk_webhook_endpoints_client_id",
        ondelete="CASCADE",
    )
    _recreate_fk(
        table_name="webhook_delivery_log",
        constrained_columns=["webhook_endpoint_id"],
        referred_table="webhook_endpoints",
        referred_columns=["id"],
        name="fk_webhook_delivery_log_endpoint_id",
        ondelete="CASCADE",
    )
    _recreate_fk(
        table_name="notifications",
        constrained_columns=["order_id"],
        referred_table="orders",
        referred_columns=["id"],
        name="fk_notifications_order_id",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    _recreate_fk(
        table_name="notifications",
        constrained_columns=["order_id"],
        referred_table="orders",
        referred_columns=["id"],
        name="fk_notifications_order_id",
    )
    _recreate_fk(
        table_name="webhook_delivery_log",
        constrained_columns=["webhook_endpoint_id"],
        referred_table="webhook_endpoints",
        referred_columns=["id"],
        name="fk_webhook_delivery_log_endpoint_id",
    )
    _recreate_fk(
        table_name="webhook_endpoints",
        constrained_columns=["client_id"],
        referred_table="api_clients",
        referred_columns=["id"],
        name="fk_webhook_endpoints_client_id",
    )
    _recreate_fk(
        table_name="proof_of_delivery",
        constrained_columns=["route_stop_id"],
        referred_table="route_stops",
        referred_columns=["id"],
        name="fk_proof_of_delivery_route_stop_id",
    )
    _recreate_fk(
        table_name="proof_of_delivery",
        constrained_columns=["order_id"],
        referred_table="orders",
        referred_columns=["id"],
        name="fk_proof_of_delivery_order_id",
    )
    _recreate_fk(
        table_name="order_status_history",
        constrained_columns=["actor_user_id"],
        referred_table="users",
        referred_columns=["id"],
        name="fk_order_status_history_actor_user_id",
    )
    _recreate_fk(
        table_name="order_status_history",
        constrained_columns=["order_id"],
        referred_table="orders",
        referred_columns=["id"],
        name="fk_order_status_history_order_id",
    )
    _recreate_fk(
        table_name="location_snapshots",
        constrained_columns=["courier_id"],
        referred_table="couriers",
        referred_columns=["id"],
        name="fk_location_snapshots_courier_id",
    )
    _recreate_fk(
        table_name="route_stops",
        constrained_columns=["route_id"],
        referred_table="routes",
        referred_columns=["id"],
        name="fk_route_stops_route_id",
    )
    _recreate_fk(
        table_name="orders",
        constrained_columns=["route_id"],
        referred_table="routes",
        referred_columns=["id"],
        name="fk_orders_route_id",
    )
    _recreate_fk(
        table_name="orders",
        constrained_columns=["assigned_courier_id"],
        referred_table="couriers",
        referred_columns=["id"],
        name="fk_orders_assigned_courier_id",
    )

    if _index_exists("ix_location_snapshots_courier_recorded_desc"):
        op.drop_index(
            "ix_location_snapshots_courier_recorded_desc",
            table_name="location_snapshots",
        )
    if _index_exists("ix_import_jobs_created_at"):
        op.drop_index("ix_import_jobs_created_at", table_name="import_jobs")
    if _index_exists("ix_notifications_order_event_created_at"):
        op.drop_index(
            "ix_notifications_order_event_created_at",
            table_name="notifications",
        )
    if _index_exists("ix_webhook_delivery_log_endpoint_sent_at"):
        op.drop_index(
            "ix_webhook_delivery_log_endpoint_sent_at",
            table_name="webhook_delivery_log",
        )
    if _index_exists("ix_route_stops_order_id"):
        op.drop_index("ix_route_stops_order_id", table_name="route_stops")
    if _index_exists("ix_routes_courier_status_created_at"):
        op.drop_index("ix_routes_courier_status_created_at", table_name="routes")
    if _index_exists("ix_orders_assigned_courier_status_created_at"):
        op.drop_index(
            "ix_orders_assigned_courier_status_created_at",
            table_name="orders",
        )
    if _index_exists("uq_webhook_endpoints_client_url"):
        op.drop_index("uq_webhook_endpoints_client_url", table_name="webhook_endpoints")
    if _index_exists("uq_proof_of_delivery_route_stop_id"):
        op.drop_index("uq_proof_of_delivery_route_stop_id", table_name="proof_of_delivery")
    if _index_exists("uq_proof_of_delivery_order_id"):
        op.drop_index("uq_proof_of_delivery_order_id", table_name="proof_of_delivery")
    if _index_exists("uq_orders_external_ref_not_null"):
        op.drop_index("uq_orders_external_ref_not_null", table_name="orders")

    if _constraint_exists("ck_orders_pickup_window_valid"):
        op.drop_constraint("ck_orders_pickup_window_valid", "orders", type_="check")
    if _constraint_exists("ck_orders_delivery_window_valid"):
        op.drop_constraint("ck_orders_delivery_window_valid", "orders", type_="check")
    if _constraint_exists("ck_orders_attempt_count_lte_max_attempts"):
        op.drop_constraint(
            "ck_orders_attempt_count_lte_max_attempts",
            "orders",
            type_="check",
        )
    if _constraint_exists("ck_orders_max_attempts_positive"):
        op.drop_constraint("ck_orders_max_attempts_positive", "orders", type_="check")
    if _constraint_exists("ck_orders_attempt_count_non_negative"):
        op.drop_constraint(
            "ck_orders_attempt_count_non_negative",
            "orders",
            type_="check",
        )
