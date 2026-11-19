from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.traffic_profile import TrafficCellProfile
from app.utils.compat import utcnow_naive
from app.utils.geo import encode_geohash

_PROFILE_LOOKUP_CACHE_MAX_ENTRIES = 256
_profile_lookup_cache: OrderedDict[str, tuple[float, dict[str, float]]] = OrderedDict()


@dataclass(frozen=True, slots=True)
class DurationAdjustmentResult:
    durations: list[list[int]]
    applied: bool
    avg_multiplier: float
    profile_hits: int
    profile_misses: int
    fleet_hits: int = 0
    prior_hits: int = 0


def _normalize_datetime(dt: datetime) -> datetime:
    tz = ZoneInfo(settings.TRAFFIC_PROFILE_TIMEZONE)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    return dt.astimezone(tz)


def _cache_key(geohashes: set[str], dt: datetime, region_code: str = "") -> str:
    local_dt = _normalize_datetime(dt)
    return (
        f"{region_code}:{local_dt.weekday()}:{local_dt.hour}:"
        f"{','.join(sorted(geohashes))}"
    )


def _clamp_multiplier(value: float) -> float:
    return max(
        settings.TRAFFIC_PROFILE_MIN_MULTIPLIER,
        min(settings.TRAFFIC_PROFILE_MAX_MULTIPLIER, value),
    )


def _prune_lookup_cache(now_ts: float) -> None:
    expired_keys = [
        key
        for key, (expires_at, _value) in _profile_lookup_cache.items()
        if expires_at <= now_ts
    ]
    for key in expired_keys:
        _profile_lookup_cache.pop(key, None)

    while len(_profile_lookup_cache) > _PROFILE_LOOKUP_CACHE_MAX_ENTRIES:
        _profile_lookup_cache.popitem(last=False)


async def _load_multiplier_lookup(
    db: AsyncSession,
    *,
    geohashes: set[str],
    at_time: datetime,
    region_code: str | None = None,
) -> dict[str, float]:
    """Return per-cell traffic multipliers, merging fleet-first with prior fallback.

    Fleet observations (`TrafficCellProfile`) win for cells that pass the
    `TRAFFIC_PROFILE_MIN_SAMPLES` gate. For cells with no fleet entry, the
    `TrafficCellPrior` table provides a historical municipal-data multiplier
    when `TRAFFIC_PRIOR_ENABLED`. Cells with neither are absent from the
    returned dict (caller treats absence as "no adjustment available").
    """
    if not geohashes:
        return {}

    region = region_code or settings.TRAFFIC_PRIOR_ACTIVE_REGION_CODE
    cache_key = _cache_key(geohashes, at_time, region)
    now_ts = time.time()
    cached = _profile_lookup_cache.get(cache_key)
    if cached:
        if cached[0] > now_ts:
            _profile_lookup_cache.move_to_end(cache_key)
            return dict(cached[1])
        _profile_lookup_cache.pop(cache_key, None)

    _prune_lookup_cache(now_ts)

    local_dt = _normalize_datetime(at_time)
    fleet_result = await db.execute(
        select(TrafficCellProfile).where(
            TrafficCellProfile.geohash6.in_(geohashes),
            TrafficCellProfile.day_of_week == local_dt.weekday(),
            TrafficCellProfile.hour_bucket == local_dt.hour,
        )
    )
    fleet_rows = list(fleet_result.scalars().all())
    fleet_lookup = {
        row.geohash6: _clamp_multiplier(float(row.ema_multiplier))
        for row in fleet_rows
        if row.sample_count >= settings.TRAFFIC_PROFILE_MIN_SAMPLES
    }

    prior_lookup: dict[str, float] = {}
    uncovered = set(geohashes) - fleet_lookup.keys()
    if settings.TRAFFIC_PRIOR_ENABLED and uncovered:
        # Late import to avoid a circular dependency: traffic_prior_service
        # imports `_clamp_multiplier` from this module.
        from app.services import traffic_prior_service

        prior_lookup = await traffic_prior_service.lookup_prior_multipliers(
            db,
            region_code=region,
            geohashes=uncovered,
            day_of_week=local_dt.weekday(),
            hour_bucket=local_dt.hour,
        )

    # Fleet wins, prior fills gaps.
    lookup = {**prior_lookup, **fleet_lookup}
    _profile_lookup_cache[cache_key] = (
        now_ts + settings.TRAFFIC_PROFILE_CACHE_TTL_SECONDS,
        lookup,
    )
    _profile_lookup_cache.move_to_end(cache_key)
    _prune_lookup_cache(now_ts)
    return lookup


def invalidate_lookup_cache() -> None:
    _profile_lookup_cache.clear()


