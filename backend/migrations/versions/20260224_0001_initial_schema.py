"""initial schema

Revision ID: 20260224_0001
Revises:
Create Date: 2026-02-24 00:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260224_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_UPGRADE_SQL: tuple[str, ...] = (
    "CREATE TYPE userrole AS ENUM ('ADMIN', 'DISPATCHER', 'COURIER')",
    "CREATE TYPE vehicletype AS ENUM ('MOTORCYCLE', 'CAR_VAN', 'TRUCK')",
    "CREATE TYPE availabilitystatus AS ENUM ('ONLINE', 'OFFLINE', 'ON_BREAK')",
    "CREATE TYPE ordertype AS ENUM ('ON_DEMAND', 'SCHEDULED', 'BULK', 'REVERSE')",
    "CREATE TYPE orderstatus AS ENUM ('DRAFT', 'PENDING', 'ASSIGNED', 'PICKED_UP', 'IN_TRANSIT', 'DELIVERED', 'FAILED', 'RESCHEDULED', 'CANCELLED')",
    "CREATE TYPE orderpriority AS ENUM ('LOW', 'NORMAL', 'HIGH', 'URGENT')",
    "CREATE TYPE failedordercustomerresponsestatus AS ENUM ('PENDING', 'RESPONDED', 'ESCALATED')",
    "CREATE TYPE routestatus AS ENUM ('PLANNED', 'ACTIVE', 'COMPLETED', 'ABORTED')",
    "CREATE TYPE stoptype AS ENUM ('PICKUP', 'DELIVERY')",
    "CREATE TYPE stopstatus AS ENUM ('PENDING', 'ARRIVED', 'COMPLETED', 'FAILED')",
    "CREATE TYPE failurereason AS ENUM ('NOT_HOME', 'WRONG_ADDRESS', 'REFUSED', 'DAMAGED', 'ACCESS_DENIED', 'OTHER')",
    "CREATE TYPE notificationchannel AS ENUM ('EMAIL', 'SMS', 'PUSH')",
    "CREATE TYPE notificationstatus AS ENUM ('PENDING', 'SENT', 'FAILED', 'SUPPRESSED')",
    "CREATE TYPE webhookdeliverystatus AS ENUM ('SUCCESS', 'RETRY_SCHEDULED', 'DEAD_LETTER')",
    "CREATE TYPE importjobstatus AS ENUM ('PREVIEW_READY', 'PUBLISHED', 'FAILED')",
    "CREATE TYPE optimizationruntype AS ENUM ('BATCH', 'INSERTION', 'REOPTIMIZATION')",
    "CREATE TYPE optimizationrunstatus AS ENUM ('STARTED', 'SUCCEEDED', 'FAILED', 'LOCKED', 'NO_OP')",
    """
    CREATE TABLE users (
        id UUID NOT NULL,
        email VARCHAR(255) NOT NULL,
        hashed_password VARCHAR(255) NOT NULL,
        role userrole NOT NULL,
        is_active BOOLEAN NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        PRIMARY KEY (id)
    )
    """,
    "CREATE UNIQUE INDEX ix_users_email ON users (email)",
    """
    CREATE TABLE api_clients (
        id UUID NOT NULL,
        name VARCHAR(255) NOT NULL,
        api_key_hash VARCHAR(255) NOT NULL,
        api_key_prefix VARCHAR(16),
        permissions VARCHAR[] NOT NULL,
        rate_limit_per_min INTEGER NOT NULL,
        is_active BOOLEAN NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        PRIMARY KEY (id)
    )
    """,
    "CREATE INDEX ix_api_clients_api_key_prefix ON api_clients (api_key_prefix)",
    """
    CREATE TABLE optimization_runs (
        id UUID NOT NULL,
        task_id VARCHAR(120),
        run_type optimizationruntype NOT NULL,
        status optimizationrunstatus NOT NULL,
        routes_created INTEGER,
        unassigned_count INTEGER,
        solver_status VARCHAR(80),
        metrics JSONB,
        error_message TEXT,
        started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        finished_at TIMESTAMP WITHOUT TIME ZONE,
        PRIMARY KEY (id)
    )
    """,
    "CREATE INDEX ix_optimization_runs_task_id ON optimization_runs (task_id)",
    "CREATE INDEX ix_optimization_runs_started_at ON optimization_runs (started_at)",
    "CREATE INDEX ix_optimization_runs_status ON optimization_runs (status)",
    """
    CREATE TABLE couriers (
        id UUID NOT NULL,
        user_id UUID NOT NULL,
        vehicle_type vehicletype NOT NULL,
        max_weight_kg FLOAT NOT NULL,
        availability_status availabilitystatus NOT NULL,
        zone_polygon JSON,
        depot_lat FLOAT NOT NULL,
        depot_lon FLOAT NOT NULL,
        working_hours_start TIME WITHOUT TIME ZONE NOT NULL,
        working_hours_end TIME WITHOUT TIME ZONE NOT NULL,
        fcm_token VARCHAR(255),
        PRIMARY KEY (id),
        UNIQUE (user_id),
        FOREIGN KEY(user_id) REFERENCES users (id)
    )
    """,
    """
    CREATE TABLE webhook_endpoints (
        id UUID NOT NULL,
        client_id UUID NOT NULL,
        url VARCHAR(1000) NOT NULL,
        secret VARCHAR(255) NOT NULL,
        events VARCHAR[] NOT NULL,
        is_active BOOLEAN NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(client_id) REFERENCES api_clients (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE audit_logs (
        id UUID NOT NULL,
        actor_user_id UUID,
        action VARCHAR(120) NOT NULL,
        resource_type VARCHAR(80),
        resource_id VARCHAR(120),
        details JSONB,
        ip_address VARCHAR(64),
        user_agent TEXT,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(actor_user_id) REFERENCES users (id)
    )
    """,
    "CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at)",
    "CREATE INDEX ix_audit_logs_action ON audit_logs (action)",
    "CREATE INDEX ix_audit_logs_actor_user_id ON audit_logs (actor_user_id)",
    """
    CREATE TABLE import_jobs (
        id UUID NOT NULL,
        created_by_user_id UUID NOT NULL,
        status importjobstatus NOT NULL,
        total_rows INTEGER NOT NULL,
        valid_rows INTEGER NOT NULL,
        invalid_rows INTEGER NOT NULL,
        payload JSONB NOT NULL,
        invalid_details JSONB NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        published_at TIMESTAMP WITHOUT TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(created_by_user_id) REFERENCES users (id)
    )
    """,
    "CREATE INDEX ix_import_jobs_created_by_user_id ON import_jobs (created_by_user_id)",
    "CREATE INDEX ix_import_jobs_status ON import_jobs (status)",
    """
    CREATE TABLE routes (
        id UUID NOT NULL,
        courier_id UUID NOT NULL,
        status routestatus NOT NULL,
        planned_distance_km FLOAT NOT NULL,
        actual_distance_km FLOAT,
        optimization_run_id UUID,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        started_at TIMESTAMP WITHOUT TIME ZONE,
        completed_at TIMESTAMP WITHOUT TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(courier_id) REFERENCES couriers (id)
    )
    """,
    """
    CREATE TABLE location_snapshots (
        id BIGSERIAL NOT NULL,
        courier_id UUID NOT NULL,
        lat FLOAT NOT NULL,
        lon FLOAT NOT NULL,
        heading FLOAT,
        speed_kmh FLOAT,
        accuracy_m FLOAT NOT NULL,
        recorded_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(courier_id) REFERENCES couriers (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX ix_location_snapshots_courier_recorded ON location_snapshots (courier_id, recorded_at)",
    """
    CREATE TABLE webhook_delivery_log (
        id UUID NOT NULL,
        webhook_endpoint_id UUID NOT NULL,
        event_type VARCHAR(100) NOT NULL,
        payload JSONB NOT NULL,
        attempt_number INTEGER NOT NULL,
        delivery_status webhookdeliverystatus NOT NULL,
        response_status INTEGER,
        error_message TEXT,
        sent_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        next_retry_at TIMESTAMP WITHOUT TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(webhook_endpoint_id) REFERENCES webhook_endpoints (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE orders (
        id UUID NOT NULL,
        type ordertype NOT NULL,
        status orderstatus NOT NULL,
        priority orderpriority NOT NULL,
        pickup_address VARCHAR(500) NOT NULL,
        pickup_lat FLOAT NOT NULL,
        pickup_lon FLOAT NOT NULL,
        delivery_address VARCHAR(500) NOT NULL,
        delivery_lat FLOAT NOT NULL,
        delivery_lon FLOAT NOT NULL,
        weight_kg FLOAT NOT NULL,
        is_fragile BOOLEAN NOT NULL,
        is_temp_sensitive BOOLEAN NOT NULL,
        earliest_pickup TIMESTAMP WITHOUT TIME ZONE,
        latest_pickup TIMESTAMP WITHOUT TIME ZONE,
        earliest_delivery TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        latest_delivery TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        assigned_courier_id UUID,
        route_id UUID,
        customer_email VARCHAR(255) NOT NULL,
        customer_phone VARCHAR(50),
        tracking_token VARCHAR(1000) NOT NULL,
        external_ref VARCHAR(255),
        attempt_count INTEGER NOT NULL,
        max_attempts INTEGER NOT NULL,
        failed_at TIMESTAMP WITHOUT TIME ZONE,
        customer_response_status failedordercustomerresponsestatus,
        customer_response_due_at TIMESTAMP WITHOUT TIME ZONE,
        escalated_at TIMESTAMP WITHOUT TIME ZONE,
        notes TEXT,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(assigned_courier_id) REFERENCES couriers (id) ON DELETE SET NULL,
        FOREIGN KEY(route_id) REFERENCES routes (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX ix_orders_customer_response_status ON orders (customer_response_status)",
    """
    CREATE TABLE route_stops (
        id UUID NOT NULL,
        route_id UUID NOT NULL,
        order_id UUID NOT NULL,
        sequence_number INTEGER NOT NULL,
        stop_type stoptype NOT NULL,
        lat FLOAT NOT NULL,
        lon FLOAT NOT NULL,
        address VARCHAR(500) NOT NULL,
        planned_arrival TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        actual_arrival TIMESTAMP WITHOUT TIME ZONE,
        status stopstatus NOT NULL,
        failure_reason failurereason,
        notes TEXT,
        PRIMARY KEY (id),
        FOREIGN KEY(route_id) REFERENCES routes (id) ON DELETE CASCADE,
        FOREIGN KEY(order_id) REFERENCES orders (id)
    )
    """,
    """
    CREATE TABLE notifications (
        id UUID NOT NULL,
        order_id UUID,
        channel notificationchannel NOT NULL,
        event_type VARCHAR(100) NOT NULL,
        recipient VARCHAR(255) NOT NULL,
        dedupe_key VARCHAR(128),
        payload JSONB,
        provider_message_id VARCHAR(255),
        status notificationstatus NOT NULL,
        sent_at TIMESTAMP WITHOUT TIME ZONE,
        error_message TEXT,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(order_id) REFERENCES orders (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX ix_notifications_dedupe_key ON notifications (dedupe_key)",
    """
    CREATE TABLE order_status_history (
        id UUID NOT NULL,
        order_id UUID NOT NULL,
        from_status orderstatus,
        to_status orderstatus NOT NULL,
        actor_user_id UUID,
        reason TEXT,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(order_id) REFERENCES orders (id) ON DELETE CASCADE,
        FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX ix_order_status_history_created_at ON order_status_history (created_at)",
    "CREATE INDEX ix_order_status_history_order_id ON order_status_history (order_id)",
    "CREATE INDEX ix_order_status_history_to_status ON order_status_history (to_status)",
    """
    CREATE TABLE proof_of_delivery (
        id UUID NOT NULL,
        order_id UUID NOT NULL,
        route_stop_id UUID NOT NULL,
        photo_url VARCHAR(1000),
        signature_url VARCHAR(1000),
        captured_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        notes TEXT,
        PRIMARY KEY (id),
        FOREIGN KEY(order_id) REFERENCES orders (id) ON DELETE CASCADE,
        FOREIGN KEY(route_stop_id) REFERENCES route_stops (id) ON DELETE CASCADE
    )
    """,
)


_DOWNGRADE_SQL: tuple[str, ...] = (
    "DROP TABLE IF EXISTS proof_of_delivery CASCADE",
    "DROP TABLE IF EXISTS order_status_history CASCADE",
    "DROP TABLE IF EXISTS notifications CASCADE",
    "DROP TABLE IF EXISTS route_stops CASCADE",
    "DROP TABLE IF EXISTS orders CASCADE",
    "DROP TABLE IF EXISTS webhook_delivery_log CASCADE",
    "DROP TABLE IF EXISTS location_snapshots CASCADE",
    "DROP TABLE IF EXISTS routes CASCADE",
    "DROP TABLE IF EXISTS import_jobs CASCADE",
    "DROP TABLE IF EXISTS audit_logs CASCADE",
    "DROP TABLE IF EXISTS webhook_endpoints CASCADE",
    "DROP TABLE IF EXISTS couriers CASCADE",
    "DROP TABLE IF EXISTS optimization_runs CASCADE",
    "DROP TABLE IF EXISTS api_clients CASCADE",
    "DROP TABLE IF EXISTS users CASCADE",
    "DROP TYPE IF EXISTS optimizationrunstatus",
    "DROP TYPE IF EXISTS optimizationruntype",
    "DROP TYPE IF EXISTS importjobstatus",
    "DROP TYPE IF EXISTS webhookdeliverystatus",
    "DROP TYPE IF EXISTS notificationstatus",
    "DROP TYPE IF EXISTS notificationchannel",
    "DROP TYPE IF EXISTS failurereason",
    "DROP TYPE IF EXISTS stopstatus",
    "DROP TYPE IF EXISTS stoptype",
    "DROP TYPE IF EXISTS routestatus",
    "DROP TYPE IF EXISTS failedordercustomerresponsestatus",
    "DROP TYPE IF EXISTS orderpriority",
    "DROP TYPE IF EXISTS orderstatus",
    "DROP TYPE IF EXISTS ordertype",
    "DROP TYPE IF EXISTS availabilitystatus",
    "DROP TYPE IF EXISTS vehicletype",
    "DROP TYPE IF EXISTS userrole",
)


def upgrade() -> None:
    for statement in _UPGRADE_SQL:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE_SQL:
        op.execute(statement)
