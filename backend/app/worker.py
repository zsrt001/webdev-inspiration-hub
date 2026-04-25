"""ARQ worker settings entrypoint.

Run in `backend/` directory:
  arq app.worker.WorkerSettings
"""

from __future__ import annotations

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.worker_tasks import generate_order, generate_live_portrait

settings = get_settings()


class WorkerSettings:
    functions = [generate_order, generate_live_portrait]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    # Generation can be slow; keep generous timeouts.
    job_timeout = settings.generation_poll_timeout + 60
    max_tries = max(1, settings.generation_max_retries + 1)
