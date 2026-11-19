"""Tests for the fleet-first + prior-fallback merge in `_load_multiplier_lookup`.

The fleet path is exercised via a fake session that returns SimpleNamespace
rows. The prior path is patched at the `lookup_prior_multipliers` boundary
to keep these tests pure-Python (no DuckDB, no DB).

Also covers `record_observation` timezone bucketing — after the UTC→Istanbul
TZ flip, observations must land on the locally-correct (day_of_week, hour)
bucket.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import traffic_profile_service
from app.utils.compat import UTC


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeFleetResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, fleet_rows):
        self._fleet_rows = fleet_rows
        self.execute_calls = 0

    async def execute(self, _stmt):
        self.execute_calls += 1
        return _FakeFleetResult(self._fleet_rows)


@pytest.fixture(autouse=True)
def _clear_cache():
    traffic_profile_service.invalidate_lookup_cache()
    yield
    traffic_profile_service.invalidate_lookup_cache()


@pytest.fixture
def enable_prior(monkeypatch):
    monkeypatch.setattr(
        traffic_profile_service.settings, "TRAFFIC_PRIOR_ENABLED", True
    )


@pytest.fixture
def at_time():
    return datetime(2026, 1, 5, 10, tzinfo=UTC)


@pytest.mark.asyncio
async def test_fleet_wins_over_prior_for_same_cell(monkeypatch, enable_prior, at_time):
    fake_db = _FakeSession(
        [
            SimpleNamespace(
                geohash6="sxk001",
                ema_multiplier=1.30,
                sample_count=traffic_profile_service.settings.TRAFFIC_PROFILE_MIN_SAMPLES,
            )
        ]
    )
    prior_mock = AsyncMock(return_value={"sxk001": 1.10})
    monkeypatch.setattr(
        "app.services.traffic_prior_service.lookup_prior_multipliers", prior_mock
    )

    lookup = await traffic_profile_service._load_multiplier_lookup(
        fake_db, geohashes={"sxk001"}, at_time=at_time,
    )

    assert lookup == {"sxk001": 1.30}
    # Prior was NOT consulted because fleet covered the cell.
    prior_mock.assert_not_called()


@pytest.mark.asyncio
async def test_prior_fills_gaps_for_fleet_uncovered_cells(monkeypatch, enable_prior, at_time):
    fake_db = _FakeSession(
        [
            SimpleNamespace(
                geohash6="sxk001",
                ema_multiplier=1.30,
                sample_count=traffic_profile_service.settings.TRAFFIC_PROFILE_MIN_SAMPLES,
            )
        ]
    )
    prior_mock = AsyncMock(return_value={"sxk002": 1.15})
    monkeypatch.setattr(
        "app.services.traffic_prior_service.lookup_prior_multipliers", prior_mock
    )

    lookup = await traffic_profile_service._load_multiplier_lookup(
        fake_db,
        geohashes={"sxk001", "sxk002"},
        at_time=at_time,
    )

    assert lookup == {"sxk001": 1.30, "sxk002": 1.15}
    prior_mock.assert_awaited_once()
    # Prior call was made only for the uncovered geohash.
    args, kwargs = prior_mock.await_args
    assert kwargs["geohashes"] == {"sxk002"}


@pytest.mark.asyncio
async def test_fleet_under_min_samples_falls_back_to_prior(monkeypatch, enable_prior, at_time):
    """A row exists in the fleet table but below min_samples — should not block
    the prior fallback."""
    fake_db = _FakeSession(
        [
            SimpleNamespace(
                geohash6="sxk001",
                ema_multiplier=1.30,
                sample_count=traffic_profile_service.settings.TRAFFIC_PROFILE_MIN_SAMPLES - 1,
            )
        ]
    )
    prior_mock = AsyncMock(return_value={"sxk001": 1.10})
    monkeypatch.setattr(
        "app.services.traffic_prior_service.lookup_prior_multipliers", prior_mock
    )

    lookup = await traffic_profile_service._load_multiplier_lookup(
        fake_db, geohashes={"sxk001"}, at_time=at_time,
    )

    assert lookup == {"sxk001": 1.10}
    prior_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_flag_skips_prior_lookup_entirely(monkeypatch, at_time):
    monkeypatch.setattr(
        traffic_profile_service.settings, "TRAFFIC_PRIOR_ENABLED", False
    )
    fake_db = _FakeSession([])
    prior_mock = AsyncMock(return_value={"sxk001": 1.10})
    monkeypatch.setattr(
        "app.services.traffic_prior_service.lookup_prior_multipliers", prior_mock
    )

    lookup = await traffic_profile_service._load_multiplier_lookup(
        fake_db, geohashes={"sxk001"}, at_time=at_time,
    )

    assert lookup == {}
    prior_mock.assert_not_called()


class _RecordObservationSession:
    """Fake session that captures what `record_observation` upserts.

    `execute(select(...))` returns no existing row (insert path).
    `add(profile)` captures the new TrafficCellProfile.
    `flush()` and `commit()` are no-ops.
    """

    def __init__(self):
        self.added: list = []
        self.flushes = 0

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1


@pytest.mark.asyncio
async def test_record_observation_buckets_utc_into_istanbul_local():
    """Tue 2026-05-26 15:30 UTC should land on (dow=1=Tuesday, hour=18) Istanbul.

    Catches a regression where the function would silently bucket on UTC
    hour 15 (a non-rush quiet slot) instead of Istanbul rush hour 18.
    """
    fake_db = _RecordObservationSession()
    observed_at = datetime(2026, 5, 26, 15, 30, tzinfo=UTC)

    await traffic_profile_service.record_observation(
        fake_db,
        lat=41.0082,
        lon=28.9784,
        observed_travel_s=720,
        planned_travel_s=600,
        observed_at=observed_at,
    )

    assert len(fake_db.added) == 1, "expected exactly one insert"
    profile = fake_db.added[0]
    assert profile.day_of_week == 1, "Tuesday in Istanbul (UTC+3)"
    assert profile.hour_bucket == 18, "15:30 UTC + 3h = 18:30 Istanbul → hour bucket 18"
    assert profile.sample_count == 1
    # ratio = 720/600 = 1.2; within clamp.
    assert profile.ema_multiplier == pytest.approx(1.2)


@pytest.mark.asyncio
async def test_record_observation_handles_midnight_crossing_into_next_day():
    """Sun 2026-05-24 21:30 UTC should bucket as Monday hour=0 in Istanbul.

    21:30 UTC + 3h = 00:30 Mon Istanbul. weekday() is 0 (Monday), hour is 0.
    This is the trickiest case — wrong TZ handling would put it on Sunday hour 21.
    """
    fake_db = _RecordObservationSession()
    observed_at = datetime(2026, 5, 24, 21, 30, tzinfo=UTC)

    await traffic_profile_service.record_observation(
        fake_db,
        lat=41.0082,
        lon=28.9784,
        observed_travel_s=500,
        planned_travel_s=500,
        observed_at=observed_at,
    )

    assert len(fake_db.added) == 1
    profile = fake_db.added[0]
    assert profile.day_of_week == 0, "Sunday 21:30 UTC → Monday 00:30 Istanbul"
    assert profile.hour_bucket == 0, "Monday hour 0 in Istanbul"


@pytest.mark.asyncio
async def test_record_observation_handles_naive_utc_datetime():
    """A naive (no tzinfo) datetime must be interpreted as UTC, not local.

    The codebase historically stores naive UTC timestamps (utcnow_naive());
    the bucketing code in `_normalize_datetime` handles this.
    """
    fake_db = _RecordObservationSession()
    observed_at = datetime(2026, 5, 26, 15, 30)  # naive — treated as UTC

    await traffic_profile_service.record_observation(
        fake_db,
        lat=41.0082,
        lon=28.9784,
        observed_travel_s=720,
        planned_travel_s=600,
        observed_at=observed_at,
    )

    assert len(fake_db.added) == 1
    profile = fake_db.added[0]
    assert profile.day_of_week == 1  # Tuesday
    assert profile.hour_bucket == 18  # 15:30 naive UTC → 18:30 Istanbul


@pytest.mark.asyncio
async def test_cache_key_isolates_per_region(monkeypatch, enable_prior, at_time):
    fake_db = _FakeSession([])
    prior_mock = AsyncMock(return_value={})
    monkeypatch.setattr(
        "app.services.traffic_prior_service.lookup_prior_multipliers", prior_mock
    )

    # First call with default region IST
    await traffic_profile_service._load_multiplier_lookup(
        fake_db, geohashes={"sxk001"}, at_time=at_time,
    )
    # Second call with explicit region CAI
    await traffic_profile_service._load_multiplier_lookup(
        fake_db, geohashes={"sxk001"}, at_time=at_time, region_code="CAI",
    )

    keys = list(traffic_profile_service._profile_lookup_cache.keys())
    assert len(keys) == 2, "different regions should produce different cache entries"
    assert any(k.startswith("IST:") for k in keys)
    assert any(k.startswith("CAI:") for k in keys)
