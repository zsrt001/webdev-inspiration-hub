"""ARQ settings for the dedicated durable Worker image."""

from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.worker_tasks import (
    dispatch_generation_outbox,
    dispatch_generation_reconciliation,
    generate_attempt_v1,
    generate_order_v1,
    publish_worker_heartbeat,
    reconcile_generation_v1,
    startup_worker,
)


settings = get_settings()


class WorkerSettings:
    functions = [generate_order_v1, generate_attempt_v1, reconcile_generation_v1]
    cron_jobs = [
        cron(
            dispatch_generation_outbox,
            second={0, 10, 20, 30, 40, 50},
            run_at_startup=True,
            unique=True,
        ),
        cron(
            publish_worker_heartbeat,
            second={5, 35},
            unique=True,
        ),
        cron(
            dispatch_generation_reconciliation,
            second={3, 8, 13, 18, 23, 28, 33, 38, 43, 48, 53, 58},
            run_at_startup=True,
            unique=True,
        ),
    ]
    on_startup = startup_worker
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = settings.generation_poll_timeout + 180
    # PostgreSQL retry and reconciliation facts are authoritative; ARQ must not
    # create an independent retry history.
    max_tries = 1
