"""Known-task Evolink reconciliation never becomes a resubmission path."""

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

from app.services.evolink_reconciliation_service import (
    ReconciliationResult,
    reconcile_evolink_attempt,
)
from app.services.evolink_service import EvolinkTaskFact, EvolinkTaskState
from app.services.job_lease_service import JobLease, StaleWorkerFence
from app.services.generation_repair_service import QaDisposition, QaDispositionKind


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class EvolinkReconciliationTest(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_exhaustion_stages_cleanup_before_failure_settlement(self) -> None:
        from app.services import generation_executor_service as executor

        lease = JobLease(uuid.uuid4(), "worker-a", uuid.uuid4(), 3, NOW, NOW.replace(minute=2))
        attempt_id = uuid.uuid4()
        db = SimpleNamespace(rollback=AsyncMock())
        calls: list[str] = []

        async def cleanup(*_args, **_kwargs):
            calls.append("cleanup")

        async def settle(*_args, **_kwargs):
            calls.append("settle")

        with (
            patch.object(
                executor,
                "build_delivery_assets",
                AsyncMock(side_effect=RuntimeError("storage unavailable")),
            ),
            patch.object(
                executor,
                "_retry_reconciliation",
                AsyncMock(return_value=False),
            ),
            patch.object(
                executor,
                "prepare_delivery_intents_for_terminal_cleanup",
                cleanup,
            ),
            patch.object(executor, "_settle_reconciliation_failure", settle),
        ):
            await executor._execute_ready_delivery(
                db,
                attempt_id=attempt_id,
                lease=lease,
            )

        self.assertEqual(calls, ["cleanup", "settle"])
        db.rollback.assert_awaited_once()

    async def test_due_reconciliation_is_selected_without_redis(self) -> None:
        from app.services import generation_executor_service as executor

        job_id = uuid.uuid4()

        class Rows:
            def all(self):
                return [job_id]

        db = SimpleNamespace(scalars=AsyncMock(return_value=Rows()))
        class SessionContext:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(
                executor,
                "async_session_maker",
                return_value=SessionContext(),
            ),
            patch.object(
                executor,
                "backend_runtime_coordinates",
                return_value=("dpl_current", "rtb_" + "a" * 64),
            ),
        ):
            work = await executor._pending_backend_work(limit=1, now=NOW)

        self.assertEqual(work, [("reconcile", job_id)])

    async def test_worker_success_path_accounts_stores_qa_and_disposes_exact_attempt(self) -> None:
        from app.services import generation_executor_service as executor

        lease = JobLease(uuid.uuid4(), "worker-a", uuid.uuid4(), 1, NOW, NOW.replace(minute=2))
        attempt_id = uuid.uuid4()
        user_id = uuid.uuid4()
        attempt = SimpleNamespace(
            id=attempt_id,
            status="SUBMITTED",
            submitted_at=NOW,
        )
        fact = EvolinkTaskFact(
            task_id="task_123",
            state=EvolinkTaskState.SUCCEEDED,
            output_urls=("https://provider.example.test/result.jpg",),
        )
        db = SimpleNamespace(commit=AsyncMock())

        class SessionContext:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *_args):
                return False

        accounting = AsyncMock(return_value=attempt)
        candidate = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
        qa = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
        disposition = AsyncMock(
            return_value=QaDisposition(QaDispositionKind.READY_FOR_DELIVERY)
        )
        delivery = AsyncMock()
        with (
            patch.object(executor, "async_session_maker", return_value=SessionContext()),
            patch.object(
                executor,
                "_load_reconciliation_attempt",
                AsyncMock(return_value=attempt),
            ),
            patch.object(executor, "ensure_accepted_submission_accounting", accounting),
            patch.object(
                executor,
                "reconcile_evolink_attempt",
                AsyncMock(
                    return_value=ReconciliationResult(
                        "SUCCEEDED",
                        "provider_task_succeeded",
                        fact,
                    )
                ),
            ),
            patch.object(executor, "persist_evolink_candidate", candidate),
            patch.object(executor, "run_and_persist_strict_qa", qa),
            patch.object(executor, "decide_next_generation_action", disposition),
            patch.object(
                executor,
                "settle_open_partner_consent_case_after_provider",
                AsyncMock(return_value=False),
            ),
            patch.object(executor, "build_delivery_assets", delivery, create=True),
            patch.object(
                executor,
                "_qa_retry_is_exhausted",
                AsyncMock(return_value=False),
            ),
        ):
            await executor._execute_generation_reconciliation(
                lease,
                user_id,
                attempt_id,
            )

        accounting.assert_awaited_once()
        candidate.assert_awaited_once()
        qa.assert_awaited_once()
        disposition.assert_awaited_once()
        delivery.assert_awaited_once_with(
            db,
            attempt_id=attempt_id,
            lease=lease,
        )

    async def test_missing_task_id_stays_unresolved_without_provider_io(self) -> None:
        lease = JobLease(uuid.uuid4(), "worker-a", uuid.uuid4(), 1, NOW, NOW.replace(minute=2))
        provider = SimpleNamespace(get_task=AsyncMock(), submit=AsyncMock())
        with patch(
            "app.services.evolink_reconciliation_service.load_reconcilable_attempt",
            new=AsyncMock(return_value=SimpleNamespace(provider_job_id=None)),
        ):
            result = await reconcile_evolink_attempt(
                AsyncMock(), attempt_id=uuid.uuid4(), lease=lease, provider=provider
            )

        self.assertEqual(result.state, "UNRESOLVED")
        self.assertEqual(result.reason, "provider_submission_human_required")
        provider.get_task.assert_not_awaited()
        provider.submit.assert_not_awaited()

    async def test_transient_known_task_query_never_submits_again(self) -> None:
        lease = JobLease(uuid.uuid4(), "worker-a", uuid.uuid4(), 1, NOW, NOW.replace(minute=2))
        provider = SimpleNamespace(
            get_task=AsyncMock(side_effect=httpx.ReadTimeout("query timeout")),
            submit=AsyncMock(),
        )
        with patch(
            "app.services.evolink_reconciliation_service.load_reconcilable_attempt",
            new=AsyncMock(return_value=SimpleNamespace(provider_job_id="task_123")),
        ):
            result = await reconcile_evolink_attempt(
                AsyncMock(), attempt_id=uuid.uuid4(), lease=lease, provider=provider
            )

        self.assertEqual(result.state, "PENDING")
        self.assertEqual(result.reason, "provider_query_transient")
        provider.submit.assert_not_awaited()

    async def test_terminal_failure_is_applied_only_after_fresh_fence_lock(self) -> None:
        lease = JobLease(uuid.uuid4(), "worker-a", uuid.uuid4(), 1, NOW, NOW.replace(minute=2))
        before = SimpleNamespace(provider_job_id="task_123")
        current = SimpleNamespace(
            provider_job_id="task_123",
            status="SUBMITTED",
            finished_at=None,
        )
        fact = EvolinkTaskFact(
            task_id="task_123",
            state=EvolinkTaskState.FAILED,
            output_urls=(),
            failure_code="model_rejected",
        )
        provider = SimpleNamespace(get_task=AsyncMock(return_value=fact), submit=AsyncMock())
        with patch(
            "app.services.evolink_reconciliation_service.load_reconcilable_attempt",
            new=AsyncMock(side_effect=(before, current)),
        ):
            result = await reconcile_evolink_attempt(
                AsyncMock(), attempt_id=uuid.uuid4(), lease=lease, provider=provider
            )

        self.assertEqual(result.state, "FAILED")
        self.assertEqual(current.status, "FAILED")
        self.assertIsNotNone(current.finished_at)
        provider.submit.assert_not_awaited()

    async def test_stale_fence_after_task_query_writes_no_terminal_fact(self) -> None:
        lease = JobLease(uuid.uuid4(), "worker-a", uuid.uuid4(), 1, NOW, NOW.replace(minute=2))
        before = SimpleNamespace(provider_job_id="task_123")
        fact = EvolinkTaskFact(
            task_id="task_123",
            state=EvolinkTaskState.SUCCEEDED,
            output_urls=("https://provider.example.test/result.jpg",),
            failure_code=None,
        )
        provider = SimpleNamespace(get_task=AsyncMock(return_value=fact), submit=AsyncMock())
        with patch(
            "app.services.evolink_reconciliation_service.load_reconcilable_attempt",
            new=AsyncMock(
                side_effect=(
                    before,
                    StaleWorkerFence("stale", lease.job_id),
                )
            ),
        ):
            with self.assertRaises(StaleWorkerFence):
                await reconcile_evolink_attempt(
                    AsyncMock(), attempt_id=uuid.uuid4(), lease=lease, provider=provider
                )

        provider.get_task.assert_awaited_once_with("task_123")
        provider.submit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
