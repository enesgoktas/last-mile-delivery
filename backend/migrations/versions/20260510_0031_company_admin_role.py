"""Phase 2.1: COMPANY_ADMIN role for per-tenant read-only ops + reporting

Revision ID: 20260510_0031
Revises: 20260510_0030
Create Date: 2026-05-10

Adds the COMPANY_ADMIN value to the `userrole` PostgreSQL enum. The
application-side semantics (read access via DISPATCH_ACCESS_ROLES,
all-false default permissions in operations_policy.team_permissions)
live in code; this migration only widens the DB enum.

Idempotent: ALTER TYPE ... ADD VALUE IF NOT EXISTS.
"""
from alembic import op

revision = "20260510_0031"
down_revision = "20260510_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'COMPANY_ADMIN'")


def downgrade() -> None:
    # PostgreSQL doesn't support ALTER TYPE DROP VALUE without recreating
    # the type; leaving the unused enum value is harmless.
    pass
