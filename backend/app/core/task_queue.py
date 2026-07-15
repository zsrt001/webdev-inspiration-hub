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


async def enqueue_generation_job(job_id: str, payload_version: str) -> str:
    """Enqueue the only supported IDs-only generation message."""
    from app.models.generation_job import GENERATION_JOB_PAYLOAD_VERSION

    if payload_version != GENERATION_JOB_PAYLOAD_VERSION:
        raise ValueError("generation_job_payload_version_unsupported")
    try:
        normalized_job_id = str(__import__("uuid").UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("generation_job_id_invalid") from exc
    pool = await get_pool()
    redis_job_id = f"generation:v1:{normalized_job_id}"
    job = await pool.enqueue_job(
        "generate_order_v1",
        normalized_job_id,
        GENERATION_JOB_PAYLOAD_VERSION,
        _job_id=redis_job_id,
    )
    return redis_job_id if job is None else str(job.job_id)
