"""Website-backend database heartbeat and fencing contracts."""

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

from app.models.generation_job import GenerationJob, GenerationJobStatus  # noqa: E402
from app.services import generation_executor_service as executor  # noqa: E402
from app.services.job_lease_service import (  # noqa: E402
    JobLease,
    StaleWorkerFence,
    heartbeat_generation_job,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class _LeaseDb:
    def __init__(self, job):
        self.job = job

    async def scalar(self, _statement):
        return self.job


def _active_job(*, claim_id: uuid.UUID, lease_expires_at: datetime) -> GenerationJob:
    return GenerationJob(
        id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        submission_correlation_id=uuid.uuid4(),
        status=GenerationJobStatus.ACTIVE,
        retry_count=0,
        repair_count=0,
        lease_owner="api:test",
        lease_claim_id=claim_id,
        lease_expires_at=lease_expires_at,
        heartbeat_at=NOW,
        fencing_token=3,
        payload_version="generation-job.v1",
        api_deployment_id="dpl_api",
        runtime_bundle_id="rtb_" + "a" * 64,
        expected_worker_image_digest="sha256:" + "b" * 64,
        settlement_status="RESERVED",
        delivery_status="PENDING",
    )


class BackendGenerationHeartbeatTest(unittest.IsolatedAsyncioTestCase):
    async def test_simultaneous_completion_never_hides_fence_loss(self) -> None:
        lease = JobLease(
            job_id=uuid.uuid4(),
            worker_id="api:test",
            claim_id=uuid.uuid4(),
            fencing_token=3,
            heartbeat_at=NOW,
            lease_expires_at=NOW + timedelta(seconds=120),
        )

        async def completed_work() -> None:
            return None

        with patch.object(
            executor,
            "_heartbeat_loop",
            AsyncMock(side_effect=StaleWorkerFence("stale", lease.job_id)),
        ):
            with self.assertRaises(StaleWorkerFence):
                await executor._run_with_heartbeat(lease, completed_work())

    async def test_database_heartbeat_extends_the_lease(self) -> None:
        claim_id = uuid.uuid4()
        job = _active_job(
            claim_id=claim_id,
            lease_expires_at=NOW + timedelta(seconds=90),
        )

        lease = await heartbeat_generation_job(
            _LeaseDb(job),
            job_id=job.id,
            worker_id="api:test",
            claim_id=claim_id,
            fencing_token=3,
            now=NOW + timedelta(seconds=30),
        )

        self.assertEqual(lease.heartbeat_at, NOW + timedelta(seconds=30))
        self.assertEqual(lease.lease_expires_at, NOW + timedelta(seconds=150))

    async def test_expired_database_heartbeat_is_rejected(self) -> None:
        claim_id = uuid.uuid4()
        job = _active_job(claim_id=claim_id, lease_expires_at=NOW)

        with self.assertRaises(StaleWorkerFence):
            await heartbeat_generation_job(
                _LeaseDb(job),
                job_id=job.id,
                worker_id="api:test",
                claim_id=claim_id,
                fencing_token=3,
                now=NOW + timedelta(seconds=1),
            )


if __name__ == "__main__":
    unittest.main()
