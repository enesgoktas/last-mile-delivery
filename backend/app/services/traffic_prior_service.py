"""Traffic prior service — historical-data-derived traffic multipliers.

This module turns external open-data CSVs (Istanbul Municipality / IBB
hourly traffic density) into per-cell, per-time-bucket multipliers that
the routing engine can use as a *prior* when the fleet has not yet
collected enough first-party observations.

Two responsibilities:

1. **Ingest** (`ingest_ibb_csv`, `refresh_region`): stream CSVs via
   DuckDB, aggregate to `(region_code, geohash6, day_of_week, hour_bucket)`,
   upsert into `traffic_cell_priors` in batches.
2. **Lookup** (`lookup_prior_multipliers`, `get_prior_status`): point
   reads of the prior table, called from `traffic_profile_service` as a
   fallback when the fleet table has no row for a cell+bucket.

The free-flow speed per cell is computed as the P85 of `AVERAGE_SPEED`
across all observations for that cell. Cells with too few observations
fall back to a city-wide P85. Multiplier = free_flow / observed, clamped
to the same [0.6, 1.8] window the fleet EMA already uses.

Timestamps in IBB CSVs are Europe/Istanbul local time. The DuckDB
pipeline extracts DOW/hour directly without timezone conversion; the
lookup side uses `TRAFFIC_PROFILE_TIMEZONE` (= Europe/Istanbul) for
consistency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.traffic_cell_prior import TrafficCellPrior
from app.services.traffic_profile_service import _clamp_multiplier
from app.utils.compat import utcnow_naive

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Postgres' wire protocol caps parameters at 32_767 per statement. With
# 13 columns per row, we keep the chunk well under (2_000 × 13 = 26_000).
_UPSERT_CHUNK_SIZE = 2_000
_FETCHMANY_SIZE = 10_000


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    region_code: str
    csv_paths: list[str]
    rows_processed: int
    extrapolated_buckets: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class PriorStatus:
    region_code: str
    prior_enabled: bool
    total_cells: int
    total_buckets: int
    source_first_seen: date | None
    source_last_seen: date | None
    last_computed_at: datetime | None
    extrapolated_pct: float


_AGGREGATE_SQL = """
WITH per_bucket AS (
    SELECT
        LOWER(GEOHASH) AS geohash6,
        ((EXTRACT(DOW FROM DATE_TIME::TIMESTAMP) + 6) % 7)::INT AS day_of_week,
        EXTRACT(HOUR FROM DATE_TIME::TIMESTAMP)::INT AS hour_bucket,
        AVG(AVERAGE_SPEED) AS bucket_mean_speed,
        SUM(NUMBER_OF_VEHICLES) AS bucket_vehicle_samples,
        COUNT(*) AS bucket_row_count
    FROM read_csv_auto($csv_paths, header=true)
    WHERE AVERAGE_SPEED > 0
    GROUP BY 1, 2, 3
),
per_cell AS (
    SELECT
        geohash6,
        QUANTILE_CONT(bucket_mean_speed, 0.85) AS cell_p85,
        SUM(bucket_row_count) AS total_rows
    FROM per_bucket
    GROUP BY 1
),
city_ff AS (
    SELECT QUANTILE_CONT(bucket_mean_speed, 0.85) AS city_p85
    FROM per_bucket USING SAMPLE 1%
)
SELECT
    pb.geohash6,
    pb.day_of_week,
    pb.hour_bucket,
    pb.bucket_mean_speed,
    pb.bucket_vehicle_samples,
    CASE WHEN pc.total_rows >= $min_cell_rows
         THEN GREATEST(10.0, LEAST(110.0, pc.cell_p85))
         ELSE GREATEST(10.0, LEAST(110.0, cf.city_p85))
    END AS computed_free_flow_kmh,
    (pc.total_rows < $min_cell_rows) AS is_extrapolated_free_flow,
    GREATEST($min_mult, LEAST($max_mult,
        CASE WHEN pc.total_rows >= $min_cell_rows
             THEN GREATEST(10.0, LEAST(110.0, pc.cell_p85)) / pb.bucket_mean_speed
             ELSE GREATEST(10.0, LEAST(110.0, cf.city_p85)) / pb.bucket_mean_speed
        END
    )) AS prior_multiplier
