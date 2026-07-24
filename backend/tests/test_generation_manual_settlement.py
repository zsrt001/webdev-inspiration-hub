"""Operator-only closure for task-ID-less ambiguous generation submissions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch
from sqlalchemy.exc import IntegrityError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.credit_reservation import ReservationStatus  # noqa: E402
from app.models.generation_attempt import (  # noqa: E402
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJobStatus  # noqa: E402
from app.models.order import OrderStatus  # noqa: E402
from app.routers.admin import AdminGenerationManualResolutionRequest  # noqa: E402
from app.services import generation_manual_settlement_service as service  # noqa: E402


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


class _EvidenceStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_private(self, object_key: str, data: bytes, _content_type: str) -> None:
        if object_key in self.objects:
            raise RuntimeError("already exists")
        self.objects[object_key] = bytes(data)

    def read_private(self, object_key: str) -> bytes:
        if object_key not in self.objects:
            raise FileNotFoundError(object_key)
        return self.objects[object_key]


class _Rows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _Db:
    def __init__(
        self,
        *,
        job,
        attempt,
        order,
        reservation,
        grants=(),
        existing_provider_attempt_id=None,
        flush_error=None,
    ):
        self._scalars = iter((job, attempt, order, reservation))
        self._grants = list(grants)
        self._existing_provider_attempt_id = existing_provider_attempt_id
        self._flush_error = flush_error

    async def scalar(self, _statement):
        try:
            return next(self._scalars)
        except StopIteration:
            return self._existing_provider_attempt_id

    async def scalars(self, _statement):
        return _Rows(self._grants)

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def flush(self):
        if self._flush_error is not None:
            raise self._flush_error


def _case(*, reservation_status: ReservationStatus = ReservationStatus.RESERVED):
    job_id = uuid.uuid4()
    order_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    reservation_id = uuid.uuid4()
    job = SimpleNamespace(
        id=job_id,
        order_id=order_id,
        active_attempt_id=attempt_id,
        status=GenerationJobStatus.RECONCILING,
        lease_owner=None,
        lease_claim_id=None,
        lease_expires_at=None,
        next_retry_at=None,
        retry_count=0,
        settlement_status="RESERVED",
        delivery_status="PENDING",
        last_error_code="provider_submission_human_required",
        last_error_detail="submit_response_lost",
        finished_at=None,
        runtime_bundle_id="rtb_" + ("b" * 64),
        api_deployment_id="dpl_manual_evidence_test",
    )
    attempt = SimpleNamespace(
        id=attempt_id,
        job_id=job_id,
        kind=GenerationAttemptKind.INITIAL,
        provider="evolink",
        status=GenerationAttemptStatus.UNKNOWN,
        provider_job_id=None,
        submitted_at=None,
        submit_started_at=NOW,
        submission_accounting_state="NOT_CAPTURED",
        cost_minor_units=None,
        cost_currency=None,
        finished_at=None,
    )
    order = SimpleNamespace(
        id=order_id,
        generation_job_id=job_id,
        reservation_id=reservation_id,
        status=OrderStatus.UNKNOWN_EXTERNAL_STATE,
        settlement_status="RESERVED",
        delivery_status="PENDING",
        error_message=None,
    )
    reservation = SimpleNamespace(
        id=reservation_id,
        status=reservation_status,
    )
    return job, attempt, order, reservation


def _evidence(
    job,
    attempt,
    order,
    *,
    action: str,
    reason: str,
    provider_task_id: str | None = None,
    provider_accepted: bool | None = None,
):
    _payload, raw, record = service.build_generation_manual_evidence(
        job=job,
        attempt=attempt,
        order=order,
        action=action,
        source_type="EVOLINK_DASHBOARD",
        observation_reference="provider-console-record-20260723",
        observed_at=NOW,
        approval_id=uuid.UUID("00000000-0000-4000-8000-000000000075"),
        operator_actor="ops@example.com",
        operator_reason=reason,
        provider_task_id=provider_task_id,
        provider_accepted=provider_accepted,
        now=NOW,
    )
    store = _EvidenceStore()
    store.objects[record.object_key] = raw
    return {
        "provider_evidence_object_key": record.object_key,
        "provider_evidence_sha256": record.sha256,
        "evidence_store": store,
    }


class GenerationManualSettlementTest(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_case_is_queryable_and_not_scheduled_for_auto_retry(
        self,
    ) -> None:
        job, attempt, order, _reservation = _case()
        attempt.kind = GenerationAttemptKind.INITIAL

        class QueryDb:
            async def execute(self, _statement):
                return _Rows([(job, attempt, order)])

        cases = await service.list_generation_manual_cases(QueryDb(), limit=10)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].job_id, job.id)
        self.assertEqual(cases[0].attempt_id, attempt.id)
        self.assertEqual(
            cases[0].reason_code,
            "provider_submission_human_required",
        )
        self.assertIsNone(job.next_retry_at)

    async def test_operator_can_bind_confirmed_task_without_provider_post(self) -> None:
        job, attempt, order, reservation = _case()
        db = _Db(job=job, attempt=attempt, order=order, reservation=reservation)
        reason = "Verified in the EvoLink provider console."
        evidence = _evidence(
            job,
            attempt,
            order,
            action="BIND_PROVIDER_TASK",
            reason=reason,
            provider_task_id="task_verified_123",
        )

        result = await service.resolve_generation_manual_case(
            db,
            job_id=job.id,
            action="BIND_PROVIDER_TASK",
            **evidence,
            operator_reason=reason,
            provider_task_id="task_verified_123",
            now=NOW,
        )

        self.assertEqual(result.next_action, "RECONCILE_PROVIDER_TASK")
        self.assertEqual(attempt.provider_job_id, "task_verified_123")
        self.assertEqual(attempt.status, GenerationAttemptStatus.SUBMITTED)
        self.assertEqual(attempt.submission_accounting_state, "PENDING")
        self.assertEqual(job.status, GenerationJobStatus.RECONCILING)
        self.assertEqual(job.next_retry_at, NOW)
        self.assertEqual(order.status, OrderStatus.GENERATING)

    async def test_existing_provider_task_binding_is_a_semantic_conflict(self) -> None:
        job, attempt, order, reservation = _case()
        db = _Db(
            job=job,
            attempt=attempt,
            order=order,
            reservation=reservation,
            existing_provider_attempt_id=uuid.uuid4(),
        )
        reason = "Verified in the EvoLink provider console."
        evidence = _evidence(
            job,
            attempt,
            order,
            action="BIND_PROVIDER_TASK",
            reason=reason,
            provider_task_id="task_already_bound",
        )

        with self.assertRaisesRegex(
            service.GenerationManualSettlementError,
            "provider_task_already_bound",
        ):
            await service.resolve_generation_manual_case(
                db,
                job_id=job.id,
                action="BIND_PROVIDER_TASK",
                **evidence,
                operator_reason=reason,
                provider_task_id="task_already_bound",
                now=NOW,
            )
        self.assertIsNone(attempt.provider_job_id)
        self.assertEqual(attempt.status, GenerationAttemptStatus.UNKNOWN)

    async def test_concurrent_unique_conflict_is_mapped_to_semantic_conflict(
        self,
    ) -> None:
        job, attempt, order, reservation = _case()
        conflict = IntegrityError(
            "UPDATE generation_attempts",
            {},
            RuntimeError(
                "duplicate key violates constraint "
                '"uq_generation_attempt_provider_job"'
            ),
        )
        db = _Db(
            job=job,
            attempt=attempt,
            order=order,
            reservation=reservation,
            flush_error=conflict,
        )
        reason = "Verified in the EvoLink provider console."
        evidence = _evidence(
            job,
            attempt,
            order,
            action="BIND_PROVIDER_TASK",
            reason=reason,
            provider_task_id="task_race",
        )

        with self.assertRaisesRegex(
            service.GenerationManualSettlementError,
            "provider_task_already_bound",
        ):
            await service.resolve_generation_manual_case(
                db,
                job_id=job.id,
                action="BIND_PROVIDER_TASK",
                **evidence,
                operator_reason=reason,
                provider_task_id="task_race",
                now=NOW,
            )

    async def test_confirmed_nonacceptance_authorizes_exactly_one_new_attempt_state(
        self,
    ) -> None:
        job, attempt, order, reservation = _case()
        grant = SimpleNamespace(revoked_at=None)
        db = _Db(
            job=job,
            attempt=attempt,
            order=order,
            reservation=reservation,
            grants=[grant],
        )
        reason = "Provider console confirms that no task was accepted."
        evidence = _evidence(
            job,
            attempt,
            order,
            action="CONFIRMED_NOT_ACCEPTED_RETRY",
            reason=reason,
            provider_accepted=False,
        )

        result = await service.resolve_generation_manual_case(
            db,
            job_id=job.id,
            action="CONFIRMED_NOT_ACCEPTED_RETRY",
            **evidence,
            operator_reason=reason,
            provider_accepted=False,
            now=NOW,
        )

        self.assertEqual(result.next_action, "ONE_OPERATOR_AUTHORIZED_SUBMISSION")
        self.assertEqual(attempt.status, GenerationAttemptStatus.PREPARED)
        self.assertEqual(job.status, GenerationJobStatus.ACTIVE)
        self.assertEqual(job.retry_count, 1)
        self.assertEqual(order.status, OrderStatus.GENERATING)
        self.assertEqual(grant.revoked_at, NOW)

        second_db = _Db(
            job=job,
            attempt=attempt,
            order=order,
            reservation=reservation,
        )
        with self.assertRaisesRegex(
            service.GenerationManualSettlementError,
            "not_resolvable",
        ):
            await service.resolve_generation_manual_case(
                second_db,
                job_id=job.id,
                action="CONFIRMED_NOT_ACCEPTED_RETRY",
                **evidence,
                operator_reason=reason,
                provider_accepted=False,
                now=NOW,
            )

    async def test_confirmed_nonacceptance_can_fail_and_release_reserved_credit(
        self,
    ) -> None:
        job, attempt, order, reservation = _case()
        db = _Db(job=job, attempt=attempt, order=order, reservation=reservation)
        settle = AsyncMock()
        reason = "Provider confirms the request was never accepted."
        evidence = _evidence(
            job,
            attempt,
            order,
            action="FAIL_AND_SETTLE",
            reason=reason,
            provider_accepted=False,
        )

        with patch.object(service, "release_or_refund_reservation", settle):
            result = await service.resolve_generation_manual_case(
                db,
                job_id=job.id,
                action="FAIL_AND_SETTLE",
                **evidence,
                operator_reason=reason,
                provider_accepted=False,
                now=NOW,
            )

        self.assertEqual(result.next_action, "TERMINAL")
        self.assertEqual(attempt.status, GenerationAttemptStatus.FAILED)
        self.assertEqual(job.status, GenerationJobStatus.FAILED)
        self.assertEqual(job.settlement_status, "RELEASED")
        self.assertEqual(order.status, OrderStatus.FAILED)
        settle.assert_awaited_once()
        self.assertEqual(settle.await_args.kwargs["settlement"], "RELEASE")
        self.assertTrue(settle.await_args.kwargs["pre_submission_confirmed"])

    async def test_confirmed_acceptance_captures_then_refunds_internal_credit(
        self,
    ) -> None:
        job, attempt, order, reservation = _case()
        db = _Db(job=job, attempt=attempt, order=order, reservation=reservation)
        capture = AsyncMock()
        settle = AsyncMock()
        reason = "Provider confirms acceptance but the task cannot be recovered."
        evidence = _evidence(
            job,
            attempt,
            order,
            action="FAIL_AND_SETTLE",
            reason=reason,
            provider_accepted=True,
        )

        with (
            patch.object(service, "capture_reservation", capture),
            patch.object(service, "release_or_refund_reservation", settle),
        ):
            await service.resolve_generation_manual_case(
                db,
                job_id=job.id,
                action="FAIL_AND_SETTLE",
                **evidence,
                operator_reason=reason,
                provider_accepted=True,
                now=NOW,
            )

        capture.assert_awaited_once()
        self.assertEqual(settle.await_args.kwargs["settlement"], "GENERATION_REFUND")
        self.assertEqual(attempt.status, GenerationAttemptStatus.FAILED)
        self.assertEqual(attempt.submission_accounting_state, "CAPTURED")
        self.assertEqual(job.settlement_status, "REFUNDED")

    async def test_resolution_rejects_missing_tampered_and_cross_attempt_evidence(
        self,
    ) -> None:
        reason = "Provider console confirms that no task was accepted."
        for mode in ("missing", "tampered", "cross-attempt"):
            with self.subTest(mode=mode):
                job, attempt, order, reservation = _case()
                db = _Db(
                    job=job,
                    attempt=attempt,
                    order=order,
                    reservation=reservation,
                )
                evidence = _evidence(
                    job,
                    attempt,
                    order,
                    action="CONFIRMED_NOT_ACCEPTED_RETRY",
                    reason=reason,
                    provider_accepted=False,
                )
                if mode == "missing":
                    evidence["evidence_store"].objects.clear()
                    expected = "evidence_not_found"
                elif mode == "tampered":
                    key = evidence["provider_evidence_object_key"]
                    evidence["evidence_store"].objects[key] += b" "
                    expected = "evidence_hash_mismatch"
                else:
                    evidence["provider_evidence_object_key"] = (
                        "operations/generation-manual-evidence/"
                        f"{uuid.uuid4()}/{evidence['provider_evidence_sha256']}.json"
                    )
                    expected = "evidence_reference_invalid"
                with self.assertRaisesRegex(
                    service.GenerationManualSettlementError,
                    expected,
                ):
                    await service.resolve_generation_manual_case(
                        db,
                        job_id=job.id,
                        action="CONFIRMED_NOT_ACCEPTED_RETRY",
                        **evidence,
                        operator_reason=reason,
                        provider_accepted=False,
                        now=NOW,
                    )
                self.assertEqual(attempt.status, GenerationAttemptStatus.UNKNOWN)

    def test_stale_evidence_is_rejected_before_storage(self) -> None:
        job, attempt, order, _reservation = _case()
        with self.assertRaisesRegex(
            service.GenerationManualSettlementError,
            "evidence_stale",
        ):
            service.build_generation_manual_evidence(
                job=job,
                attempt=attempt,
                order=order,
                action="CONFIRMED_NOT_ACCEPTED_RETRY",
                source_type="EVOLINK_SUPPORT",
                observation_reference="support-ticket-12345",
                observed_at=datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc),
                approval_id=uuid.uuid4(),
                operator_actor="ops@example.com",
                operator_reason="Provider confirms that no task was accepted.",
                provider_accepted=False,
                now=NOW,
            )

    def test_request_schema_forbids_automatic_or_unproven_retry(self) -> None:
        object_key = (
            "operations/generation-manual-evidence/"
            f"{uuid.uuid4()}/{'a' * 64}.json"
        )
        with self.assertRaises(ValueError):
            AdminGenerationManualResolutionRequest(
                action="CONFIRMED_NOT_ACCEPTED_RETRY",
                provider_evidence_object_key=object_key,
                provider_evidence_sha256="a" * 64,
                operator_reason="No provider fact was supplied.",
                provider_accepted=None,
            )
        with self.assertRaises(ValueError):
            AdminGenerationManualResolutionRequest(
                action="BIND_PROVIDER_TASK",
                provider_evidence_object_key=object_key,
                provider_evidence_sha256="a" * 64,
                operator_reason="Task binding without an exact task ID.",
            )


if __name__ == "__main__":
    unittest.main()
