"""Durable generation Worker lease, recovery, and fencing contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import sys
import unittest
import uuid

from sqlalchemy.dialects import postgresql


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.generation_attempt import GenerationAttemptStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.services.job_lease_service import (
    JobAlreadyLeased,
    JobRequiresReconciliation,
    StaleWorkerFence,
    claim_generation_reconciliation,
    claim_generation_job,
    complete_generation_job,
    pause_generation_reconciliation,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _job() -> GenerationJob:
    return GenerationJob(
        id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        submission_correlation_id=uuid.uuid4(),
        status=GenerationJobStatus.QUEUED,
        retry_count=0,
        repair_count=0,
        fencing_token=0,
        payload_version="generation-job.v1",
        api_deployment_id="dpl_api",
        runtime_bundle_id="rtb_" + "a" * 64,
        expected_worker_image_digest="sha256:" + "b" * 64,
        settlement_status="RESERVED",
        delivery_status="PENDING",
    )


class _ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class _LeaseDb:
    def __init__(self, job: GenerationJob, attempt_statuses=()):
        self.job = job
        self.attempt_statuses = list(attempt_statuses)
        self.scalar_statements = []
        self.scalars_statements = []

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        return self.job

    async def scalars(self, statement):
        self.scalars_statements.append(statement)
        return _ScalarRows(self.attempt_statuses)


class WorkerLeaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_claim_replay_does_not_increment_fence(self) -> None:
        job = _job()
        db = _LeaseDb(job)
        claim_id = uuid.uuid4()

        first = await claim_generation_job(
            db, job_id=job.id, worker_id="worker-a", claim_id=claim_id, now=NOW
        )
        second = await claim_generation_job(
            db,
            job_id=job.id,
            worker_id="worker-a",
            claim_id=claim_id,
            now=NOW + timedelta(seconds=30),
        )

        self.assertEqual(first.fencing_token, 1)
        self.assertEqual(second.fencing_token, 1)
        self.assertEqual(second.lease_expires_at, first.lease_expires_at)

    async def test_distinct_claim_cannot_steal_unexpired_lease(self) -> None:
        job = _job()
        db = _LeaseDb(job)
        await claim_generation_job(
            db, job_id=job.id, worker_id="worker-a", claim_id=uuid.uuid4(), now=NOW
        )

        with self.assertRaises(JobAlreadyLeased):
            await claim_generation_job(
                db,
                job_id=job.id,
                worker_id="worker-a",
                claim_id=uuid.uuid4(),
                now=NOW + timedelta(seconds=30),
            )

    async def test_submitting_work_moves_to_reconciliation_after_expiry(self) -> None:
        job = _job()
        job.status = GenerationJobStatus.ACTIVE
        job.lease_owner = "dead-worker"
        job.lease_claim_id = uuid.uuid4()
        job.fencing_token = 7
        job.lease_expires_at = NOW - timedelta(seconds=1)
        job.heartbeat_at = NOW - timedelta(seconds=121)
        db = _LeaseDb(job, [GenerationAttemptStatus.SUBMITTING])

        with self.assertRaises(JobRequiresReconciliation):
            await claim_generation_job(
                db, job_id=job.id, worker_id="worker-b", claim_id=uuid.uuid4(), now=NOW
            )

        self.assertEqual(job.status, GenerationJobStatus.RECONCILING)
        self.assertIsNone(job.lease_owner)
        self.assertIsNone(job.lease_claim_id)
        self.assertEqual(job.next_retry_at, NOW)

    async def test_worker_commits_reconciliation_routing_before_returning(self) -> None:
        from app import worker_tasks

        job = _job()
        db = SimpleNamespace(commit=AsyncMock())

        class SessionContext:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *_args):
                return False

        async def route_to_reconciliation(*_args, **_kwargs):
            job.status = GenerationJobStatus.RECONCILING
            raise JobRequiresReconciliation("requires_reconciliation", job.id)

        with (
            patch.object(
                worker_tasks,
                "_load_capability_context",
                AsyncMock(return_value=(job, uuid.uuid4())),
            ),
            patch.object(worker_tasks, "async_session_maker", return_value=SessionContext()),
            patch.object(worker_tasks, "claim_generation_job", side_effect=route_to_reconciliation),
        ):
            await worker_tasks.generate_order_v1(
                {"worker_id": "worker-a"},
                str(job.id),
                "generation-job.v1",
            )

        db.commit.assert_awaited_once()

    async def test_prepared_work_can_be_reclaimed_after_expiry(self) -> None:
        job = _job()
        job.status = GenerationJobStatus.ACTIVE
        job.lease_owner = "dead-worker"
        job.lease_claim_id = uuid.uuid4()
        job.fencing_token = 7
        job.lease_expires_at = NOW - timedelta(seconds=1)
        job.heartbeat_at = NOW - timedelta(seconds=121)
        db = _LeaseDb(job, [GenerationAttemptStatus.PREPARED])

        lease = await claim_generation_job(
            db, job_id=job.id, worker_id="worker-b", claim_id=uuid.uuid4(), now=NOW
        )

        self.assertEqual(lease.fencing_token, 8)
        self.assertEqual(job.status, GenerationJobStatus.ACTIVE)

    async def test_repair_claim_ignores_finished_history_and_reads_only_active_attempt(self) -> None:
        job = _job()
        job.status = GenerationJobStatus.ACTIVE
        job.active_attempt_id = uuid.uuid4()
        job.lease_owner = "dead-worker"
        job.lease_claim_id = uuid.uuid4()
        job.fencing_token = 4
        job.lease_expires_at = NOW - timedelta(seconds=1)
        db = _LeaseDb(job, [GenerationAttemptStatus.PREPARED])

        lease = await claim_generation_job(
            db,
            job_id=job.id,
            worker_id="repair-worker",
            claim_id=uuid.uuid4(),
            now=NOW,
        )

        sql = str(
            db.scalars_statements[-1].compile(dialect=postgresql.dialect())
        ).lower()
        self.assertIn("generation_attempts.id =", sql)
        self.assertEqual(lease.fencing_token, 5)

    async def test_stale_fencing_token_cannot_write(self) -> None:
        job = _job()
        db = _LeaseDb(job)
        first = await claim_generation_job(
            db, job_id=job.id, worker_id="worker-a", claim_id=uuid.uuid4(), now=NOW
        )
        job.lease_expires_at = NOW - timedelta(seconds=1)
        second = await claim_generation_job(
            db,
            job_id=job.id,
            worker_id="worker-b",
            claim_id=uuid.uuid4(),
            now=NOW + timedelta(seconds=1),
        )

        with self.assertRaises(StaleWorkerFence):
            await complete_generation_job(
                db,
                job_id=job.id,
                worker_id="worker-a",
                claim_id=first.claim_id,
                fencing_token=first.fencing_token,
                terminal_status=GenerationJobStatus.FINISHED,
                now=NOW + timedelta(seconds=2),
            )
        await complete_generation_job(
            db,
            job_id=job.id,
            worker_id="worker-b",
            claim_id=second.claim_id,
            fencing_token=second.fencing_token,
            terminal_status=GenerationJobStatus.FINISHED,
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(job.status, GenerationJobStatus.FINISHED)

    async def test_reconciliation_work_has_an_independent_fenced_claim(self) -> None:
        job = _job()
        job.status = GenerationJobStatus.RECONCILING
        attempt = SimpleNamespace(
            id=uuid.uuid4(),
            job_id=job.id,
            status=GenerationAttemptStatus.SUBMITTED,
            provider_job_id="task_123",
        )

        class ReconciliationDb:
            def __init__(self):
                self.calls = 0

            async def scalar(self, _statement):
                self.calls += 1
                return job if self.calls in {1, 3} else attempt

        db = ReconciliationDb()
        claim = await claim_generation_reconciliation(
            db,
            job_id=job.id,
            worker_id="worker-reconciler",
            claim_id=uuid.uuid4(),
            now=NOW,
        )

        self.assertEqual(claim.attempt_id, attempt.id)
        self.assertEqual(job.status, GenerationJobStatus.ACTIVE)
        self.assertEqual(claim.lease.fencing_token, 1)
        await pause_generation_reconciliation(
            db,
            lease=claim.lease,
            reason="provider_task_nonterminal",
            retry_after_seconds=15,
            now=NOW,
        )
        self.assertEqual(job.status, GenerationJobStatus.RECONCILING)
        self.assertEqual(job.next_retry_at, NOW + timedelta(seconds=15))
        self.assertIsNone(job.lease_owner)
        self.assertIsNone(job.lease_claim_id)

    async def test_finished_candidate_can_be_reclaimed_for_bounded_qa_retry(self) -> None:
        job = _job()
        job.status = GenerationJobStatus.RECONCILING
        job.active_attempt_id = uuid.uuid4()
        attempt = SimpleNamespace(
            id=job.active_attempt_id,
            job_id=job.id,
            status=GenerationAttemptStatus.FINISHED,
            provider_job_id="task_qa_retry",
        )

        class ReconciliationDb:
            def __init__(self):
                self.calls = 0
                self.attempt_statement = None

            async def scalar(self, statement):
                self.calls += 1
                if self.calls == 1:
                    return job
                self.attempt_statement = statement
                return attempt

        db = ReconciliationDb()
        claim = await claim_generation_reconciliation(
            db,
            job_id=job.id,
            worker_id="worker-reconciler",
            claim_id=uuid.uuid4(),
            now=NOW,
        )

        sql = str(db.attempt_statement.compile(dialect=postgresql.dialect())).upper()
        self.assertIn("FINISHED", sql)
        self.assertEqual(claim.attempt_id, attempt.id)
        self.assertEqual(job.status, GenerationJobStatus.ACTIVE)


if __name__ == "__main__":
    unittest.main()
