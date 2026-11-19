"""traffic cell profiles

Revision ID: 20260225_0009
Revises: 20260224_0008
Create Date: 2026-02-25 10:20:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260225_0009"
down_revision: Union[str, None] = "20260224_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS traffic_cell_profiles (
            id SERIAL PRIMARY KEY,
            geohash6 VARCHAR(6) NOT NULL,
            day_of_week INTEGER NOT NULL,
            hour_bucket INTEGER NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            ema_multiplier DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_traffic_cell_profiles_cell_time
        ON traffic_cell_profiles (geohash6, day_of_week, hour_bucket)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_traffic_cell_profiles_lookup
        ON traffic_cell_profiles (geohash6, day_of_week, hour_bucket)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_traffic_cell_profiles_lookup")
    op.execute("DROP INDEX IF EXISTS uq_traffic_cell_profiles_cell_time")
    op.execute("DROP TABLE IF EXISTS traffic_cell_profiles")
