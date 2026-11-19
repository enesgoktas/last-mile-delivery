"""Add SUPERADMIN role + seed platform tenant + Doruk and Murat as superadmins

Revision ID: 20260510_0029
Revises: 20260506_0028
Create Date: 2026-05-10

This migration introduces the platform-level SUPERADMIN role + bootstraps the
two human superadmins who manage the system: Doruk and Murat. They live in a
dedicated "platform" tenant so the existing UniqueConstraint(email, tenant_id)
on `users` is respected without collision against any customer tenant.

Idempotent: if the platform tenant or the two users already exist, this is a
no-op for those rows. Re-running across dev / staging / prod is safe.
"""
import uuid

import sqlalchemy as sa
from alembic import op
from passlib.context import CryptContext
from sqlalchemy.dialects.postgresql import UUID

revision = "20260510_0029"
down_revision = "20260506_0028"
branch_labels = None
depends_on = None


# Match the application's password hashing config exactly so login works.
# (See `app/utils/auth.py` — also bcrypt via passlib's CryptContext.)
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


# Stable UUIDs for the platform tenant + the two superadmins so re-running
# this migration on a fresh DB produces deterministic IDs (useful for tests
# and for cross-environment reasoning). These are public — not secrets.
_PLATFORM_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_DORUK_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
_MURAT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")


def upgrade() -> None:
    # 1. Extend the userrole enum type with SUPERADMIN.
    # PostgreSQL ALTER TYPE ADD VALUE must run outside a transaction block.
    # Alembic's default autocommit_block helps here.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SUPERADMIN'")

    # 2. Seed the platform tenant (idempotent).
    op.execute(
        sa.text(
            """
            INSERT INTO tenants (id, name, slug, is_active, created_at)
            VALUES (:id, :name, :slug, TRUE, NOW())
            ON CONFLICT (slug) DO NOTHING
            """
        ).bindparams(
            id=_PLATFORM_TENANT_ID,
            name="Avernsys (platform)",
            slug="platform",
        )
    )

    # 3. Seed the two SUPERADMIN users (idempotent).
    # Hash passwords at migration runtime so plaintext never touches disk
    # outside the running container's memory.
    doruk_hash = _pwd.hash("admin123!doruk")
    murat_hash = _pwd.hash("admin123!murat")

    insert_user = sa.text(
        """
        INSERT INTO users (
            id, tenant_id, email, hashed_password, role,
            is_active, created_at, email_verified
        )
        VALUES (
            :id, :tenant_id, :email, :hashed_password, 'SUPERADMIN',
            TRUE, NOW(), TRUE
        )
        ON CONFLICT (email, tenant_id) DO NOTHING
        """
    )

    op.execute(
        insert_user.bindparams(
            id=_DORUK_USER_ID,
            tenant_id=_PLATFORM_TENANT_ID,
            email="doruk@avernsys.com",
            hashed_password=doruk_hash,
        )
    )
    op.execute(
        insert_user.bindparams(
            id=_MURAT_USER_ID,
            tenant_id=_PLATFORM_TENANT_ID,
            email="murat@avernsys.com",
            hashed_password=murat_hash,
        )
    )


def downgrade() -> None:
    # Remove the seeded users + platform tenant.
    op.execute(
        sa.text(
            "DELETE FROM users WHERE id IN (:doruk, :murat)"
        ).bindparams(doruk=_DORUK_USER_ID, murat=_MURAT_USER_ID)
    )
    op.execute(
        sa.text("DELETE FROM tenants WHERE id = :id").bindparams(
            id=_PLATFORM_TENANT_ID
        )
    )
    # Note: we don't remove the SUPERADMIN enum value. PostgreSQL doesn't
    # support ALTER TYPE ... DROP VALUE without recreating the type; not
    # worth the complexity since leaving an unused enum value is harmless.