FROM per_bucket pb
JOIN per_cell pc USING (geohash6)
CROSS JOIN city_ff cf
WHERE pb.bucket_vehicle_samples >= $min_bucket_samples
"""


def _open_duckdb() -> "duckdb.DuckDBPyConnection":
    import duckdb

    return duckdb.connect(database=":memory:")


def _run_aggregate(
    duck: "duckdb.DuckDBPyConnection",
    *,
    csv_paths: list[str],
    min_cell_rows: int,
    min_bucket_samples: int,
    min_mult: float,
    max_mult: float,
) -> "duckdb.DuckDBPyRelation":
    return duck.execute(
        _AGGREGATE_SQL,
        {
            "csv_paths": csv_paths,
            "min_cell_rows": min_cell_rows,
            "min_bucket_samples": min_bucket_samples,
            "min_mult": min_mult,
            "max_mult": max_mult,
        },
    )


def compute_free_flow(
    duck: "duckdb.DuckDBPyConnection",
    *,
    csv_paths: list[str],
    min_cell_rows: int | None = None,
) -> tuple[dict[str, float], float]:
    """Return (per_cell_p85_map, city_wide_p85) from a CSV glob.

    Pure read; no DB writes. Exposed so unit tests can spin up DuckDB on
    a small fixture CSV without exercising the upsert path.
    """
    threshold = min_cell_rows if min_cell_rows is not None else settings.TRAFFIC_PRIOR_MIN_CELL_ROWS

    rows = duck.execute(
        """
        WITH per_bucket AS (
            SELECT
                LOWER(GEOHASH) AS geohash6,
                AVG(AVERAGE_SPEED) AS bucket_mean_speed,
                COUNT(*) AS bucket_row_count
            FROM read_csv_auto($csv_paths, header=true)
            WHERE AVERAGE_SPEED > 0
            GROUP BY 1, ((EXTRACT(DOW FROM DATE_TIME::TIMESTAMP) + 6) % 7)::INT,
                       EXTRACT(HOUR FROM DATE_TIME::TIMESTAMP)::INT
        ),
        per_cell AS (
            SELECT
                geohash6,
                QUANTILE_CONT(bucket_mean_speed, 0.85) AS cell_p85,
                SUM(bucket_row_count) AS total_rows
            FROM per_bucket GROUP BY 1
        )
        SELECT
            geohash6,
            GREATEST(10.0, LEAST(110.0, cell_p85)) AS clamped_p85,
            total_rows
        FROM per_cell
        """,
        {"csv_paths": csv_paths},
    ).fetchall()

    per_cell = {
        row[0]: float(row[1]) for row in rows if int(row[2]) >= threshold
    }

    city_row = duck.execute(
        """
        WITH per_bucket AS (
            SELECT
                LOWER(GEOHASH) AS geohash6,
                AVG(AVERAGE_SPEED) AS bucket_mean_speed
            FROM read_csv_auto($csv_paths, header=true)
            WHERE AVERAGE_SPEED > 0
            GROUP BY 1, ((EXTRACT(DOW FROM DATE_TIME::TIMESTAMP) + 6) % 7)::INT,
                       EXTRACT(HOUR FROM DATE_TIME::TIMESTAMP)::INT
        )
        SELECT GREATEST(10.0, LEAST(110.0, QUANTILE_CONT(bucket_mean_speed, 0.85)))
        FROM per_bucket
        """,
        {"csv_paths": csv_paths},
    ).fetchone()
    city_p85 = float(city_row[0]) if city_row and city_row[0] is not None else 30.0
    return per_cell, city_p85


async def _upsert_chunk(
    db: AsyncSession,
    rows: list[dict],
) -> None:
    if not rows:
        return
    stmt = pg_insert(TrafficCellPrior).values(rows)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=["region_code", "geohash6", "day_of_week", "hour_bucket"],
        set_={
            "prior_multiplier": excluded.prior_multiplier,
            "bucket_mean_speed_kmh": excluded.bucket_mean_speed_kmh,
            "bucket_vehicle_samples": excluded.bucket_vehicle_samples,
            "computed_free_flow_kmh": excluded.computed_free_flow_kmh,
            "is_extrapolated_free_flow": excluded.is_extrapolated_free_flow,
            "source_name": excluded.source_name,
            "source_first_seen": excluded.source_first_seen,
            "source_last_seen": excluded.source_last_seen,
            "computed_at": excluded.computed_at,
        },
    )
    await db.execute(stmt)
    await db.commit()


async def ingest_ibb_csv(
    db: AsyncSession,
    *,
    csv_paths: list[Path | str],
    region_code: str,
    source_first_seen: date,
    source_last_seen: date,
    source_name: str = "IBB",
    duck: "duckdb.DuckDBPyConnection | None" = None,
) -> IngestionSummary:
    """Aggregate one or more IBB CSVs and upsert into the prior table.

    Streams DuckDB output in batches to keep RAM bounded. Idempotent —
    re-running with the same inputs overwrites the prior rows in place.
    """
    import time as _time

    started = _time.monotonic()
    str_paths = [str(p) for p in csv_paths]
    if not str_paths:
        raise ValueError("ingest_ibb_csv requires at least one csv path")

    owns_duck = duck is None
    duck = duck or _open_duckdb()

    try:
        relation = _run_aggregate(
            duck,
            csv_paths=str_paths,
            min_cell_rows=settings.TRAFFIC_PRIOR_MIN_CELL_ROWS,
            min_bucket_samples=settings.TRAFFIC_PRIOR_MIN_BUCKET_SAMPLES,
            min_mult=settings.TRAFFIC_PROFILE_MIN_MULTIPLIER,
            max_mult=settings.TRAFFIC_PROFILE_MAX_MULTIPLIER,
        )

        chunk: list[dict] = []
        rows_processed = 0
        extrapolated = 0
        now = utcnow_naive()

        while True:
            batch = relation.fetchmany(_FETCHMANY_SIZE)
            if not batch:
                break
            for row in batch:
                (
                    geohash6,
                    day_of_week,
                    hour_bucket,
                    bucket_mean_speed,
                    bucket_vehicle_samples,
                    computed_free_flow_kmh,
                    is_extrapolated,
                    prior_multiplier,
                ) = row
                if is_extrapolated:
                    extrapolated += 1
                chunk.append(
                    {
                        "region_code": region_code,
                        "geohash6": geohash6,
                        "day_of_week": int(day_of_week),
                        "hour_bucket": int(hour_bucket),
                        "prior_multiplier": _clamp_multiplier(float(prior_multiplier)),
                        "bucket_mean_speed_kmh": float(bucket_mean_speed),
                        "bucket_vehicle_samples": int(bucket_vehicle_samples),
                        "computed_free_flow_kmh": float(computed_free_flow_kmh),
                        "is_extrapolated_free_flow": bool(is_extrapolated),
                        "source_name": source_name,
                        "source_first_seen": source_first_seen,
                        "source_last_seen": source_last_seen,
                        "computed_at": now,
                    }
                )
                if len(chunk) >= _UPSERT_CHUNK_SIZE:
                    await _upsert_chunk(db, chunk)
                    rows_processed += len(chunk)
                    chunk = []
        if chunk:
            await _upsert_chunk(db, chunk)
            rows_processed += len(chunk)

        elapsed = _time.monotonic() - started
        logger.info(
            "IBB ingest complete: region=%s rows=%d extrapolated=%d elapsed=%.1fs",
            region_code,
            rows_processed,
            extrapolated,
            elapsed,
        )
        return IngestionSummary(
            region_code=region_code,
            csv_paths=str_paths,
            rows_processed=rows_processed,
            extrapolated_buckets=extrapolated,
            duration_seconds=elapsed,
        )
    finally:
        if owns_duck:
            duck.close()


async def refresh_region(
    db: AsyncSession,
    *,
    region_code: str,
    csv_paths: list[Path | str],
    source_first_seen: date,
    source_last_seen: date,
) -> IngestionSummary:
    """Orchestrator for the monthly Celery task.

    Currently a thin wrapper around `ingest_ibb_csv` — accepts pre-downloaded
    local CSV paths. The download step is intentionally out of scope; the
    Celery task is responsible for fetching the latest IBB monthly bundle
    and writing it to a known location before calling this.
    """
    return await ingest_ibb_csv(
        db,
        csv_paths=csv_paths,
        region_code=region_code,
        source_first_seen=source_first_seen,
        source_last_seen=source_last_seen,
    )


async def lookup_prior_multipliers(
    db: AsyncSession,
    *,
    region_code: str,
    geohashes: set[str] | list[str],
    day_of_week: int,
    hour_bucket: int,
) -> dict[str, float]:
    """Return `{geohash6: prior_multiplier}` for the given cells+bucket.

    Defensive clamp applied at read time even though the write side
    already clamps; cheap insurance against a stale row predating a
    clamp-bound change.
    """
    hashes = list(geohashes)
    if not hashes:
        return {}
    result = await db.execute(
        select(
            TrafficCellPrior.geohash6,
            TrafficCellPrior.prior_multiplier,
        ).where(
            TrafficCellPrior.region_code == region_code,
            TrafficCellPrior.day_of_week == day_of_week,
            TrafficCellPrior.hour_bucket == hour_bucket,
            TrafficCellPrior.geohash6.in_(hashes),
        )
    )
    return {
        row[0]: _clamp_multiplier(float(row[1]))
        for row in result.all()
    }


async def get_prior_status(
    db: AsyncSession,
    *,
    region_code: str,
) -> PriorStatus:
    """Aggregate stats for the admin status endpoint."""
    totals = await db.execute(
        select(
            func.count(TrafficCellPrior.id).label("total_buckets"),
            func.count(func.distinct(TrafficCellPrior.geohash6)).label("total_cells"),
            func.min(TrafficCellPrior.source_first_seen).label("source_first_seen"),
            func.max(TrafficCellPrior.source_last_seen).label("source_last_seen"),
            func.max(TrafficCellPrior.computed_at).label("last_computed_at"),
            func.sum(
                case((TrafficCellPrior.is_extrapolated_free_flow, 1), else_=0)
            ).label("extrapolated_count"),
        ).where(TrafficCellPrior.region_code == region_code)
    )
    row = totals.one()
    total_buckets = int(row.total_buckets or 0)
    extrapolated_count = int(row.extrapolated_count or 0)
    pct = (extrapolated_count / total_buckets * 100.0) if total_buckets else 0.0
    return PriorStatus(
        region_code=region_code,
        prior_enabled=settings.TRAFFIC_PRIOR_ENABLED,
        total_cells=int(row.total_cells or 0),
        total_buckets=total_buckets,
        source_first_seen=row.source_first_seen,
        source_last_seen=row.source_last_seen,
        last_computed_at=row.last_computed_at,
        extrapolated_pct=round(pct, 2),
    )
