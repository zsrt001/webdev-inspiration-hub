"""Fenced Provider-output transfer into private candidate authority."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest
import uuid

from app.models.generation_attempt import GenerationAttemptStatus
from app.models.media_asset import MediaAssetRole, MediaAssetStatus
from app.services.evolink_service import EvolinkTaskFact, EvolinkTaskState
from app.services.generation_candidate_service import (
    CandidateContext,
    build_candidate_intent,
    persist_evolink_candidate,
)
from app.services.job_lease_service import JobLease, StaleWorkerFence
from app.services.media_asset_service import ValidatedImageBytes


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class _Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_private(self, key: str, data: bytes, _content_type: str) -> None:
        if key in self.objects:
            raise FileExistsError(key)
        self.objects[key] = bytes(data)

    def read_private(self, key: str) -> bytes:
        return self.objects[key]

    def delete_private(self, key: str):
        self.objects.pop(key, None)


def _context() -> CandidateContext:
    job_id = uuid.uuid4()
    order_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    job = SimpleNamespace(id=job_id, order_id=order_id, active_attempt_id=attempt_id)
    attempt = SimpleNamespace(
        id=attempt_id,
        job_id=job_id,
        provider_job_id="task_123",
        status=GenerationAttemptStatus.SUBMITTED,
        result_asset_id=None,
        finished_at=None,
    )
    order = SimpleNamespace(
        id=order_id,
        user_id=owner_id,
        generation_job_id=job_id,
        status="GENERATING",
        expires_at=NOW + timedelta(days=30),
    )
    return CandidateContext(job=job, attempt=attempt, order=order, existing_asset=None)


class GenerationCandidateTest(unittest.IsolatedAsyncioTestCase):
    def test_intent_is_deterministic_private_and_contains_no_provider_url(self) -> None:
        context = _context()
        payload = b"canonical-jpeg"
        validated = ValidatedImageBytes(
            content=payload,
            mime_type="image/jpeg",
            width=900,
            height=1200,
            byte_size=len(payload),
            sha256=sha256(payload).hexdigest(),
        )

        first = build_candidate_intent(context=context, validated=validated, now=NOW)
        second = build_candidate_intent(context=context, validated=validated, now=NOW)

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.object_key, second.object_key)
        self.assertEqual(first.role, MediaAssetRole.CANDIDATE)
        self.assertEqual(first.status, MediaAssetStatus.PENDING_UPLOAD)
        self.assertEqual(first.access_level, "private")
        self.assertNotIn("provider.example", first.object_key)

    async def test_success_activates_only_after_store_readback_and_fresh_fence(self) -> None:
        context = _context()
        payload = b"canonical-jpeg"
        validated = ValidatedImageBytes(
            content=payload,
            mime_type="image/jpeg",
            width=900,
            height=1200,
            byte_size=len(payload),
            sha256=sha256(payload).hexdigest(),
        )
        candidate = build_candidate_intent(context=context, validated=validated, now=NOW)
        fact = EvolinkTaskFact(
            task_id="task_123",
            state=EvolinkTaskState.SUCCEEDED,
            output_urls=("https://provider.example.test/result.jpg",),
        )
        lease = JobLease(
            context.job.id,
            "worker-a",
            uuid.uuid4(),
            4,
            NOW,
            NOW + timedelta(seconds=120),
        )
        fetcher = AsyncMock(return_value=validated)
        db = SimpleNamespace(commit=AsyncMock())
        store = _Store()
        with (
            patch(
                "app.services.generation_candidate_service._load_candidate_context",
                AsyncMock(side_effect=(context, context, context, context)),
            ),
            patch(
                "app.services.generation_candidate_service._load_or_create_candidate_intent",
                AsyncMock(return_value=candidate),
            ),
            patch(
                "app.services.generation_candidate_service._reject_source_duplicate",
                AsyncMock(),
            ),
        ):
            result = await persist_evolink_candidate(
                db,
                attempt_id=context.attempt.id,
                lease=lease,
                fact=fact,
                fetcher=fetcher,
                object_store=store,
                now=NOW,
            )

        self.assertIs(result, candidate)
        fetcher.assert_awaited_once_with(fact.output_urls[0])
        self.assertEqual(store.objects[candidate.object_key], payload)
        self.assertEqual(candidate.status, MediaAssetStatus.ACTIVE)
        self.assertEqual(context.attempt.result_asset_id, candidate.id)
        self.assertEqual(context.attempt.status, GenerationAttemptStatus.FINISHED)
        self.assertEqual(context.order.status, "QA_PENDING")
        db.commit.assert_awaited_once()  # final activation; intent helper owns its commit

    async def test_stale_fence_after_object_write_leaves_only_pending_intent(self) -> None:
        context = _context()
        payload = b"canonical-jpeg"
        validated = ValidatedImageBytes(
            content=payload,
            mime_type="image/jpeg",
            width=900,
            height=1200,
            byte_size=len(payload),
            sha256=sha256(payload).hexdigest(),
        )
        candidate = build_candidate_intent(context=context, validated=validated, now=NOW)
        fact = EvolinkTaskFact(
            task_id="task_123",
            state=EvolinkTaskState.SUCCEEDED,
            output_urls=("https://provider.example.test/result.jpg",),
        )
        lease = JobLease(
            context.job.id,
            "worker-a",
            uuid.uuid4(),
            4,
            NOW,
            NOW + timedelta(seconds=120),
        )
        db = SimpleNamespace(commit=AsyncMock())
        store = _Store()
        with (
            patch(
                "app.services.generation_candidate_service._load_candidate_context",
                AsyncMock(
                    side_effect=(
                        context,
                        context,
                        context,
                        StaleWorkerFence("stale", context.job.id),
                    )
                ),
            ),
            patch(
                "app.services.generation_candidate_service._load_or_create_candidate_intent",
                AsyncMock(return_value=candidate),
            ),
            patch(
                "app.services.generation_candidate_service._reject_source_duplicate",
                AsyncMock(),
            ),
        ):
            with self.assertRaises(StaleWorkerFence):
                await persist_evolink_candidate(
                    db,
                    attempt_id=context.attempt.id,
                    lease=lease,
                    fact=fact,
                    fetcher=AsyncMock(return_value=validated),
                    object_store=store,
                    now=NOW,
                )

        self.assertEqual(candidate.status, MediaAssetStatus.PENDING_UPLOAD)
        self.assertIsNone(context.attempt.result_asset_id)
        self.assertEqual(context.attempt.status, GenerationAttemptStatus.SUBMITTED)
        self.assertIn(candidate.object_key, store.objects)


if __name__ == "__main__":
    unittest.main()
