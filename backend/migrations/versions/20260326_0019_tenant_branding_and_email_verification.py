"""tenant branding, email verification, OIDC provider, RBAC permissions

Revision ID: 20260326_0019
Revises: 20260315_0018
Create Date: 2026-03-26

Changes:
- tenants: add logo_url, primary_color, secondary_color (8.4)
- users: add email_verified, email_verification_token, email_verification_sent_at (8.6)
- oidc_providers: new table for per-tenant SSO/OIDC configuration (8.1)
- role_permissions: new table for granular RBAC (8.2)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260326_0019"
down_revision = "20260315_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Tenant branding (8.4) ──────────────────────────────────────────────
    op.add_column("tenants", sa.Column("logo_url", sa.String(2048), nullable=True))
    op.add_column("tenants", sa.Column("primary_color", sa.String(7), nullable=True))
    op.add_column("tenants", sa.Column("secondary_color", sa.String(7), nullable=True))

    # ── Email verification (8.6) ───────────────────────────────────────────
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("users", sa.Column("email_verification_token", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("email_verification_sent_at", sa.DateTime(), nullable=True))

    # ── OIDC provider config (8.1) ─────────────────────────────────────────
    op.create_table(
        "oidc_providers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("issuer", sa.String(2048), nullable=False),
        sa.Column("client_id", sa.String(512), nullable=False),
        sa.Column("client_secret_enc", sa.String(2048), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("scopes", sa.String(512), nullable=False, server_default="openid email profile"),
        sa.Column("role_claim", sa.String(128), nullable=False, server_default="lmd_role"),
        sa.Column("default_role", sa.String(64), nullable=False, server_default="DISPATCHER"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_oidc_providers_tenant_id"),
    )

    # ── Granular RBAC permissions (8.2) ───────────────────────────────────
    op.create_table(
        "role_permissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("permission", sa.String(128), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "role", "permission", name="uq_role_permissions"),
    )


def downgrade() -> None:
    op.drop_table("role_permissions")
    op.drop_table("oidc_providers")

    op.drop_column("users", "email_verification_sent_at")
    op.drop_column("users", "email_verification_token")
    op.drop_column("users", "email_verified")

    op.drop_column("tenants", "secondary_color")
    op.drop_column("tenants", "primary_color")
    op.drop_column("tenants", "logo_url")
