"""courier push provider metadata

Revision ID: 20260326_0020
Revises: 20260326_0019
Create Date: 2026-03-26

Changes:
- couriers: add push_provider and push_token columns for provider-aware push delivery
- backfill existing FCM registrations into the new provider-aware columns
"""

from alembic import op
import sqlalchemy as sa

revision = "20260326_0020"
down_revision = "20260326_0019"
branch_labels = None
depends_on = None


push_provider_enum = sa.Enum("FCM", "EXPO", name="pushprovider")


def upgrade() -> None:
    bind = op.get_bind()
    push_provider_enum.create(bind, checkfirst=True)
    op.add_column("couriers", sa.Column("push_provider", push_provider_enum, nullable=True))
    op.add_column("couriers", sa.Column("push_token", sa.String(length=512), nullable=True))
    op.execute(
        """
        UPDATE couriers
        SET push_provider = 'FCM', push_token = fcm_token
        WHERE fcm_token IS NOT NULL
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_column("couriers", "push_token")
    op.drop_column("couriers", "push_provider")
    push_provider_enum.drop(bind, checkfirst=True)
