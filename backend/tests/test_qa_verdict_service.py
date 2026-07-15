"""Immutable strict-QA verdict persistence under the current Worker fence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import unittest
import uuid

from app.models.qa_verdict import QaDecision
from app.schemas.qa import StrictQaResponse, failed_qa_response
from app.services.job_lease_service import JobLease, StaleWorkerFence
from app.services.qa_verdict_service import (
    QaInfrastructureError,
    QaSnapshot,
    build_qa_verdict,
    run_and_persist_strict_qa,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
CHECKS = (
    "technical",
    "identity",
    "subject",
    "safety",
    "style",
    "composition",
    "exposure",
    "watermark",
)


def _pass_response() -> StrictQaResponse:
    check = {"passed": True, "score": 1.0, "reason_codes": []}
    return StrictQaResponse.model_validate(
        {
            "schema_version": "vowpic.qa.v1",
            "checker_version": "vowpic-checker.v1",
            "model_version": "vowpic-vision.v1",
            "passed": True,
            "reason_codes": [],
            "checks": {name: dict(check) for name in CHECKS},
        }
    )


def _snapshot(*, existing=None) -> QaSnapshot:
    payload = b"private-candidate"
    job_id = uuid.uuid4()
    order_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    candidate = SimpleNamespace(
        id=candidate_id,
        owner_user_id=owner_id,
        order_id=order_id,
        job_id=job_id,
        object_key="private/candidate.jpg",
        sha256=sha256(payload).hexdigest(),
        byte_size=len(payload),
        mime_type="image/jpeg",
    )
    source = SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id=owner_id,
        order_id=order_id,
        job_id=job_id,
    )
    return QaSnapshot(
        job=SimpleNamespace(id=job_id, order_id=order_id, active_attempt_id=attempt_id),
        attempt=SimpleNamespace(
            id=attempt_id,
            job_id=job_id,
            result_asset_id=candidate_id,
        ),
        order=SimpleNamespace(
            id=order_id,
            user_id=owner_id,
            product_policy_snapshot={"subject_count": 1},
            template_id="solo_royal_castle",
        ),
        candidate=candidate,
        source_assets=(source,),
        existing_verdict=existing,
    )


class QaVerdictServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_persisted_shape_contains_only_normalized_scores_and_hash(self) -> None:
        snapshot = _snapshot()
        verdict = build_qa_verdict(snapshot=snapshot, response=_pass_response())

        self.assertEqual(verdict.decision, QaDecision.PASS)
        self.assertEqual(set(verdict.metrics), {"scores"})
        self.assertEqual(set(verdict.metrics["scores"]), set(CHECKS))
        self.assertEqual(len(verdict.response_sha256), 64)
        self.assertNotIn("embedding", repr(verdict.metrics).lower())

    async def test_stale_fence_after_semantic_qa_writes_no_verdict(self) -> None:
        snapshot = _snapshot()
        payload = b"private-candidate"
        lease = JobLease(
            snapshot.job.id,
            "worker-a",
            uuid.uuid4(),
            4,
            NOW,
            NOW + timedelta(seconds=120),
        )
        db = SimpleNamespace(add=Mock(), flush=AsyncMock(), commit=AsyncMock())
        store = SimpleNamespace(read_private=Mock(return_value=payload))
        evaluator = AsyncMock(return_value=_pass_response())
        issued = SimpleNamespace(
            read_url="https://grant.example.test/private",
            grant=SimpleNamespace(revoked_at=None),
        )
        with (
            patch(
                "app.services.qa_verdict_service._snapshot_qa_context",
                AsyncMock(
                    side_effect=(
                        snapshot,
                        snapshot,
                        snapshot,
                        StaleWorkerFence("stale", snapshot.job.id),
                    )
                ),
            ),
            patch(
                "app.services.qa_verdict_service.create_provider_grant",
                AsyncMock(return_value=issued),
            ),
        ):
            with self.assertRaises(StaleWorkerFence):
                await run_and_persist_strict_qa(
                    db,
                    attempt_id=snapshot.attempt.id,
                    lease=lease,
                    evaluator=evaluator,
                    object_store=store,
                    now=NOW,
                )

        evaluator.assert_awaited_once()
        db.add.assert_not_called()

    async def test_existing_verdict_replays_without_storage_or_qa_io(self) -> None:
        existing = SimpleNamespace(id=uuid.uuid4())
        snapshot = _snapshot(existing=existing)
        lease = JobLease(
            snapshot.job.id,
            "worker-a",
            uuid.uuid4(),
            4,
            NOW,
            NOW + timedelta(seconds=120),
        )
        evaluator = AsyncMock()
        store = SimpleNamespace(read_private=Mock())
        with patch(
            "app.services.qa_verdict_service._snapshot_qa_context",
            AsyncMock(return_value=snapshot),
        ):
            result = await run_and_persist_strict_qa(
                SimpleNamespace(),
                attempt_id=snapshot.attempt.id,
                lease=lease,
                evaluator=evaluator,
                object_store=store,
                now=NOW,
            )

        self.assertIs(result, existing)
        evaluator.assert_not_awaited()
        store.read_private.assert_not_called()

    async def test_operational_failure_is_not_persisted_as_candidate_repair(self) -> None:
        snapshot = _snapshot()
        payload = b"private-candidate"
        lease = JobLease(
            snapshot.job.id,
            "worker-a",
            uuid.uuid4(),
            4,
            NOW,
            NOW + timedelta(seconds=120),
        )
        db = SimpleNamespace(add=Mock(), flush=AsyncMock(), commit=AsyncMock())
        issued = SimpleNamespace(
            read_url="https://grant.example.test/private",
            grant=SimpleNamespace(revoked_at=None),
        )
        with (
            patch(
                "app.services.qa_verdict_service._snapshot_qa_context",
                AsyncMock(side_effect=(snapshot, snapshot, snapshot, snapshot)),
            ),
            patch(
                "app.services.qa_verdict_service.create_provider_grant",
                AsyncMock(return_value=issued),
            ),
        ):
            with self.assertRaises(QaInfrastructureError):
                await run_and_persist_strict_qa(
                    db,
                    attempt_id=snapshot.attempt.id,
                    lease=lease,
                    evaluator=AsyncMock(return_value=failed_qa_response("vision_error")),
                    object_store=SimpleNamespace(read_private=Mock(return_value=payload)),
                    now=NOW,
                )

        db.add.assert_not_called()

    async def test_exhausted_operational_failure_persists_reject_for_refund(self) -> None:
        snapshot = _snapshot()
        payload = b"private-candidate"
        lease = JobLease(
            snapshot.job.id,
            "worker-a",
            uuid.uuid4(),
            4,
            NOW,
            NOW + timedelta(seconds=120),
        )
        db = SimpleNamespace(add=Mock(), flush=AsyncMock(), commit=AsyncMock())
        issued = SimpleNamespace(
            read_url="https://grant.example.test/private",
            grant=SimpleNamespace(revoked_at=None),
        )
        with (
            patch(
                "app.services.qa_verdict_service._snapshot_qa_context",
                AsyncMock(side_effect=(snapshot, snapshot, snapshot, snapshot)),
            ),
            patch(
                "app.services.qa_verdict_service.create_provider_grant",
                AsyncMock(return_value=issued),
            ),
        ):
            verdict = await run_and_persist_strict_qa(
                db,
                attempt_id=snapshot.attempt.id,
                lease=lease,
                evaluator=AsyncMock(return_value=failed_qa_response("vision_error")),
                object_store=SimpleNamespace(read_private=Mock(return_value=payload)),
                persist_infrastructure_failure=True,
                now=NOW,
            )

        self.assertEqual(verdict.decision, QaDecision.REJECT)
        db.add.assert_called_once_with(verdict)


if __name__ == "__main__":
    unittest.main()
