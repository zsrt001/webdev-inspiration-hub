"""Task queue helpers (ARQ + Redis)."""

from __future__ import annotations

import logging

from arq.connections import ArqRedis, RedisSettings, create_pool

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        _pool = await create_pool(redis_settings)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue_generate_order(order_id: str) -> str:
    """
    Enqueue generation job.

    Returns:
        job_id
    """
    pool = await get_pool()
    job = await pool.enqueue_job("generate_order", order_id)
    return job.job_id


async def enqueue_live_portrait(job_id: str) -> str:
    """
    Enqueue Live Portrait generation job.

    Returns:
        job_id
    """
    pool = await get_pool()
    job = await pool.enqueue_job("generate_live_portrait", job_id)
    return job.job_id
