"""Website-backend generation execution contract."""

from pathlib import Path
from datetime import datetime, timezone
import sys
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import ANY, AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.generation_job import GENERATION_JOB_PAYLOAD_VERSION  # noqa: E402
from app.services import generation_executor_service as executor  # noqa: E402
from app.services.generation_repair_service import (  # noqa: E402
    GENERATION_ATTEMPT_PAYLOAD_VERSION,
)


class BackendGenerationExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_job_execution_uses_durable_entry_without_redis(self) -> None:
        job_id = uuid.uuid4()
        generate = AsyncMock()

        with patch.object(executor, "generate_order_v1", generate):
            await executor.execute_generation_job(job_id, executor_id="api:test")

        generate.assert_awaited_once_with(
            {"worker_id": "api:test"},
            str(job_id),
            GENERATION_JOB_PAYLOAD_VERSION,
        )
    async def test_maintenance_routes_persisted_work_without_queue(self) -> None:
        job_id = uuid.uuid4()
        attempt_id = uuid.uuid4()
        reconcile_id = uuid.uuid4()
        execute_job = AsyncMock()
        execute_attempt = AsyncMock()
        reconcile_job = AsyncMock()

        with (
            patch.object(
                executor,
                "_pending_backend_work",
                new=AsyncMock(
                    return_value=[
                        ("job", job_id),
                        ("attempt", attempt_id),
                        ("reconcile", reconcile_id),
                    ]
                ),
            ),
            patch.object(executor, "backend_executor_id", return_value="api:test"),
            patch.object(executor, "execute_generation_job", execute_job),
            patch.object(executor, "execute_generation_attempt", execute_attempt),
            patch.object(executor, "reconcile_generation_job", reconcile_job),
            patch.object(
                executor,
                "count_generation_manual_cases",
                new=AsyncMock(return_value=0),
            ),
        ):
            result = await executor.run_backend_generation_maintenance(limit=3)

        self.assertEqual(
            result,
            {
                "selected": 3,
                "submitted": 2,
                "reconciled": 1,
                "failed": 0,
                "human_required": 0,
            },
        )
        execute_job.assert_awaited_once_with(job_id, executor_id="api:test")
        execute_attempt.assert_awaited_once_with(attempt_id, executor_id="api:test")
        reconcile_job.assert_awaited_once_with(reconcile_id, executor_id="api:test")

    async def test_repair_execution_keeps_existing_payload_contract(self) -> None:
        attempt_id = uuid.uuid4()
        generate = AsyncMock()

        with patch.object(executor, "generate_attempt_v1", generate):
            await executor.execute_generation_attempt(
                attempt_id,
                executor_id="api:test",
            )

        generate.assert_awaited_once_with(
            {"worker_id": "api:test"},
            str(attempt_id),
            GENERATION_ATTEMPT_PAYLOAD_VERSION,
        )

    async def test_authenticated_order_progress_reconciles_one_due_job(self) -> None:
        order_id = uuid.uuid4()
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        job = SimpleNamespace(
            id=job_id,
            status=executor.GenerationJobStatus.RECONCILING,
            next_retry_at=now,
            lease_expires_at=None,
            active_attempt_id=uuid.uuid4(),
        )
        reconcile = AsyncMock()

        class _Result:
            def one_or_none(self):
                return job, user_id

        class _Db:
            async def execute(self, _statement):
                return _Result()

        class _Session:
            async def __aenter__(self):
                return _Db()

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(executor, "async_session_maker", return_value=_Session()),
            patch.object(executor, "reconcile_generation_job", reconcile),
        ):
            action = await executor.advance_order_generation_once(
                order_id=order_id,
                user_id=user_id,
                executor_id="api:test",
                now=now,
            )

        self.assertEqual(action, "reconcile")
        reconcile.assert_awaited_once_with(job_id, executor_id="api:test")

    async def test_authenticated_order_progress_waits_until_retry_is_due(self) -> None:
        order_id = uuid.uuid4()
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        job = SimpleNamespace(
            id=job_id,
            status=executor.GenerationJobStatus.RECONCILING,
            next_retry_at=now.replace(year=now.year + 1),
            lease_expires_at=None,
            active_attempt_id=uuid.uuid4(),
        )
        reconcile = AsyncMock()

        class _Result:
            def one_or_none(self):
                return job, user_id

        class _Db:
            async def execute(self, _statement):
                return _Result()

        class _Session:
            async def __aenter__(self):
                return _Db()

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(executor, "async_session_maker", return_value=_Session()),
            patch.object(executor, "reconcile_generation_job", reconcile),
        ):
            action = await executor.advance_order_generation_once(
                order_id=order_id,
                user_id=user_id,
                executor_id="api:test",
                now=now,
            )

        self.assertEqual(action, "idle")
        reconcile.assert_not_awaited()

    async def test_one_failed_item_does_not_abort_the_remaining_batch(self) -> None:
        failed_job = uuid.uuid4()
        repair_attempt = uuid.uuid4()
        reconcile_job = uuid.uuid4()
        execute_job = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        execute_attempt = AsyncMock()
        reconcile = AsyncMock()
        with (
            patch.object(
                executor,
                "_pending_backend_work",
                new=AsyncMock(
                    return_value=[
                        ("job", failed_job),
                        ("attempt", repair_attempt),
                        ("reconcile", reconcile_job),
                    ]
                ),
            ),
            patch.object(executor, "backend_executor_id", return_value="api:test"),
            patch.object(executor, "execute_generation_job", execute_job),
            patch.object(executor, "execute_generation_attempt", execute_attempt),
            patch.object(executor, "reconcile_generation_job", reconcile),
            patch.object(
                executor,
                "count_generation_manual_cases",
                new=AsyncMock(return_value=2),
            ),
        ):
            result = await executor.run_backend_generation_maintenance(limit=3)

        self.assertEqual(
            result,
            {
                "selected": 3,
                "submitted": 1,
                "reconciled": 1,
                "failed": 1,
                "human_required": 2,
            },
        )
        execute_attempt.assert_awaited_once_with(repair_attempt, executor_id="api:test")
        reconcile.assert_awaited_once_with(reconcile_job, executor_id="api:test")

    async def test_pending_queries_recover_all_compatible_v1_work(self) -> None:
        statements = []

        class _Rows:
            def all(self):
                return []

        class _Db:
            async def scalars(self, statement):
                statements.append(statement)
                return _Rows()

        class _Session:
            async def __aenter__(self):
                return _Db()

            async def __aexit__(self, *_args):
                return False

        runtime = SimpleNamespace(
            vercel_deployment_id="dpl_current",
            runtime_bundle_id="rtb_" + "a" * 64,
        )
        with (
            patch.object(executor, "settings", runtime),
            patch.object(executor, "async_session_maker", return_value=_Session()),
        ):
            result = await executor._pending_backend_work(
                limit=2,
                now=datetime.now(timezone.utc),
            )

        self.assertEqual(result, [])
        self.assertEqual(len(statements), 2)
        for statement in statements:
            compiled = statement.compile()
            where_clause = str(statement).split("WHERE", 1)[1]
            self.assertIn("payload_version", where_clause)
            self.assertIn(GENERATION_JOB_PAYLOAD_VERSION, compiled.params.values())
            self.assertNotIn("api_deployment_id", where_clause)
            self.assertNotIn("runtime_bundle_id", where_clause)

    async def test_capability_uses_active_backend_not_job_origin(self) -> None:
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        job = SimpleNamespace(
            id=job_id,
            api_deployment_id="dpl_origin",
            runtime_bundle_id="rtb_" + "b" * 64,
        )
        require_capability = AsyncMock()

        class _Result:
            def one_or_none(self):
                return job, user_id

        class _Db:
            async def execute(self, _statement):
                return _Result()

        class _Session:
            async def __aenter__(self):
                return _Db()

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(executor, "async_session_maker", return_value=_Session()),
            patch.object(
                executor,
                "backend_runtime_coordinates",
                return_value=("dpl_current", "rtb_" + "a" * 64),
            ),
            patch.object(
                executor,
                "require_backend_capability",
                require_capability,
            ),
        ):
            loaded_job, loaded_user = await executor._load_capability_context(job_id)

        self.assertIs(loaded_job, job)
        self.assertEqual(loaded_user, user_id)
        require_capability.assert_awaited_once_with(
            ANY,
            executor.Capability.GENERATION,
            deployment_id="dpl_current",
            runtime_bundle_id="rtb_" + "a" * 64,
            user_id=user_id,
        )


if __name__ == "__main__":
    unittest.main()
