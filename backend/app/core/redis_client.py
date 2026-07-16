"""Async Redis client (shared utilities)."""

from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.core.config import get_settings

settings = get_settings()

_redis: Redis | None = None
FEATURE_FLAG_OFF_CACHE_TTL_SECONDS = 30


def feature_flag_off_cache_key(environment: str, capability: str) -> str:
    """Return the only cache namespace allowed for capability decisions."""
    return f"ops:feature-flag:off:{environment}:{capability}"


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = from_url(settings.redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
