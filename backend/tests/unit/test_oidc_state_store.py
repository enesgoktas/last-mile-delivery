import uuid

import pytest
from fastapi import HTTPException

from app.api import oidc as oidc_api


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int]] = []

    async def set(self, key: str, value: str, ex: int) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)


@pytest.mark.asyncio
async def test_store_oidc_state_uses_redis_ttl(monkeypatch):
    fake_redis = _FakeRedis()

    async def _fake_get_state_redis():
        return fake_redis

    monkeypatch.setattr(oidc_api, "_get_state_redis", _fake_get_state_redis)
    monkeypatch.setattr(oidc_api.settings, "OIDC_STATE_TTL_SECONDS", 321)

    await oidc_api._store_oidc_state(state="state-123", tenant_id="tenant-456")

    assert fake_redis.set_calls == [("oidc:state:state-123", "tenant-456", 321)]


@pytest.mark.asyncio
async def test_consume_oidc_state_returns_tenant_id(monkeypatch):
    tenant_id = uuid.uuid4()
    fake_redis = _FakeRedis()
    fake_redis.values["oidc:state:state-123"] = str(tenant_id)

    async def _fake_get_state_redis():
        return fake_redis

    monkeypatch.setattr(oidc_api, "_get_state_redis", _fake_get_state_redis)

    assert await oidc_api._consume_oidc_state("state-123") == tenant_id
    assert fake_redis.values == {}


@pytest.mark.asyncio
async def test_consume_oidc_state_rejects_missing_state(monkeypatch):
    fake_redis = _FakeRedis()

    async def _fake_get_state_redis():
        return fake_redis

    monkeypatch.setattr(oidc_api, "_get_state_redis", _fake_get_state_redis)

    with pytest.raises(HTTPException, match="Invalid or expired OIDC state parameter") as exc_info:
        await oidc_api._consume_oidc_state("missing")

    assert exc_info.value.status_code == 400
