"""Signed EvoLink callbacks recover one lost submit response without replay."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
import sys
import unittest
import uuid


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.generation_attempt import GenerationAttemptKind, GenerationAttemptStatus
from app.models.generation_job import GenerationJobStatus
from app.services import evolink_service as evolink_module
from app.services.evolink_callback_service import (
    EvolinkCallbackError,
    EvolinkCallbackResult,
    bind_evolink_callback_task,
)
from app.services.evolink_service import (
    EvolinkTaskFact,
    EvolinkTaskState,
    build_evolink_callback_token,
)
from app.services.generation_attempt_service import mark_submission_unknown


ATTEMPT_ID = uuid.UUID("00000000-0000-4000-8000-000000000071")
JOB_ID = uuid.UUID("00000000-0000-4000-8000-000000000072")
CLAIM_ID = uuid.UUID("00000000-0000-4000-8000-000000000073")
NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)
SECRET = "callback-test-secret-key-at-least-32-bytes"


def _terminal_fact(task_id: str = "task-unified-1756817821-test") -> EvolinkTaskFact:
    return EvolinkTaskFact(
        task_id=task_id,
        state=EvolinkTaskState.SUCCEEDED,
        output_urls=("https://assets.example.com/result.png",),
    )


class EvolinkCallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_http_callback_is_mounted_and_commits_one_terminal_fact(self) -> None:
        from app.core.database import get_db
        from app.main import app
        from app.routers import provider_callbacks

        db = SimpleNamespace(commit=AsyncMock())
        bind = AsyncMock(
            return_value=EvolinkCallbackResult(
                state="BOUND",
                task_id="task-unified-1756817821-test",
                job_id=JOB_ID,
            )
        )
        reconcile = AsyncMock()

        async def database_override():
            yield db

        token = build_evolink_callback_token(ATTEMPT_ID, secret_key=SECRET)
        original_overrides = dict(app.dependency_overrides)
        had_runtime_blocker = hasattr(app.state, "runtime_config_blocked")
        original_runtime_blocker = getattr(app.state, "runtime_config_blocked", False)
        app.state.runtime_config_blocked = False
        app.dependency_overrides[get_db] = database_override
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        try:
            with (
                patch.object(provider_callbacks, "bind_evolink_callback_task", bind),
                patch.object(
                    provider_callbacks,
                    "reconcile_generation_job",
                    reconcile,
                ),
            ):
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    response = await client.post(
                        f"/api/v1/provider-callbacks/evolink/{ATTEMPT_ID}/{token}",
                        json={
                            "data": {
                                "task_id": "task-unified-1756817821-test",
                                "status": "completed",
                                "results": ["https://assets.example.com/result.png"],
                            }
                        },
                    )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original_overrides)
            if had_runtime_blocker:
                app.state.runtime_config_blocked = original_runtime_blocker
            elif hasattr(app.state, "runtime_config_blocked"):
                delattr(app.state, "runtime_config_blocked")

        self.assertEqual(response.status_code, 204, response.text)
        bind.assert_awaited_once()
        self.assertIs(bind.await_args.args[0], db)
        call = bind.await_args.kwargs
        self.assertEqual(call["attempt_id"], ATTEMPT_ID)
        self.assertEqual(call["token"], token)
        self.assertEqual(call["fact"].task_id, "task-unified-1756817821-test")
        db.commit.assert_awaited_once_with()
        reconcile.assert_awaited_once_with(JOB_ID)

    async def test_unknown_attempt_binds_once_and_reschedules_reconciliation(self) -> None:
        attempt = SimpleNamespace(
            id=ATTEMPT_ID,
            job_id=JOB_ID,
            provider="evolink",
            kind=GenerationAttemptKind.INITIAL,
            status=GenerationAttemptStatus.UNKNOWN,
            provider_job_id=None,
            submitted_at=None,
            submission_accounting_state="NOT_CAPTURED",
        )
        job = SimpleNamespace(
            id=JOB_ID,
            active_attempt_id=ATTEMPT_ID,
            status=GenerationJobStatus.RECONCILING,
            lease_owner=None,
            lease_claim_id=None,
            lease_expires_at=None,
            heartbeat_at=None,
            next_retry_at=None,
            last_error_code="provider_submission_human_required",
            last_error_detail="submit_response_lost",
        )
        statements: list[str] = []

        async def scalar(statement):
            statements.append(str(statement))
            return (JOB_ID, job, attempt)[len(statements) - 1]

        db = SimpleNamespace(scalar=AsyncMock(side_effect=scalar))
        token = build_evolink_callback_token(ATTEMPT_ID, secret_key=SECRET)

        with patch.object(evolink_module.settings, "secret_key", SECRET):
            result = await bind_evolink_callback_task(
                db,
                attempt_id=ATTEMPT_ID,
                token=token,
                fact=_terminal_fact(),
                now=NOW,
            )

        self.assertEqual(result.state, "BOUND")
        self.assertEqual(attempt.provider_job_id, result.task_id)
        self.assertEqual(attempt.status, GenerationAttemptStatus.SUBMITTED)
        self.assertEqual(attempt.submitted_at, NOW)
        self.assertEqual(attempt.submission_accounting_state, "PENDING")
        self.assertEqual(job.next_retry_at, NOW)
        self.assertIsNone(job.last_error_code)
        self.assertIsNone(job.last_error_detail)
        self.assertEqual(db.scalar.await_count, 3)
        self.assertIn("generation_attempts.job_id", statements[0])
        self.assertIn("FROM generation_jobs", statements[1])
        self.assertIn("FROM generation_attempts", statements[2])

    async def test_invalid_token_fails_before_database_lookup(self) -> None:
        db = SimpleNamespace(scalar=AsyncMock())
        with patch.object(evolink_module.settings, "secret_key", SECRET):
            with self.assertRaisesRegex(EvolinkCallbackError, "not_found"):
                await bind_evolink_callback_task(
                    db,
                    attempt_id=ATTEMPT_ID,
                    token="0" * 64,
                    fact=_terminal_fact(),
                )
        db.scalar.assert_not_awaited()

    async def test_duplicate_callback_is_idempotent_and_conflict_is_rejected(self) -> None:
        attempt = SimpleNamespace(
            id=ATTEMPT_ID,
            job_id=JOB_ID,
            provider="evolink",
            status=GenerationAttemptStatus.SUBMITTED,
            provider_job_id="task-unified-1756817821-test",
            submitted_at=NOW,
        )
        token = build_evolink_callback_token(ATTEMPT_ID, secret_key=SECRET)
        job = SimpleNamespace(
            id=JOB_ID,
            active_attempt_id=ATTEMPT_ID,
            status=GenerationJobStatus.RECONCILING,
            lease_owner=None,
            lease_claim_id=None,
            lease_expires_at=None,
            heartbeat_at=None,
            next_retry_at=None,
        )
        with patch.object(evolink_module.settings, "secret_key", SECRET):
            same_db = SimpleNamespace(
                scalar=AsyncMock(side_effect=[JOB_ID, job, attempt])
            )
            same = await bind_evolink_callback_task(
                same_db,
                attempt_id=ATTEMPT_ID,
                token=token,
                fact=_terminal_fact(),
            )
            self.assertEqual(same.state, "UNCHANGED")
            with self.assertRaisesRegex(EvolinkCallbackError, "task_conflict"):
                await bind_evolink_callback_task(
                    SimpleNamespace(
                        scalar=AsyncMock(side_effect=[JOB_ID, job, attempt])
                    ),
                    attempt_id=ATTEMPT_ID,
                    token=token,
                    fact=_terminal_fact("task-unified-1756817821-other"),
                )

    async def test_submitting_callback_becomes_submitted_without_stealing_live_lease(
        self,
    ) -> None:
        claim_id = uuid.uuid4()
        lease_expires_at = NOW.replace(hour=NOW.hour + 1)
        attempt = SimpleNamespace(
            id=ATTEMPT_ID,
            job_id=JOB_ID,
            provider="evolink",
            kind=GenerationAttemptKind.INITIAL,
            status=GenerationAttemptStatus.SUBMITTING,
            provider_job_id=None,
            submitted_at=None,
            submission_accounting_state="NOT_CAPTURED",
        )
        job = SimpleNamespace(
            id=JOB_ID,
            active_attempt_id=ATTEMPT_ID,
            status=GenerationJobStatus.ACTIVE,
            lease_owner="api:active",
            lease_claim_id=claim_id,
            lease_expires_at=lease_expires_at,
            heartbeat_at=NOW,
            next_retry_at=None,
            last_error_code=None,
            last_error_detail=None,
        )
        db = SimpleNamespace(
            scalar=AsyncMock(side_effect=[JOB_ID, job, attempt])
        )
        token = build_evolink_callback_token(ATTEMPT_ID, secret_key=SECRET)

        with patch.object(evolink_module.settings, "secret_key", SECRET):
            result = await bind_evolink_callback_task(
                db,
                attempt_id=ATTEMPT_ID,
                token=token,
                fact=_terminal_fact(),
                now=NOW,
            )

        self.assertEqual(result.job_id, JOB_ID)
        self.assertEqual(attempt.status, GenerationAttemptStatus.SUBMITTED)
        self.assertEqual(attempt.submitted_at, NOW)
        self.assertEqual(job.status, GenerationJobStatus.ACTIVE)
        self.assertEqual(job.lease_owner, "api:active")
        self.assertEqual(job.lease_claim_id, claim_id)
        self.assertEqual(job.next_retry_at, NOW)

    async def test_nonterminal_callback_cannot_bind_a_provider_task(self) -> None:
        db = SimpleNamespace(scalar=AsyncMock())
        token = build_evolink_callback_token(ATTEMPT_ID, secret_key=SECRET)
        pending = EvolinkTaskFact(
            task_id="task-unified-1756817821-test",
            state=EvolinkTaskState.PENDING,
        )
        with patch.object(evolink_module.settings, "secret_key", SECRET):
            with self.assertRaisesRegex(EvolinkCallbackError, "not_terminal"):
                await bind_evolink_callback_task(
                    db,
                    attempt_id=ATTEMPT_ID,
                    token=token,
                    fact=pending,
                )
        db.scalar.assert_not_awaited()

    async def test_unknown_transition_schedules_immediate_retry_when_callback_won_race(self) -> None:
        lease = SimpleNamespace(
            job_id=JOB_ID,
            worker_id="worker-1",
            claim_id=CLAIM_ID,
            fencing_token=7,
        )
        job = SimpleNamespace(
            id=JOB_ID,
            order_id=uuid.UUID("00000000-0000-4000-8000-000000000074"),
            status=GenerationJobStatus.ACTIVE,
            next_retry_at=None,
            last_error_code=None,
            lease_owner="worker-1",
            lease_claim_id=CLAIM_ID,
            lease_expires_at=NOW,
            heartbeat_at=NOW,
        )
        attempt = SimpleNamespace(
            id=ATTEMPT_ID,
            job_id=JOB_ID,
            status=GenerationAttemptStatus.SUBMITTING,
            provider_job_id="task-unified-1756817821-test",
        )
        db = SimpleNamespace(scalar=AsyncMock(return_value=None))
        with (
            patch(
                "app.services.generation_attempt_service.require_current_generation_fence",
                AsyncMock(return_value=job),
            ),
            patch(
                "app.services.generation_attempt_service._locked_attempt",
                AsyncMock(return_value=attempt),
            ),
        ):
            await mark_submission_unknown(
                db,
                attempt_id=ATTEMPT_ID,
                lease=lease,
                reason="submit_response_lost",
                now=NOW,
            )

        self.assertEqual(attempt.status, GenerationAttemptStatus.UNKNOWN)
        self.assertEqual(job.status, GenerationJobStatus.RECONCILING)
        self.assertEqual(job.next_retry_at, NOW)


if __name__ == "__main__":
    unittest.main()
