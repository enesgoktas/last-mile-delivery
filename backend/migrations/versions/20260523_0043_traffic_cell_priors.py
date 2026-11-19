"""traffic cell priors + UTC->Istanbul bucket shift

Revision ID: 20260523_0043
Revises: 20260513_0042
Create Date: 2026-05-23 12:00:00.000000

This migration does two things:

1. Creates the new `traffic_cell_priors` table — a parallel store for
   externally-derived traffic multipliers (IBB municipal open data, with
   room to add more cities later via region_code).

2. Shifts existing `traffic_cell_profiles.hour_bucket` rows by +3 because
   we are flipping the bucketing timezone from UTC to Europe/Istanbul
   (Istanbul is UTC+3 year-round; no DST since 2016). Rush hour is a
   local concept and aligning the buckets to local time keeps the
   fleet-EMA semantics correct as we add the IBB prior (which is
   natively in local time).
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260523_0043"
down_revision: Union[str, None] = "20260513_0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS traffic_cell_priors (
            id SERIAL PRIMARY KEY,
            region_code VARCHAR(8) NOT NULL,
            geohash6 VARCHAR(6) NOT NULL,
            day_of_week INTEGER NOT NULL,
            hour_bucket INTEGER NOT NULL,
            prior_multiplier DOUBLE PRECISION NOT NULL,
            bucket_mean_speed_kmh DOUBLE PRECISION NOT NULL,
            bucket_vehicle_samples BIGINT NOT NULL DEFAULT 0,
            computed_free_flow_kmh DOUBLE PRECISION NOT NULL,
            is_extrapolated_free_flow BOOLEAN NOT NULL DEFAULT FALSE,
            source_name VARCHAR(32) NOT NULL DEFAULT 'IBB',
            source_first_seen DATE NOT NULL,
            source_last_seen DATE NOT NULL,
            computed_at TIMESTAMP NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_traffic_cell_priors_dow
                CHECK (day_of_week BETWEEN 0 AND 6),
            CONSTRAINT ck_traffic_cell_priors_hour
                CHECK (hour_bucket BETWEEN 0 AND 23),
            CONSTRAINT ck_traffic_cell_priors_multiplier_range
                CHECK (prior_multiplier BETWEEN 0.6 AND 1.8)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_traffic_cell_priors
        ON traffic_cell_priors (region_code, geohash6, day_of_week, hour_bucket)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_traffic_cell_priors_region_cell_time
        ON traffic_cell_priors (region_code, geohash6, day_of_week, hour_bucket)
        """
    )

    # One-time shift: existing rows were bucketed in UTC; from now on we
    # bucket in Europe/Istanbul (UTC+3). Shift hour_bucket by +3 so the
    # historical EMA values land on their locally-correct buckets.
    op.execute(
        """
        UPDATE traffic_cell_profiles
        SET hour_bucket = (hour_bucket + 3) % 24
        """
    )


def downgrade() -> None:
    # Reverse the bucket shift first so re-applying upgrade() is idempotent.
    op.execute(
        """
        UPDATE traffic_cell_profiles
        SET hour_bucket = (hour_bucket + 21) % 24
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_traffic_cell_priors_region_cell_time")
    op.execute("DROP INDEX IF EXISTS uq_traffic_cell_priors")
    op.execute("DROP TABLE IF EXISTS traffic_cell_priors")
