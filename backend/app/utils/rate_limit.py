import asyncio
import logging
import time

import redis.asyncio as aioredis
from fastapi import HTTPException, status

from app.config import settings

_redis: aioredis.Redis | None = None
_fallback_windows: dict[str, tuple[int, float]] = {}
_fallback_lock = asyncio.Lock()
_redis_fallback_active = False
_redis_retry_after_ts = 0.0
logger = logging.getLogger(__name__)


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def _fallback_increment(*, key: str, window_seconds: int) -> int:
    now = time.time()
    async with _fallback_lock:
        count, expires_at = _fallback_windows.get(key, (0, now + window_seconds))
        if now >= expires_at:
            count = 0
            expires_at = now + window_seconds
        count += 1
        _fallback_windows[key] = (count, expires_at)

        if len(_fallback_windows) > 2000:
            stale_keys = [k for k, (_c, exp) in _fallback_windows.items() if exp <= now]
            for stale_key in stale_keys[:500]:
                _fallback_windows.pop(stale_key, None)
        return count


async def enforce_fixed_window_limit(
    *,
    key: str,
    limit: int,
    window_seconds: int = 60,
) -> None:
    global _redis_fallback_active, _redis_retry_after_ts
    now_ts = time.time()

    if _redis_fallback_active and now_ts < _redis_retry_after_ts:
        current = await _fallback_increment(key=key, window_seconds=window_seconds)
    else:
        try:
            r = await _get_redis()
            current = await r.incr(key)
            if current == 1:
                await r.expire(key, window_seconds)
            if _redis_fallback_active:
                logger.info("Rate limiter Redis connection restored; fallback disabled.")
                _redis_fallback_active = False
                _redis_retry_after_ts = 0.0
        except Exception:
            if not _redis_fallback_active:
                logger.warning("Rate limiter Redis unavailable; using in-memory fallback.")
            _redis_fallback_active = True
            _redis_retry_after_ts = now_ts + settings.REDIS_FALLBACK_RETRY_SECONDS
            current = await _fallback_increment(key=key, window_seconds=window_seconds)

    if current > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )
