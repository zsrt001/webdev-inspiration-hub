"""Durable, bounded repair attempts and IDs-only handoff contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import unittest
import uuid

from app.models.credit_reservation import CreditReservation, ReservationStatus
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GENERATION_JOB_PAYLOAD_VERSION, GenerationJob
from app.models.qa_verdict import QaDecision, QaVerdict
from app.services.generation_repair_service import (
    GENERATION_ATTEMPT_PAYLOAD_VERSION,
    RepairInvariantError,
    build_repair_attempt,
    decide_next_generation_action,
    validate_repair_capture_provenance,
)
from app.services.job_lease_service import JobLease
from app.services.generation_attempt_service import _build_repair_request


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _job(*, repair_count: int = 0) -> GenerationJob:
    job = GenerationJob.queued(
        order_id=uuid.uuid4(),
        submission_correlation_id=uuid.uuid4(),
        api_deployment_id="dpl_api",
        runtime_bundle_id="rtb_" + "a" * 64,
        expected_worker_image_digest="sha256:" + "b" * 64,
    )
    job.repair_count = repair_count
    return job


def _initial(job: GenerationJob) -> GenerationAttempt:
    attempt = GenerationAttempt.prepared(
        job=job,
        attempt_number=1,
        kind=GenerationAttemptKind.INITIAL,
        provider="evolink",
    )
    attempt.status = GenerationAttemptStatus.FINISHED
    attempt.submission_accounting_state = "CAPTURED"
    return attempt


def _verdict(job: GenerationJob, initial: GenerationAttempt) -> QaVerdict:
    return QaVerdict(
        id=uuid.uuid4(),
        job_id=job.id,
        attempt_id=initial.id,
        candidate_asset_id=uuid.uuid4(),
        checker_version="vowpic-checker.v1",
        model_version="vowpic-vision.v1",
        schema_version="vowpic.qa.v1",
        decision=QaDecision.REPAIR,
        reasons=["face_underexposed"],
        metrics={"scores": {"exposure": 0.2}},
        response_sha256="c" * 64,
    )


class GenerationRepairTest(unittest.IsolatedAsyncioTestCase):
    def test_repair_request_is_targeted_and_can_include_previous_candidate(self) -> None:
        order = SimpleNamespace(
            template_id="solo_royal_castle",
            product_policy_snapshot={
                "template_id": "solo_royal_castle",
                "subject_count": 1,
                "prompt_override": None,
                "global_style_text": None,
                "scene_text": None,
                "outfit_text": None,
            },
        )
        request = _build_repair_request(
            order,
            identity_grant_urls=("https://grant.example.test/source",),
            previous_candidate_url="https://grant.example.test/candidate",
            reasons=("face_underexposed",),
            repair_number=1,
        )

        self.assertEqual(
            request.image_urls,
            (
                "https://grant.example.test/source",
                "https://grant.example.test/candidate",
            ),
        )
        self.assertIn("face_underexposed", request.prompt)
        self.assertIn("TARGETED REPAIR 1", request.prompt)
        self.assertNotIn("https://", request.prompt)

    def test_repair_has_unique_verdict_lineage_without_queue_handoff(self) -> None:
        job = _job()
        initial = _initial(job)
        verdict = _verdict(job, initial)

        repair = build_repair_attempt(job=job, verdict=verdict)

        self.assertEqual(repair.kind, GenerationAttemptKind.REPAIR)
        self.assertEqual(repair.source_verdict_id, verdict.id)
        self.assertEqual(repair.submission_accounting_state, "NOT_CAPTURED")

    def test_third_candidate_producing_repair_is_refused(self) -> None:
        job = _job(repair_count=2)
        initial = _initial(job)
        with self.assertRaisesRegex(RepairInvariantError, "generation_repair_limit_reached"):
            build_repair_attempt(job=job, verdict=_verdict(job, initial))

    def test_repair_requires_exact_initial_capture_provenance(self) -> None:
        job = _job()
        initial = _initial(job)
        reservation = CreditReservation(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            order_id=job.order_id,
            amount=2,
            status=ReservationStatus.CAPTURED,
            idempotency_key="reserve:test",
            request_hash="d" * 64,
            funding_policy_snapshot={},
            funding_policy_hash="e" * 64,
            provider_attempt_id=initial.id,
            captured_transaction_id=uuid.uuid4(),
            expires_at=NOW,
        )

        validate_repair_capture_provenance(
            job=job,
            reservation=reservation,
            initial_attempt=initial,
        )
        for override in (
            {"status": ReservationStatus.RELEASED},
            {"provider_attempt_id": uuid.uuid4()},
        ):
            for key, value in override.items():
                setattr(reservation, key, value)
            with self.assertRaises(RepairInvariantError):
                validate_repair_capture_provenance(
                    job=job,
                    reservation=reservation,
                    initial_attempt=initial,
                )
            reservation.status = ReservationStatus.CAPTURED
            reservation.provider_attempt_id = initial.id

    async def test_disposition_creates_one_exact_repair_and_replay_reuses_it(self) -> None:
        job = _job()
        job.status = "ACTIVE"
        job.lease_owner = "worker-a"
        job.lease_claim_id = uuid.uuid4()
        job.fencing_token = 7
        job.lease_expires_at = NOW.replace(minute=2)
        initial = _initial(job)
        verdict = _verdict(job, initial)
        reservation = SimpleNamespace(
            id=uuid.uuid4(),
            order_id=job.order_id,
            status=ReservationStatus.CAPTURED,
            provider_attempt_id=initial.id,
            captured_transaction_id=uuid.uuid4(),
        )
        order = SimpleNamespace(
            id=job.order_id,
            generation_job_id=job.id,
            status="QA_PENDING",
            reservation_id=reservation.id,
        )
        db = SimpleNamespace(add=Mock(), flush=AsyncMock())
        lease_kwargs = {
            "job_id": job.id,
            "verdict_id": verdict.id,
            "worker_id": "worker-a",
            "lease_claim_id": job.lease_claim_id,
            "fencing_token": 7,
            "now": NOW,
        }

        with (
            patch(
                "app.services.generation_repair_service.require_current_generation_fence",
                AsyncMock(return_value=job),
            ),
            patch(
                "app.services.generation_repair_service._load_verdict",
                AsyncMock(return_value=verdict),
            ),
            patch(
                "app.services.generation_repair_service._load_existing_repair",
                AsyncMock(side_effect=(None,)),
            ),
            patch(
                "app.services.generation_repair_service._load_repair_capture_lineage",
                AsyncMock(return_value=(order, reservation, initial)),
            ),
        ):
            created = await decide_next_generation_action(db, **lease_kwargs)

        self.assertEqual(created.kind, "CREATE_REPAIR")
        self.assertEqual(job.repair_count, 1)
        self.assertEqual(job.active_attempt_id, created.attempt_id)
        self.assertEqual(order.status, "REPAIRING")
        self.assertIsNone(job.lease_owner)
        self.assertEqual(db.add.call_count, 1)

        existing = SimpleNamespace(
            id=created.attempt_id,
            job_id=job.id,
            source_verdict_id=verdict.id,
            kind=GenerationAttemptKind.REPAIR,
        )
        replay_db = SimpleNamespace(add=Mock(), flush=AsyncMock())
        with (
            patch(
                "app.services.generation_repair_service.require_current_generation_fence",
                AsyncMock(return_value=job),
            ),
            patch(
                "app.services.generation_repair_service._load_verdict",
                AsyncMock(return_value=verdict),
            ),
            patch(
                "app.services.generation_repair_service._load_existing_repair",
                AsyncMock(return_value=existing),
            ),
            patch(
                "app.services.generation_repair_service._load_order_for_update",
                AsyncMock(return_value=order),
            ),
        ):
            replay = await decide_next_generation_action(replay_db, **lease_kwargs)

        self.assertEqual(replay.attempt_id, created.attempt_id)
        self.assertTrue(replay.replayed)
        replay_db.add.assert_not_called()

    async def test_worker_submits_only_the_persisted_repair_attempt_id(self) -> None:
        from app.services import generation_executor_service as executor

        job = _job(repair_count=1)
        job.status = "ACTIVE"
        repair = GenerationAttempt.prepared(
            job=job,
            attempt_number=2,
            kind=GenerationAttemptKind.REPAIR,
            provider="evolink",
            client_request_id=f"repair:{uuid.uuid4()}",
            source_verdict_id=uuid.uuid4(),
        )
        job.active_attempt_id = repair.id
        user_id = uuid.uuid4()
        lease = JobLease(
            job_id=job.id,
            worker_id="worker-a",
            claim_id=uuid.uuid4(),
            fencing_token=3,
            heartbeat_at=NOW,
            lease_expires_at=NOW.replace(minute=2),
        )
        execute = AsyncMock()
        reconcile = AsyncMock()

        async def run_now(_lease, operation):
            await operation

        with (
            patch(
                "app.services.generation_executor_service._load_attempt_capability_context",
                AsyncMock(return_value=(job, repair, user_id)),
            ),
            patch(
                "app.services.generation_executor_service.claim_generation_job",
                AsyncMock(return_value=lease),
            ),
            patch(
                "app.services.generation_executor_service._execute_claimed_generation_attempt",
                execute,
            ),
            patch(
                "app.services.generation_executor_service._run_with_heartbeat",
                side_effect=run_now,
            ),
            patch(
                "app.services.generation_executor_service.reconcile_generation_v1",
                reconcile,
            ),
            patch("app.services.generation_executor_service.async_session_maker") as sessions,
        ):
            session = SimpleNamespace(commit=AsyncMock())

            class SessionContext:
                async def __aenter__(self):
                    return session

                async def __aexit__(self, *_args):
                    return False

            sessions.return_value = SessionContext()
            await executor.generate_attempt_v1(
                {"worker_id": "worker-a"},
                str(repair.id),
                GENERATION_ATTEMPT_PAYLOAD_VERSION,
            )

        execute.assert_awaited_once_with(lease, user_id, repair.id)
        reconcile.assert_awaited_once_with(
            {"worker_id": "worker-a"},
            str(job.id),
            GENERATION_JOB_PAYLOAD_VERSION,
        )

    async def test_attempt_executor_never_prepares_a_replacement_row(self) -> None:
        from app.services import generation_attempt_service as service

        job = _job(repair_count=1)
        job.active_attempt_id = uuid.uuid4()
        repair = SimpleNamespace(
            id=job.active_attempt_id,
            job_id=job.id,
            kind=GenerationAttemptKind.REPAIR,
            status=GenerationAttemptStatus.PREPARED,
        )
        lease = JobLease(
            job_id=job.id,
            worker_id="worker-a",
            claim_id=uuid.uuid4(),
            fencing_token=3,
            heartbeat_at=NOW,
            lease_expires_at=NOW.replace(minute=2),
        )
        session = SimpleNamespace(commit=AsyncMock())

        class SessionContext:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(service, "async_session_maker", return_value=SessionContext(), create=True),
            patch.object(
                service,
                "require_current_generation_fence",
                AsyncMock(return_value=job),
            ),
            patch.object(service, "_locked_attempt", AsyncMock(return_value=repair)),
            patch.object(
                service,
                "submit_generation_attempt",
                AsyncMock(
                    return_value=SimpleNamespace(
                        id=repair.id,
                        status=GenerationAttemptStatus.SUBMITTED,
                    )
                ),
            ) as submit,
            patch.object(
                service,
                "pause_generation_reconciliation",
                AsyncMock(),
            ),
            patch.object(service, "prepare_initial_generation_attempt", AsyncMock()) as prepare_initial,
        ):
            await service.execute_claimed_generation_attempt(
                lease=lease,
                user_id=uuid.uuid4(),
                attempt_id=repair.id,
            )

        submit.assert_awaited_once()
        self.assertEqual(submit.await_args.kwargs["attempt_id"], repair.id)
        prepare_initial.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
