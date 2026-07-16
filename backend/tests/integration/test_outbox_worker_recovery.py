"""Real PostgreSQL + Redis crash-recovery verification gate."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import subprocess
import sys
import unittest
from urllib.parse import urlsplit
import uuid

import asyncpg
from arq.connections import RedisSettings, create_pool
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.credit_transaction import CreditTransaction
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.order import Order, OrderStatus
from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.models.user import User
from app.services.job_lease_service import (
    JobRequiresReconciliation,
    StaleWorkerFence,
    claim_generation_job,
    complete_generation_job,
)
from app.services.outbox_service import publish_pending_generation_outbox


BACKEND_DIR = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


async def _create_database(base_url, database_name: str) -> None:
    connection = await asyncpg.connect(
        host=base_url.host,
        port=base_url.port or 5432,
        user=base_url.username,
        password=base_url.password,
        database="postgres",
    )
    try:
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()


async def _drop_database(base_url, database_name: str) -> None:
    connection = await asyncpg.connect(
        host=base_url.host,
        port=base_url.port or 5432,
        user=base_url.username,
        password=base_url.password,
        database="postgres",
    )
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            database_name,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
    finally:
        await connection.close()


@unittest.skipUnless(
    os.getenv("RUN_WORKER_INTEGRATION") == "1",
    "requires explicit real PostgreSQL and Redis integration environment",
)
class OutboxWorkerRecoveryIntegrationTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        raw_database_url = os.getenv("WORKER_INTEGRATION_DATABASE_URL", "").strip()
        raw_redis_url = os.getenv("WORKER_INTEGRATION_REDIS_URL", "").strip()
        if not raw_database_url or not raw_redis_url:
            raise RuntimeError(
                "WORKER_INTEGRATION_DATABASE_URL and WORKER_INTEGRATION_REDIS_URL are required"
            )

        base_url = make_url(raw_database_url)
        redis_url = urlsplit(raw_redis_url)
        if base_url.get_backend_name() != "postgresql":
            raise RuntimeError("worker integration database must be PostgreSQL")
        if redis_url.scheme not in {"redis", "rediss"}:
            raise RuntimeError("worker integration queue must use a Redis URL")
        if base_url.host not in LOCAL_HOSTS or redis_url.hostname not in LOCAL_HOSTS:
            raise RuntimeError("worker integration services must be local")
        if "test" not in str(base_url.database or "").lower():
            raise RuntimeError("worker integration PostgreSQL database must be test-only")

        cls.base_database_url = base_url
        cls.redis_url = raw_redis_url
        cls.database_name = f"vowpic_worker_it_{secrets.token_hex(6)}"
        temp_url = base_url.set(database=cls.database_name)
        cls.database_url = temp_url.render_as_string(hide_password=False)
        asyncio.run(_create_database(base_url, cls.database_name))

        migration_env = os.environ.copy()
        migration_env["DATABASE_URL"] = cls.database_url
        migration_env["PYTHONIOENCODING"] = "utf-8"
        try:
            migration = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=BACKEND_DIR,
                env=migration_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
        except Exception:
            asyncio.run(_drop_database(base_url, cls.database_name))
            cls.database_name = None
            raise
        if migration.returncode != 0:
            asyncio.run(_drop_database(base_url, cls.database_name))
            cls.database_name = None
            raise RuntimeError(
                "worker integration migration failed:\n"
                + (migration.stderr or migration.stdout)[-4000:]
            )

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "database_name", None):
            asyncio.run(_drop_database(cls.base_database_url, cls.database_name))
        super().tearDownClass()

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(self.database_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.queue_name = f"arq:queue:vowpic-worker-it:{secrets.token_hex(6)}"
        self.redis = await create_pool(
            RedisSettings.from_dsn(self.redis_url),
            default_queue_name=self.queue_name,
        )
        self.redis_job_ids: set[str] = set()

    async def asyncTearDown(self) -> None:
        try:
            if hasattr(self, "redis"):
                try:
                    keys: list[str] = [self.queue_name]
                    for job_id in self.redis_job_ids:
                        keys.extend(
                            (
                                f"arq:job:{job_id}",
                                f"arq:result:{job_id}",
                                f"arq:retry:{job_id}",
                            )
                        )
                    await self.redis.delete(*keys)
                finally:
                    await self.redis.aclose()
        finally:
            if hasattr(self, "engine"):
                await self.engine.dispose()

    async def test_commit_publish_and_worker_restart_are_exactly_once_at_submission_boundary(self) -> None:
        started_at = datetime.now(timezone.utc)
        user_id = uuid.uuid4()
        order_id = uuid.uuid4()
        job_id = uuid.uuid4()
        event_id = uuid.uuid4()
        correlation_id = uuid.uuid4()
        redis_job_id = f"generation:v1:{job_id}"
        self.redis_job_ids.add(redis_job_id)

        async with self.sessions() as db:
            db.add(User(id=user_id, role="user", status="active"))
            await db.flush()
            db.add(
                Order(
                    id=order_id,
                    user_id=user_id,
                    status=OrderStatus.QUEUED,
                    price_cents=0,
                )
            )
            await db.flush()
            job = GenerationJob.queued(
                order_id=order_id,
                submission_correlation_id=correlation_id,
                api_deployment_id="dpl_worker_integration",
                runtime_bundle_id="rtb_" + "a" * 64,
                expected_worker_image_digest="sha256:" + "b" * 64,
            )
            job.id = job_id
            db.add(job)
            db.add(
                OutboxEvent(
                    id=event_id,
                    aggregate_type="generation_job",
                    aggregate_id=job_id,
                    event_type="GENERATION_JOB_CREATED",
                    dedupe_key=redis_job_id,
                    payload_version="generation-job.v1",
                    payload_json={
                        "job_id": str(job_id),
                        "payload_version": "generation-job.v1",
                    },
                    status=OutboxEventStatus.PENDING,
                    attempt_count=0,
                    next_attempt_at=started_at,
                    fencing_token=0,
                )
            )
            await db.commit()

        # Redis accepts the deterministic job, then the dispatcher process dies
        # before PostgreSQL commits its DISPATCHED state.
        async with self.sessions() as crashed_dispatcher:
            first_publish = await publish_pending_generation_outbox(
                crashed_dispatcher,
                self.redis,
                now=started_at + timedelta(seconds=1),
            )
            self.assertEqual(first_publish.dispatched_event_ids, (event_id,))
            await crashed_dispatcher.rollback()

        async with self.sessions() as db:
            event = await db.get(OutboxEvent, event_id)
            self.assertEqual(event.status, OutboxEventStatus.PENDING)

        # The restarted dispatcher sees PENDING again. ARQ rejects the duplicate
        # job ID, while PostgreSQL can now durably mark the event DISPATCHED.
        async with self.sessions() as restarted_dispatcher:
            second_publish = await publish_pending_generation_outbox(
                restarted_dispatcher,
                self.redis,
                now=started_at + timedelta(seconds=2),
            )
            self.assertEqual(second_publish.dispatched_event_ids, (event_id,))
            await restarted_dispatcher.commit()

        queued = await self.redis.queued_jobs(queue_name=self.queue_name)
        matching_jobs = [queued_job for queued_job in queued if queued_job.job_id == redis_job_id]
        self.assertEqual(len(matching_jobs), 1)
        self.assertEqual(matching_jobs[0].function, "generate_order_v1")
        self.assertEqual(matching_jobs[0].args, (str(job_id), "generation-job.v1"))

        worker_claim_id = uuid.uuid4()
        async with self.sessions() as first_worker:
            lease = await claim_generation_job(
                first_worker,
                job_id=job_id,
                worker_id="worker-before-crash",
                claim_id=worker_claim_id,
                now=started_at + timedelta(seconds=3),
            )
            await first_worker.commit()

        provider_submission_count = 0
        async with self.sessions() as first_worker:
            job = await first_worker.get(GenerationJob, job_id)
            attempt = GenerationAttempt.prepared(
                job=job,
                attempt_number=1,
                kind=GenerationAttemptKind.INITIAL,
                provider="evolink",
            )
            first_worker.add(attempt)
            await first_worker.flush()
            attempt.status = GenerationAttemptStatus.SUBMITTING
            attempt.submit_started_at = started_at + timedelta(seconds=4)
            await first_worker.commit()
            provider_submission_count += 1

        # The Worker disappears after the durable SUBMITTING boundary. Expire
        # its lease without waiting, then redeliver the same Redis job.
        async with self.sessions() as db:
            job = await db.get(GenerationJob, job_id)
            job.lease_expires_at = started_at + timedelta(seconds=5)
            job.heartbeat_at = started_at + timedelta(seconds=4)
            await db.commit()

        async with self.sessions() as restarted_worker:
            with self.assertRaises(JobRequiresReconciliation):
                await claim_generation_job(
                    restarted_worker,
                    job_id=job_id,
                    worker_id="worker-after-crash",
                    claim_id=uuid.uuid4(),
                    now=started_at + timedelta(seconds=6),
                )
            await restarted_worker.commit()

        self.assertEqual(provider_submission_count, 1)
        async with self.sessions() as db:
            job = await db.get(GenerationJob, job_id)
            attempt = await db.scalar(
                select(GenerationAttempt).where(GenerationAttempt.job_id == job_id)
            )
            transaction_count = await db.scalar(
                select(func.count(CreditTransaction.id)).where(
                    CreditTransaction.user_id == user_id
                )
            )
            self.assertEqual(job.status, GenerationJobStatus.RECONCILING)
            self.assertIsNone(job.lease_owner)
            self.assertEqual(attempt.status, GenerationAttemptStatus.SUBMITTING)
            self.assertEqual(transaction_count, 0)

            with self.assertRaises(StaleWorkerFence):
                await complete_generation_job(
                    db,
                    job_id=job_id,
                    worker_id=lease.worker_id,
                    claim_id=lease.claim_id,
                    fencing_token=lease.fencing_token,
                    terminal_status=GenerationJobStatus.FINISHED,
                    now=started_at + timedelta(seconds=7),
                )


if __name__ == "__main__":
    unittest.main()
