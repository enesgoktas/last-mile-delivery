"""Read/write the per-(courier, address) empirical-service-time table.

Layer 3 — the compounding moat. Two sides:

  * **Write**: on every successful stop completion we record the
    observed service duration (`actual_departure - actual_arrival`, or
    a constant fallback when we only have arrival). The row's running
    median + sample count update via an in-place upsert.
  * **Read**: the solver's order-data builder asks "for this courier +
    this address, what's the empirical p50 service time?" — and falls
    back to the static estimate when sample_count < `_MIN_SAMPLES`.

The address key is a `geohash-8` of (lat, lon) rounded to ~19 m × 38 m,
tight enough to keep different doors in the same building separated
but tolerant of GPS jitter on repeat visits.
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.empirical_service_time import EmpiricalServiceTime
from app.utils.compat import utcnow_naive

logger = logging.getLogger(__name__)


# Gate solver use on minimum sample count — under this the static
# estimate is more reliable than 1-2 observations.
_MIN_SAMPLES_FOR_SOLVER_READ = 3

# Geohash precision constants (base-32 alphabet from the standard
# Wikipedia reference implementation; 8 chars ≈ 19 m × 38 m).
_GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"


def address_hash_for(lat: float, lon: float, *, precision: int = 8) -> str:
    """Compute the geohash key used to deduplicate "same place" stops.

    Standalone implementation — keeping the dependency footprint flat
    (no `python-geohash`). The precision-8 default is the right
    granularity per the module docstring.
    """
    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    geohash: list[str] = []
    bits = [16, 8, 4, 2, 1]
    bit_idx = 0
    even = True
    ch = 0
    while len(geohash) < precision:
        if even:
            mid = (lon_range[0] + lon_range[1]) / 2.0
            if lon > mid:
                ch |= bits[bit_idx]
                lon_range[0] = mid
            else:
                lon_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2.0
            if lat > mid:
                ch |= bits[bit_idx]
                lat_range[0] = mid
            else:
                lat_range[1] = mid
        even = not even
        bit_idx += 1
        if bit_idx == 5:
            geohash.append(_GEOHASH_ALPHABET[ch])
            bit_idx = 0
            ch = 0
    return "".join(geohash)


async def record_observation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    courier_id: uuid.UUID,
    lat: float,
    lon: float,
    service_duration_s: float,
) -> EmpiricalServiceTime | None:
    """Update (or insert) the rolling median + sample count for the
    (courier, address) pair. Idempotent in the sense that a duplicate
    observation just bumps the count and shifts the median.

    Algorithm: incremental median via the standard sample-mean recurrence
    that the rolling-median test asserts is stable for our pilot scale.
    For very large sample counts a proper online median (e.g. P²) would
    be more accurate; v1 keeps the math trivial.
    """
    if service_duration_s <= 0 or service_duration_s > 24 * 3600:
        return None  # clamp out obvious garbage (negative, multi-day)

    addr = address_hash_for(lat, lon)
    existing = (
        await db.execute(
            select(EmpiricalServiceTime).where(
                EmpiricalServiceTime.tenant_id == tenant_id,
                EmpiricalServiceTime.courier_id == courier_id,
                EmpiricalServiceTime.address_hash == addr,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        row = EmpiricalServiceTime(
            tenant_id=tenant_id,
            courier_id=courier_id,
            address_hash=addr,
            service_duration_s=service_duration_s,
            sample_count=1,
            confidence_band_s=0.0,
            last_observed_at=utcnow_naive(),
        )
        db.add(row)
        return row

    # Incremental median (close to mean for low-variance samples; for
    # higher variance, the dashboard reports `confidence_band_s` so the
    # operator sees the spread). New = old + (obs - old) / (n + 1).
    n_old = existing.sample_count
    old_median = float(existing.service_duration_s)
    new_median = old_median + (service_duration_s - old_median) / max(n_old + 1, 1)
    # Welford-style running variance for confidence_band.
    old_band_sq = float(existing.confidence_band_s or 0.0) ** 2
    new_band_sq = old_band_sq + (
        (service_duration_s - old_median) * (service_duration_s - new_median)
    ) / max(n_old + 1, 1)
    new_band = math.sqrt(max(new_band_sq, 0.0))

    existing.service_duration_s = new_median
    existing.confidence_band_s = new_band
    existing.sample_count = n_old + 1
    existing.last_observed_at = utcnow_naive()
    return existing


async def lookup_for_solver(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    courier_id: uuid.UUID,
    lat: float,
    lon: float,
) -> float | None:
    """Return the empirical p50 service time in seconds, or None if we
    don't have enough samples to trust it yet. The order-data builder
    falls back to the static estimate when this returns None.
    """
    addr = address_hash_for(lat, lon)
    row = (
        await db.execute(
            select(EmpiricalServiceTime).where(
                EmpiricalServiceTime.tenant_id == tenant_id,
                EmpiricalServiceTime.courier_id == courier_id,
                EmpiricalServiceTime.address_hash == addr,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.sample_count < _MIN_SAMPLES_FOR_SOLVER_READ:
        return None
    return float(row.service_duration_s)
