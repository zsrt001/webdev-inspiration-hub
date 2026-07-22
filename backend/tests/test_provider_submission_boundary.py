"""Durable Evolink submission boundary and lost-response contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import sys
import unittest
import uuid

import httpx


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.generation_attempt import (
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJobStatus
from app.services.evolink_service import (
    EvolinkGenerationRequest,
    EvolinkProviderError,
)
from app.services.generation_attempt_service import (
    PreparedSubmission,
    ensure_accepted_submission_accounting,
    execute_claimed_generation_job,
    persist_submitted_fact,
    require_partner_submission_allowed,
    submit_generation_attempt,
)
from app.services.job_lease_service import JobLease, StaleWorkerFence


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _lease() -> JobLease:
    return JobLease(
        job_id=uuid.uuid4(),
        worker_id="worker-a",
        claim_id=uuid.uuid4(),
        fencing_token=3,
        heartbeat_at=NOW,
        lease_expires_at=NOW.replace(minute=2),
    )


class _Db:
    def __init__(self):
        self.commit = AsyncMock()


class ProviderSubmissionBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_partner_withdrawal_case_blocks_before_provider_submission_boundary(self) -> None:
        invite = SimpleNamespace(
            id=uuid.uuid4(),
            status="COMPLETED",
            job_id=uuid.uuid4(),
        )
        db = SimpleNamespace(
            scalar=AsyncMock(side_effect=[invite, uuid.uuid4()]),
        )

        with self.assertRaisesRegex(
            Exception,
            "generation_partner_consent_withdrawn",
        ):
            await require_partner_submission_allowed(db, job_id=invite.job_id)

        self.assertEqual(db.scalar.await_count, 2)

    async def test_confirmed_retryable_rejection_retries_only_after_durable_classification(self) -> None:
        lease = _lease()
        attempt_id = uuid.uuid4()
        prepared = PreparedSubmission(
            attempt_id=attempt_id,
            job_id=lease.job_id,
            reservation_id=uuid.uuid4(),
            request=EvolinkGenerationRequest(
                model="gemini-3.1-flash-image-preview",
                prompt="Identity-safe wedding portrait",
                image_urls=("https://grant.example.test/api/v1/media/grants/token",),
                size="3:4",
                quality="2K",
                model_params={"web_search": False},
            ),
        )
        fact = SimpleNamespace(task_id="task_after_429", cost_minor_units=4, currency="USD")
        provider = SimpleNamespace(
            submit=AsyncMock(
                side_effect=(
                    EvolinkProviderError(
                        "evolink_submit_rejected_429",
                        retryable=True,
                        acceptance_possible=False,
                    ),
                    fact,
                )
            )
        )
        persisted = SimpleNamespace(
            id=attempt_id,
            status=GenerationAttemptStatus.SUBMITTED,
        )
        db = _Db()
        with (
            patch(
                "app.services.generation_attempt_service.prepare_submission_boundary",
                AsyncMock(return_value=prepared),
            ),
            patch(
                "app.services.generation_attempt_service.record_preaccept_rejection",
                AsyncMock(return_value=1),
                create=True,
            ) as record,
            patch(
                "app.services.generation_attempt_service.persist_submitted_fact",
                AsyncMock(return_value=persisted),
            ),
            patch(
                "app.services.generation_attempt_service.capture_initial_submission",
                AsyncMock(return_value=persisted),
            ),
            patch(
                "app.services.generation_attempt_service.asyncio.sleep",
                AsyncMock(),
            ),
            patch(
                "app.services.generation_attempt_service.require_current_generation_fence",
                AsyncMock(),
            ),
        ):
            result = await submit_generation_attempt(
                db,
                attempt_id=attempt_id,
                lease=lease,
                user_id=uuid.uuid4(),
                provider=provider,
            )

        self.assertIs(result, persisted)
        self.assertEqual(provider.submit.await_count, 2)
        record.assert_awaited_once()
        self.assertFalse(record.await_args.kwargs["terminal"])

    async def test_confirmed_terminal_rejection_fails_and_releases_without_unknown_state(self) -> None:
        lease = _lease()
        attempt_id = uuid.uuid4()
        prepared = PreparedSubmission(
            attempt_id=attempt_id,
            job_id=lease.job_id,
            reservation_id=uuid.uuid4(),
            request=EvolinkGenerationRequest(
                model="gemini-3.1-flash-image-preview",
                prompt="Identity-safe wedding portrait",
                image_urls=("https://grant.example.test/api/v1/media/grants/token",),
                size="3:4",
                quality="2K",
                model_params={"web_search": False},
            ),
        )
        failed = SimpleNamespace(
            id=attempt_id,
            status=GenerationAttemptStatus.FAILED,
        )
        provider = SimpleNamespace(
            submit=AsyncMock(
                side_effect=EvolinkProviderError(
                    "evolink_submit_rejected_422",
                    retryable=False,
                    acceptance_possible=False,
                )
            )
        )
        db = _Db()
        with (
            patch(
                "app.services.generation_attempt_service.prepare_submission_boundary",
                AsyncMock(return_value=prepared),
            ),
            patch(
                "app.services.generation_attempt_service.record_preaccept_rejection",
                AsyncMock(return_value=failed),
                create=True,
            ) as record,
            patch(
                "app.services.generation_attempt_service.mark_submission_unknown",
                AsyncMock(),
            ) as mark_unknown,
        ):
            result = await submit_generation_attempt(
                db,
                attempt_id=attempt_id,
                lease=lease,
                user_id=uuid.uuid4(),
                provider=provider,
            )

        self.assertIs(result, failed)
        record.assert_awaited_once()
        self.assertTrue(record.await_args.kwargs["terminal"])
        mark_unknown.assert_not_awaited()

    async def test_accepted_submit_is_immediately_paused_for_reconciliation(self) -> None:
        lease = _lease()
        attempt = SimpleNamespace(
            id=uuid.uuid4(),
            status=GenerationAttemptStatus.PREPARED,
        )
        submitted = SimpleNamespace(
            id=attempt.id,
            status=GenerationAttemptStatus.SUBMITTED,
        )
        db = _Db()

        class SessionContext:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *_args):
                return False

        with (
            patch(
                "app.services.generation_attempt_service.async_session_maker",
                return_value=SessionContext(),
            ),
            patch(
                "app.services.generation_attempt_service.prepare_initial_generation_attempt",
                AsyncMock(return_value=attempt),
            ),
            patch(
                "app.services.generation_attempt_service.submit_generation_attempt",
                AsyncMock(return_value=submitted),
            ),
            patch(
                "app.services.generation_attempt_service.pause_generation_reconciliation",
                AsyncMock(),
                create=True,
            ) as pause,
        ):
            await execute_claimed_generation_job(lease=lease, user_id=uuid.uuid4())

        pause.assert_awaited_once_with(
            db,
            lease=lease,
            reason="provider_task_pending",
            retry_after_seconds=5,
        )
        self.assertEqual(db.commit.await_count, 2)

    async def test_reconciler_completes_pending_initial_capture_once(self) -> None:
        lease = _lease()
        reservation_id = uuid.uuid4()
        attempt = SimpleNamespace(
            id=uuid.uuid4(),
            job_id=lease.job_id,
            kind=GenerationAttemptKind.INITIAL,
            status=GenerationAttemptStatus.SUBMITTED,
            submission_accounting_state="PENDING",
        )
        job = SimpleNamespace(
            id=lease.job_id,
            order_id=uuid.uuid4(),
            active_attempt_id=attempt.id,
        )
        order = SimpleNamespace(
            id=job.order_id,
            user_id=uuid.uuid4(),
            generation_job_id=job.id,
            reservation_id=reservation_id,
        )
        db = SimpleNamespace(scalar=AsyncMock(return_value=order))
        with (
            patch(
                "app.services.generation_attempt_service.require_current_generation_fence",
                AsyncMock(return_value=job),
            ),
            patch(
                "app.services.generation_attempt_service._locked_attempt",
                AsyncMock(return_value=attempt),
            ),
            patch(
                "app.services.generation_attempt_service.capture_reservation",
                AsyncMock(),
            ) as capture,
        ):
            result = await ensure_accepted_submission_accounting(
                db,
                attempt_id=attempt.id,
                lease=lease,
                user_id=order.user_id,
            )

        self.assertIs(result, attempt)
        self.assertEqual(attempt.submission_accounting_state, "CAPTURED")
        capture.assert_awaited_once_with(
            db,
            reservation_id=reservation_id,
            provider_attempt_id=attempt.id,
            idempotency_key=f"capture:{attempt.id}",
        )

    async def test_repair_submit_fact_never_enters_capture_accounting(self) -> None:
        lease = _lease()
        attempt = SimpleNamespace(
            id=uuid.uuid4(),
            job_id=lease.job_id,
            kind=GenerationAttemptKind.REPAIR,
            status=GenerationAttemptStatus.SUBMITTING,
            provider_job_id=None,
            submission_accounting_state="NOT_CAPTURED",
            submitted_at=None,
            cost_minor_units=None,
            cost_currency=None,
        )
        with (
            patch(
                "app.services.generation_attempt_service.require_current_generation_fence",
                AsyncMock(return_value=SimpleNamespace(id=lease.job_id)),
            ),
            patch(
                "app.services.generation_attempt_service._locked_attempt",
                AsyncMock(return_value=attempt),
            ),
        ):
            await persist_submitted_fact(
                AsyncMock(),
                attempt_id=attempt.id,
                lease=lease,
                fact=SimpleNamespace(task_id="task_repair", cost_minor_units=3, currency="USD"),
            )

        self.assertEqual(attempt.status, GenerationAttemptStatus.SUBMITTED)
        self.assertEqual(attempt.submission_accounting_state, "NOT_CAPTURED")

    async def test_repair_submission_revalidates_initial_capture_and_never_captures_again(self) -> None:
        lease = _lease()
        attempt_id = uuid.uuid4()
        prepared = PreparedSubmission(
            attempt_id=attempt_id,
            job_id=lease.job_id,
            reservation_id=uuid.uuid4(),
            request=EvolinkGenerationRequest(
                model="gemini-3.1-flash-image-preview",
                prompt="Targeted repair",
                image_urls=("https://grant.example.test/api/v1/media/grants/token",),
                size="3:4",
                quality="2K",
                model_params={"web_search": False},
            ),
            attempt_kind=GenerationAttemptKind.REPAIR,
        )
        provider = SimpleNamespace(
            submit=AsyncMock(
                return_value=SimpleNamespace(
                    task_id="task_repair",
                    cost_minor_units=3,
                    currency="USD",
                )
            )
        )
        persisted = SimpleNamespace(id=attempt_id, kind=GenerationAttemptKind.REPAIR)
        db = _Db()
        with (
            patch(
                "app.services.generation_attempt_service.prepare_submission_boundary",
                AsyncMock(return_value=prepared),
            ),
            patch(
                "app.services.generation_attempt_service.persist_submitted_fact",
                AsyncMock(return_value=persisted),
            ),
            patch(
                "app.services.generation_attempt_service.require_repair_submission_capture",
                AsyncMock(return_value=persisted),
                create=True,
            ) as require_capture,
            patch(
                "app.services.generation_attempt_service.capture_initial_submission",
                AsyncMock(),
            ) as capture_initial,
        ):
            result = await submit_generation_attempt(
                db,
                attempt_id=attempt_id,
                lease=lease,
                user_id=uuid.uuid4(),
                provider=provider,
            )

        self.assertIs(result, persisted)
        require_capture.assert_awaited_once()
        capture_initial.assert_not_awaited()

    async def test_timeout_before_task_id_is_never_reposted(self) -> None:
        lease = _lease()
        attempt_id = uuid.uuid4()
        prepared = PreparedSubmission(
            attempt_id=attempt_id,
            job_id=lease.job_id,
            reservation_id=uuid.uuid4(),
            request=EvolinkGenerationRequest(
                model="gemini-3.1-flash-image-preview",
                prompt="Identity-safe wedding portrait",
                image_urls=("https://grant.example.test/api/v1/media/grants/token",),
                size="3:4",
                quality="2K",
                model_params={"web_search": False},
            ),
        )
        provider = SimpleNamespace(submit=AsyncMock(side_effect=httpx.ReadTimeout("uncertain")))
        db = _Db()
        unknown = SimpleNamespace(
            id=attempt_id,
            status=GenerationAttemptStatus.UNKNOWN,
            job=SimpleNamespace(status=GenerationJobStatus.RECONCILING),
        )

        with (
            patch(
                "app.services.generation_attempt_service.prepare_submission_boundary",
                new=AsyncMock(return_value=prepared),
            ),
            patch(
                "app.services.generation_attempt_service.mark_submission_unknown",
                new=AsyncMock(return_value=unknown),
            ) as mark_unknown,
        ):
            first = await submit_generation_attempt(
                db,
                attempt_id=attempt_id,
                lease=lease,
                user_id=uuid.uuid4(),
                provider=provider,
            )

        self.assertEqual(first.status, GenerationAttemptStatus.UNKNOWN)
        provider.submit.assert_awaited_once_with(
            prepared.request,
            attempt_id=prepared.attempt_id,
        )
        mark_unknown.assert_awaited_once()
        self.assertEqual(db.commit.await_count, 2)  # SUBMITTING, then UNKNOWN.

    async def test_stale_fence_after_task_id_discards_response_without_capture(self) -> None:
        lease = _lease()
        attempt_id = uuid.uuid4()
        prepared = PreparedSubmission(
            attempt_id=attempt_id,
            job_id=lease.job_id,
            reservation_id=uuid.uuid4(),
            request=EvolinkGenerationRequest(
                model="gemini-3.1-flash-image-preview",
                prompt="Identity-safe wedding portrait",
                image_urls=("https://grant.example.test/api/v1/media/grants/token",),
                size="3:4",
                quality="2K",
                model_params={"web_search": False},
            ),
        )
        provider = SimpleNamespace(
            submit=AsyncMock(
                return_value=SimpleNamespace(
                    task_id="task_123",
                    cost_minor_units=12,
                    currency="USD",
                )
            )
        )
        db = _Db()
        with (
            patch(
                "app.services.generation_attempt_service.prepare_submission_boundary",
                new=AsyncMock(return_value=prepared),
            ),
            patch(
                "app.services.generation_attempt_service.persist_submitted_fact",
                new=AsyncMock(side_effect=StaleWorkerFence("stale", lease.job_id)),
            ),
            patch(
                "app.services.generation_attempt_service.capture_initial_submission",
                new=AsyncMock(),
            ) as capture,
        ):
            with self.assertRaises(StaleWorkerFence):
                await submit_generation_attempt(
                    db,
                    attempt_id=attempt_id,
                    lease=lease,
                    user_id=uuid.uuid4(),
                    provider=provider,
                )
        capture.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
