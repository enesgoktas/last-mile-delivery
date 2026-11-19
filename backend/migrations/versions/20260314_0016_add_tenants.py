"""Add tenant isolation across application tables.

Revision ID: 20260314_0016
Revises: 20260314_0015
Create Date: 2026-03-14
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260314_0016"
down_revision = "20260314_0015"
branch_labels = None
depends_on = None


DEFAULT_TENANT_NAME = "Default"
DEFAULT_TENANT_SLUG = "default"

TENANT_TABLES: tuple[str, ...] = (
    "users",
    "orders",
    "couriers",
    "routes",
    "route_stops",
    "depots",
    "packages",
    "inventory_events",
    "location_snapshots",
    "notifications",
    "proof_of_delivery",
    "import_jobs",
    "optimization_runs",
    "api_clients",
    "webhook_endpoints",
    "webhook_delivery_log",
    "sla_alerts",
)

NULLABLE_TENANT_TABLES: tuple[str, ...] = ("audit_logs",)


def _add_tenant_column(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
    )


def _create_tenant_fk_and_index(
    table_name: str,
    *,
    unique: bool = False,
) -> None:
    op.create_foreign_key(
        f"fk_{table_name}_tenant_id",
        table_name,
        "tenants",
        ["tenant_id"],
        ["id"],
    )
    if unique:
        op.create_unique_constraint(f"uq_{table_name}_tenant_id", table_name, ["tenant_id"])
    else:
        op.create_index(f"ix_{table_name}_tenant_id", table_name, ["tenant_id"])


def _drop_tenant_fk_and_index(
    table_name: str,
    *,
    unique: bool = False,
) -> None:
    if unique:
        op.drop_constraint(f"uq_{table_name}_tenant_id", table_name, type_="unique")
    else:
        op.drop_index(f"ix_{table_name}_tenant_id", table_name=table_name)
    op.drop_constraint(f"fk_{table_name}_tenant_id", table_name, type_="foreignkey")


def upgrade() -> None:
    bind = op.get_bind()
    default_tenant_id = uuid.uuid4()

    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'OWNER'")

    op.create_table(
        "tenants",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"])

    bind.execute(
        sa.text(
            """
            INSERT INTO tenants (id, name, slug, is_active, created_at)
            VALUES (:tenant_id, :name, :slug, TRUE, NOW())
            """
        ),
        {
            "tenant_id": default_tenant_id,
            "name": DEFAULT_TENANT_NAME,
            "slug": DEFAULT_TENANT_SLUG,
        },
    )

    for table_name in TENANT_TABLES:
        _add_tenant_column(table_name)

    _add_tenant_column("audit_logs")
    _add_tenant_column("system_settings")

    for table_name in (*TENANT_TABLES, *NULLABLE_TENANT_TABLES, "system_settings"):
        bind.execute(
            sa.text(f"UPDATE {table_name} SET tenant_id = :tenant_id WHERE tenant_id IS NULL"),
            {"tenant_id": default_tenant_id},
        )

    for table_name in TENANT_TABLES:
        op.alter_column(table_name, "tenant_id", existing_type=UUID(as_uuid=True), nullable=False)
        _create_tenant_fk_and_index(table_name)

    _create_tenant_fk_and_index("audit_logs")

    op.alter_column("system_settings", "tenant_id", existing_type=UUID(as_uuid=True), nullable=False)
    _create_tenant_fk_and_index("system_settings", unique=True)

    op.execute("DROP INDEX IF EXISTS ix_users_email")
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    op.create_unique_constraint("uq_users_email_tenant_id", "users", ["email", "tenant_id"])

    op.execute("CREATE SEQUENCE IF NOT EXISTS system_settings_id_seq")
    op.execute("ALTER SEQUENCE system_settings_id_seq OWNED BY system_settings.id")
    op.execute("ALTER TABLE system_settings ALTER COLUMN id SET DEFAULT nextval('system_settings_id_seq')")
    op.execute(
        """
        SELECT setval(
            'system_settings_id_seq',
            GREATEST((SELECT COALESCE(MAX(id), 1) FROM system_settings), 1),
            TRUE
        )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE system_settings ALTER COLUMN id DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS system_settings_id_seq")

    op.drop_constraint("uq_users_email_tenant_id", "users", type_="unique")
    op.drop_index("ix_users_email", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    _drop_tenant_fk_and_index("system_settings", unique=True)
    op.drop_column("system_settings", "tenant_id")

    _drop_tenant_fk_and_index("audit_logs")
    op.drop_column("audit_logs", "tenant_id")

    for table_name in reversed(TENANT_TABLES):
        _drop_tenant_fk_and_index(table_name)
        op.drop_column(table_name, "tenant_id")

    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