async def adjust_duration_matrix(
    db: AsyncSession,
    *,
    coords: list[tuple[float, float]],
    durations: list[list[int]],
    at_time: datetime,
) -> DurationAdjustmentResult:
    """Apply learned traffic multipliers on top of base durations."""
    if (
        not settings.ENABLE_TRAFFIC_PROFILES
        or not settings.TRAFFIC_PROFILE_ENABLED
        or not coords
        or not durations
    ):
        return DurationAdjustmentResult(
            durations=durations,
            applied=False,
            avg_multiplier=1.0,
            profile_hits=0,
            profile_misses=0,
        )

    geohashes = [encode_geohash(lat, lon, precision=6) for lat, lon in coords]
    lookup = await _load_multiplier_lookup(
        db,
        geohashes=set(geohashes),
        at_time=at_time,
    )
    if not lookup:
        return DurationAdjustmentResult(
            durations=durations,
            applied=False,
            avg_multiplier=1.0,
            profile_hits=0,
            profile_misses=len(geohashes),
        )

    adjusted = [row[:] for row in durations]
    multipliers: list[float] = []
    hits = 0
    misses = 0
    sentinel_threshold = 10_000_000

    for i, src_hash in enumerate(geohashes):
        src_multiplier = lookup.get(src_hash)
        for j, dst_hash in enumerate(geohashes):
            base = durations[i][j]
            if i == j or base <= 0 or base >= sentinel_threshold:
                continue
            dst_multiplier = lookup.get(dst_hash)
            if src_multiplier is None and dst_multiplier is None:
                misses += 1
                continue
            if src_multiplier is not None and dst_multiplier is not None:
                multiplier = (src_multiplier + dst_multiplier) / 2.0
            else:
                multiplier = src_multiplier if src_multiplier is not None else dst_multiplier
            multiplier = _clamp_multiplier(float(multiplier))
            adjusted[i][j] = max(1, round(base * multiplier))
            multipliers.append(multiplier)
            hits += 1

    if not multipliers:
        return DurationAdjustmentResult(
            durations=durations,
            applied=False,
            avg_multiplier=1.0,
            profile_hits=0,
            profile_misses=max(misses, len(geohashes)),
        )

    return DurationAdjustmentResult(
        durations=adjusted,
        applied=True,
        avg_multiplier=sum(multipliers) / len(multipliers),
        profile_hits=hits,
        profile_misses=misses,
    )


async def build_hourly_duration_matrices(
    db: AsyncSession,
    *,
    coords: list[tuple[float, float]],
    durations: list[list[int]],
    at_time: datetime,
) -> dict[int, list[list[int]]]:
    """Build 24 hour-bucket duration matrices from learned traffic profiles."""
    hourly: dict[int, list[list[int]]] = {}
    base = _normalize_datetime(at_time)
    for hour in range(24):
        bucket_time = base.replace(hour=hour, minute=0, second=0, microsecond=0)
        adjusted = await adjust_duration_matrix(
            db,
            coords=coords,
            durations=durations,
            at_time=bucket_time,
        )
        hourly[hour] = adjusted.durations
    return hourly


async def adjust_single_duration(
    db: AsyncSession,
    *,
    origin: tuple[float, float],
    destination: tuple[float, float],
    base_duration_s: int,
    at_time: datetime,
) -> tuple[int, float, bool]:
    """Adjust a single route duration using cell multipliers."""
    if (
        not settings.ENABLE_TRAFFIC_PROFILES
        or not settings.TRAFFIC_PROFILE_ENABLED
        or base_duration_s <= 0
    ):
        return base_duration_s, 1.0, False

    src_hash = encode_geohash(origin[0], origin[1], precision=6)
    dst_hash = encode_geohash(destination[0], destination[1], precision=6)
    lookup = await _load_multiplier_lookup(
        db,
        geohashes={src_hash, dst_hash},
        at_time=at_time,
    )
    src_multiplier = lookup.get(src_hash)
    dst_multiplier = lookup.get(dst_hash)
    if src_multiplier is None and dst_multiplier is None:
        return base_duration_s, 1.0, False

    if src_multiplier is not None and dst_multiplier is not None:
        multiplier = (src_multiplier + dst_multiplier) / 2.0
    else:
        multiplier = src_multiplier if src_multiplier is not None else dst_multiplier

    multiplier = _clamp_multiplier(float(multiplier))
    return max(1, round(base_duration_s * multiplier)), multiplier, True


async def record_observation(
    db: AsyncSession,
    *,
    lat: float,
    lon: float,
    observed_travel_s: int,
    planned_travel_s: int,
    observed_at: datetime,
) -> None:
    """Upsert traffic profile multiplier from observed/planned travel time."""
    if (
        not settings.ENABLE_TRAFFIC_PROFILES
        or not settings.TRAFFIC_PROFILE_ENABLED
        or observed_travel_s <= 0
        or planned_travel_s <= 0
    ):
        return

    local_dt = _normalize_datetime(observed_at)
    geohash6 = encode_geohash(lat, lon, precision=6)
    ratio = _clamp_multiplier(observed_travel_s / planned_travel_s)

    result = await db.execute(
        select(TrafficCellProfile).where(
            TrafficCellProfile.geohash6 == geohash6,
            TrafficCellProfile.day_of_week == local_dt.weekday(),
            TrafficCellProfile.hour_bucket == local_dt.hour,
        )
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = TrafficCellProfile(
            geohash6=geohash6,
            day_of_week=local_dt.weekday(),
            hour_bucket=local_dt.hour,
            sample_count=1,
            ema_multiplier=ratio,
            updated_at=utcnow_naive(),
        )
        db.add(profile)
    else:
        alpha = settings.TRAFFIC_PROFILE_EMA_ALPHA
        profile.sample_count += 1
        profile.ema_multiplier = _clamp_multiplier(
            alpha * ratio + (1.0 - alpha) * profile.ema_multiplier
        )
        profile.updated_at = utcnow_naive()

    await db.flush()
    invalidate_lookup_cache()
