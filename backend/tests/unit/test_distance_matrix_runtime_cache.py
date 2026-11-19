import json

import pytest

from app.algorithms import osrm_client


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload: dict):
        self._payload = payload
        self.calls = 0

    async def get(self, _url):
        self.calls += 1
        return _FakeResponse(self._payload)


class _FakeRedis:
    def __init__(self, cached_value: str | None):
        self.cached_value = cached_value
        self.get_calls = 0
        self.set_calls = 0

    async def get(self, _key):
        self.get_calls += 1
        return self.cached_value

    async def set(self, _key, _value, ex):
        self.set_calls += 1
        return True


@pytest.mark.asyncio
async def test_distance_matrix_uses_local_runtime_cache_after_first_call(monkeypatch):
    await osrm_client.clear_distance_matrix_local_cache()

    payload = {
        "code": "Ok",
        "distances": [[0, 1000], [1000, 0]],
        "durations": [[0, 120], [120, 0]],
    }
    fake_http = _FakeHttpClient(payload)
    fake_redis = _FakeRedis(cached_value=None)

    async def _mock_get_http_client(profile: str = "driving"):
        return fake_http

    async def _mock_get_redis():
        return fake_redis

    monkeypatch.setattr(osrm_client, "_get_http_client", _mock_get_http_client)
    monkeypatch.setattr(osrm_client, "_get_redis", _mock_get_redis)

    coords = [(41.0, 29.0), (41.01, 29.01)]
    first = await osrm_client.get_distance_matrix(coords)
    second = await osrm_client.get_distance_matrix(coords)

    assert first.source == "osrm"
    assert second.source == "cache"
    assert fake_http.calls == 1
    assert fake_redis.get_calls == 1


@pytest.mark.asyncio
async def test_distance_matrix_ignores_invalid_redis_payload_shape(monkeypatch):
    await osrm_client.clear_distance_matrix_local_cache()

    malformed_cache = json.dumps(
        {
            "distances": [[0, 1000, 2000]],
            "durations": [[0, 120]],
        }
    )
    fake_redis = _FakeRedis(cached_value=malformed_cache)
    payload = {
        "code": "Ok",
        "distances": [[0, 1000], [1000, 0]],
        "durations": [[0, 120], [120, 0]],
    }
    fake_http = _FakeHttpClient(payload)

    async def _mock_get_http_client(profile: str = "driving"):
        return fake_http

    async def _mock_get_redis():
        return fake_redis

    monkeypatch.setattr(osrm_client, "_get_http_client", _mock_get_http_client)
    monkeypatch.setattr(osrm_client, "_get_redis", _mock_get_redis)

    coords = [(41.0, 29.0), (41.01, 29.01)]
    result = await osrm_client.get_distance_matrix(coords)

    assert result.source == "osrm"
    assert fake_http.calls == 1
