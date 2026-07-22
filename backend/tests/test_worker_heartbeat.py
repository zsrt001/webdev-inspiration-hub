"""Worker heartbeat and runtime function-list contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.services.job_lease_service import (
    GENERATION_SCHEMA_REVISION,
    StaleWorkerFence,
    WorkerHeartbeatInvalid,
    WorkerRuntimeHeartbeat,
    heartbeat_generation_job,
    publish_worker_runtime_heartbeat,
    read_worker_runtime_heartbeat,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class _LeaseDb:
    def __init__(self, job):
        self.job = job

    async def scalar(self, _statement):
        return self.job


class _Redis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, *, ex):
        self.values[key] = (value, ex)
        return True

    async def get(self, key):
        value = self.values.get(key)
        return None if value is None else value[0]


class WorkerHeartbeatTest(unittest.IsolatedAsyncioTestCase):
    def test_worker_schema_coordinate_matches_partner_consent_head(self) -> None:
        self.assertEqual(GENERATION_SCHEMA_REVISION, "20260710_0020")

    async def test_simultaneous_work_completion_never_hides_heartbeat_fence_loss(self) -> None:
        from app import worker_tasks
        from app.services.job_lease_service import JobLease

        lease = JobLease(
            job_id=uuid.uuid4(),
            worker_id="worker-a",
            claim_id=uuid.uuid4(),
            fencing_token=3,
            heartbeat_at=NOW,
            lease_expires_at=NOW + timedelta(seconds=120),
        )

        async def completed_work() -> None:
            return None

        with patch.object(
            worker_tasks,
            "_heartbeat_loop",
            AsyncMock(side_effect=StaleWorkerFence("stale", lease.job_id)),
        ):
            with self.assertRaises(StaleWorkerFence):
                await worker_tasks._run_with_heartbeat(lease, completed_work())

    async def test_public_heartbeat_is_release_bound_and_expires_after_120_seconds(self) -> None:
        redis = _Redis()
        heartbeat = WorkerRuntimeHeartbeat(
            worker_id="worker-a",
            environment="production",
            source_sha="a" * 40,
            runtime_bundle_id="rtb_" + "b" * 64,
            api_deployment_id="dpl_api",
            worker_deployment_id="worker_deploy_1",
            worker_image_digest="sha256:" + "c" * 64,
            schema_revision="20260710_0020",
            payload_min="generation-job.v1",
            payload_max="generation-job.v1",
            config_hash="d" * 64,
            current_feature_snapshot_hash="e" * 64,
            target_feature_snapshot_hash="f" * 64,
            published_at=NOW,
        )

        await publish_worker_runtime_heartbeat(redis, heartbeat)
        observed = await read_worker_runtime_heartbeat(
            redis,
            environment="production",
            runtime_bundle_id="rtb_" + "b" * 64,
            now=NOW + timedelta(seconds=120),
        )

        self.assertEqual(observed.worker_image_digest, heartbeat.worker_image_digest)
        stored = next(iter(redis.values.values()))
        self.assertEqual(stored[1], 150)
        with self.assertRaisesRegex(WorkerHeartbeatInvalid, "worker_heartbeat_stale"):
            await read_worker_runtime_heartbeat(
                redis,
                environment="production",
                runtime_bundle_id="rtb_" + "b" * 64,
                now=NOW + timedelta(seconds=121),
            )

    async def test_heartbeat_extends_a_120_second_lease(self) -> None:
        claim_id = uuid.uuid4()
        job = GenerationJob(
            id=uuid.uuid4(),
            order_id=uuid.uuid4(),
            submission_correlation_id=uuid.uuid4(),
            status=GenerationJobStatus.ACTIVE,
            retry_count=0,
            repair_count=0,
            lease_owner="worker-a",
            lease_claim_id=claim_id,
            lease_expires_at=NOW + timedelta(seconds=90),
            heartbeat_at=NOW,
            fencing_token=3,
            payload_version="generation-job.v1",
            api_deployment_id="dpl_api",
            runtime_bundle_id="rtb_" + "a" * 64,
            expected_worker_image_digest="sha256:" + "b" * 64,
            settlement_status="RESERVED",
            delivery_status="PENDING",
        )
        db = _LeaseDb(job)

        lease = await heartbeat_generation_job(
            db,
            job_id=job.id,
            worker_id="worker-a",
            claim_id=claim_id,
            fencing_token=3,
            now=NOW + timedelta(seconds=30),
        )

        self.assertEqual(lease.heartbeat_at, NOW + timedelta(seconds=30))
        self.assertEqual(lease.lease_expires_at, NOW + timedelta(seconds=150))

    async def test_expired_heartbeat_is_rejected(self) -> None:
        claim_id = uuid.uuid4()
        job = GenerationJob(
            id=uuid.uuid4(),
            order_id=uuid.uuid4(),
            submission_correlation_id=uuid.uuid4(),
            status=GenerationJobStatus.ACTIVE,
            retry_count=0,
            repair_count=0,
            lease_owner="worker-a",
            lease_claim_id=claim_id,
            lease_expires_at=NOW,
            heartbeat_at=NOW - timedelta(seconds=120),
            fencing_token=3,
            payload_version="generation-job.v1",
            api_deployment_id="dpl_api",
            runtime_bundle_id="rtb_" + "a" * 64,
            expected_worker_image_digest="sha256:" + "b" * 64,
            settlement_status="RESERVED",
            delivery_status="PENDING",
        )

        with self.assertRaises(StaleWorkerFence):
            await heartbeat_generation_job(
                _LeaseDb(job),
                job_id=job.id,
                worker_id="worker-a",
                claim_id=claim_id,
                fencing_token=3,
                now=NOW + timedelta(seconds=1),
            )

    def test_worker_exports_only_durable_v1_functions(self) -> None:
        from app.worker import WorkerSettings

        names = [function.__name__ for function in WorkerSettings.functions]
        self.assertEqual(
            names,
            ["generate_order_v1", "generate_attempt_v1", "reconcile_generation_v1"],
        )
        worker_source = (BACKEND_DIR / "app/worker_tasks.py").read_text(encoding="utf-8")
        for forbidden in (
            "generate_live_portrait",
            "session_service",
            "run_order_generation",
            "source_image_urls",
        ):
            self.assertNotIn(forbidden, worker_source)

    def test_worker_image_is_hash_locked_and_never_runs_uvicorn(self) -> None:
        dockerfile = (BACKEND_DIR / "Dockerfile.worker").read_text(encoding="utf-8")
        entrypoint = (BACKEND_DIR / "scripts/worker_entrypoint.py").read_text(encoding="utf-8")

        self.assertIn(
            "FROM python:3.11.15-slim-bookworm@sha256:"
            "721dc13fd1be0a771e54b72097634291d628d0007dee9da777e2ce676a9c998f",
            dockerfile,
        )
        self.assertIn(
            'LABEL org.opencontainers.image.source="https://github.com/'
            'zsrt001/webdev-inspiration-hub"',
            dockerfile,
        )
        self.assertIn(
            "sed -i 's|http://deb.debian.org|https://deb.debian.org|g'",
            dockerfile,
        )
        self.assertIn("--require-hashes -r requirements.lock.txt", dockerfile)
        self.assertIn('CMD ["python", "scripts/worker_entrypoint.py"]', dockerfile)
        self.assertNotIn("uvicorn", dockerfile.lower())
        self.assertNotIn("uvicorn", entrypoint.lower())
        self.assertIn('"app.worker.WorkerSettings"', entrypoint)


if __name__ == "__main__":
    unittest.main()
