import pytest
from fastapi import HTTPException

from app.utils import rate_limit


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        next_count = self.counts.get(key, 0) + 1
        self.counts[key] = next_count
        return next_count

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations.append((key, seconds))


@pytest.mark.asyncio
async def test_enforce_fixed_window_limit_with_redis(monkeypatch):
    fake_redis = FakeRedis()

    async def _fake_get_redis():
        return fake_redis

    monkeypatch.setattr(rate_limit, "_get_redis", _fake_get_redis)
    rate_limit._redis_fallback_active = False
    rate_limit._redis_retry_after_ts = 0.0

    await rate_limit.enforce_fixed_window_limit(key="rl:redis", limit=2, window_seconds=30)
    await rate_limit.enforce_fixed_window_limit(key="rl:redis", limit=2, window_seconds=30)

    with pytest.raises(HTTPException) as exc:
        await rate_limit.enforce_fixed_window_limit(key="rl:redis", limit=2, window_seconds=30)

    assert exc.value.status_code == 429
    assert fake_redis.expirations == [("rl:redis", 30)]


@pytest.mark.asyncio
async def test_enforce_fixed_window_limit_falls_back_when_redis_unavailable(monkeypatch):
    async def _broken_get_redis():
        raise RuntimeError("redis down")

    monkeypatch.setattr(rate_limit, "_get_redis", _broken_get_redis)
    rate_limit._fallback_windows.clear()
    rate_limit._redis_fallback_active = False
    rate_limit._redis_retry_after_ts = 0.0

    await rate_limit.enforce_fixed_window_limit(key="rl:fallback", limit=2, window_seconds=60)
    await rate_limit.enforce_fixed_window_limit(key="rl:fallback", limit=2, window_seconds=60)

    with pytest.raises(HTTPException) as exc:
        await rate_limit.enforce_fixed_window_limit(key="rl:fallback", limit=2, window_seconds=60)

    assert exc.value.status_code == 429
    assert rate_limit._redis_fallback_active is True


@pytest.mark.asyncio
async def test_enforce_fixed_window_limit_skips_redis_retries_during_cooldown(monkeypatch):
    redis_calls = 0

    async def _broken_get_redis():
        nonlocal redis_calls
        redis_calls += 1
        raise RuntimeError("redis down")

    monkeypatch.setattr(rate_limit, "_get_redis", _broken_get_redis)
    rate_limit._fallback_windows.clear()
    rate_limit._redis_fallback_active = False
    rate_limit._redis_retry_after_ts = 0.0

    await rate_limit.enforce_fixed_window_limit(key="rl:cooldown", limit=5, window_seconds=60)
    await rate_limit.enforce_fixed_window_limit(key="rl:cooldown", limit=5, window_seconds=60)

    assert redis_calls == 1
