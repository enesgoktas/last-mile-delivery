from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import traffic_profile_service
from app.utils.compat import UTC


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.execute_calls = 0

    async def execute(self, _query):
        self.execute_calls += 1
        return _FakeResult(self._rows)


@pytest.fixture(autouse=True)
def clear_lookup_cache():
    traffic_profile_service.invalidate_lookup_cache()
    yield
    traffic_profile_service.invalidate_lookup_cache()


@pytest.mark.asyncio
async def test_load_multiplier_lookup_prunes_expired_entries(monkeypatch):
    at_time = datetime(2026, 1, 5, 10, tzinfo=UTC)
    fake_db = _FakeSession(
        [
            SimpleNamespace(
                geohash6="abc123",
                ema_multiplier=1.2,
                sample_count=traffic_profile_service.settings.TRAFFIC_PROFILE_MIN_SAMPLES,
            )
        ]
    )

    traffic_profile_service._profile_lookup_cache["expired"] = (99.0, {"old": 1.1})
    traffic_profile_service._profile_lookup_cache["fresh"] = (130.0, {"keep": 1.0})
    monkeypatch.setattr(traffic_profile_service.time, "time", lambda: 100.0)

    lookup = await traffic_profile_service._load_multiplier_lookup(
        fake_db,
        geohashes={"abc123"},
        at_time=at_time,
    )

    assert lookup == {"abc123": 1.2}
    assert fake_db.execute_calls == 1
    assert "expired" not in traffic_profile_service._profile_lookup_cache
    assert "fresh" in traffic_profile_service._profile_lookup_cache


@pytest.mark.asyncio
async def test_load_multiplier_lookup_evicts_oldest_entries_when_cache_is_full(monkeypatch):
    at_time = datetime(2026, 1, 5, 11, tzinfo=UTC)
    fake_db = _FakeSession(
        [
            SimpleNamespace(
                geohash6="new123",
                ema_multiplier=1.15,
                sample_count=traffic_profile_service.settings.TRAFFIC_PROFILE_MIN_SAMPLES,
            )
        ]
    )

    monkeypatch.setattr(traffic_profile_service, "_PROFILE_LOOKUP_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(traffic_profile_service.time, "time", lambda: 100.0)
    traffic_profile_service._profile_lookup_cache["oldest"] = (130.0, {"a": 1.0})
    traffic_profile_service._profile_lookup_cache["newer"] = (130.0, {"b": 1.0})

    await traffic_profile_service._load_multiplier_lookup(
        fake_db,
        geohashes={"new123"},
        at_time=at_time,
    )

    expected_key = traffic_profile_service._cache_key(
        {"new123"},
        at_time,
        traffic_profile_service.settings.TRAFFIC_PRIOR_ACTIVE_REGION_CODE,
    )
    assert list(traffic_profile_service._profile_lookup_cache.keys()) == ["newer", expected_key]
    assert fake_db.execute_calls == 1
