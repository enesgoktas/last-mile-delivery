import uuid
from types import SimpleNamespace

import pytest

from app.schemas.courier import LocationUpdate
from app.services import courier_service


class FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None


class _RedisMiss:
    async def get(self, _key: str):
        return None


@pytest.mark.asyncio
async def test_get_courier_location_from_cache_returns_none_when_redis_unavailable(monkeypatch):
    async def _broken_get_redis():
        raise RuntimeError("redis down")

    monkeypatch.setattr(courier_service, "get_redis", _broken_get_redis)
    result = await courier_service.get_courier_location_from_cache(uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_courier_location_falls_back_to_db_on_cache_miss(monkeypatch):
    courier_id = uuid.uuid4()

    async def _mock_get_redis():
        return _RedisMiss()

    async def _mock_load_latest_location(_db, requested_courier_id):
        assert requested_courier_id == courier_id
        return {
            "lat": 41.015,
            "lon": 29.025,
            "heading": 90.0,
            "speed_kmh": 18.0,
            "updated_at": "2026-02-24T10:00:00",
        }

    monkeypatch.setattr(courier_service, "get_redis", _mock_get_redis)
    monkeypatch.setattr(courier_service, "_load_latest_location_from_db", _mock_load_latest_location)

    result = await courier_service.get_courier_location_from_cache(
        courier_id,
        db=SimpleNamespace(),
    )
    assert result is not None
    assert result["lat"] == 41.015
    assert result["lon"] == 29.025


@pytest.mark.asyncio
async def test_get_courier_location_falls_back_to_db_when_redis_is_down(monkeypatch):
    courier_id = uuid.uuid4()

    async def _broken_get_redis():
        raise RuntimeError("redis down")

    async def _mock_load_latest_location(_db, requested_courier_id):
        assert requested_courier_id == courier_id
        return {
            "lat": 40.99,
            "lon": 29.11,
            "heading": None,
            "speed_kmh": None,
            "updated_at": "2026-02-24T10:05:00",
        }

    monkeypatch.setattr(courier_service, "get_redis", _broken_get_redis)
    monkeypatch.setattr(courier_service, "_load_latest_location_from_db", _mock_load_latest_location)

    result = await courier_service.get_courier_location_from_cache(
        courier_id,
        db=SimpleNamespace(),
    )
    assert result is not None
    assert result["lat"] == 40.99
    assert result["lon"] == 29.11


@pytest.mark.asyncio
async def test_update_location_continues_when_cache_write_fails(monkeypatch):
    courier_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    db = FakeDB()

    async def _mock_get_courier(_db, requested_courier_id):
        assert requested_courier_id == courier_id
        return SimpleNamespace(id=courier_id, tenant_id=tenant_id)

    async def _broken_get_redis():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(courier_service, "get_courier", _mock_get_courier)
    monkeypatch.setattr(courier_service, "get_redis", _broken_get_redis)

    monkeypatch.setattr(courier_service, "enqueue_task", lambda *_args, **_kwargs: None)

    payload = LocationUpdate(
        lat=41.02,
        lon=29.01,
        heading=180.0,
        speed_kmh=24.0,
        accuracy_m=5.0,
    )

    response = await courier_service.update_location(db, courier_id, payload)
    assert response["status"] == "ok"
    assert response["cache_status"] == "unavailable"
    assert response["geofence_status"] == "enqueued"
    assert len(db.added) == 1
