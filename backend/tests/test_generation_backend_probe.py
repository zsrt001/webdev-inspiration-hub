"""Fail-closed readiness proof for website-backend generation execution."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import generation_executor_service as executor_module  # noqa: E402
from app.services import generation_service as generation_module  # noqa: E402
from app.services.generation_service import GenerationService  # noqa: E402


class GenerationBackendProbeTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _runtime_patch(**overrides):
        values = {
            "runtime_environment": "production",
            "debug": False,
            "task_execution_mode": "backend",
            "vercel_deployment_id": "dpl_backend_probe",
            "vercel_git_commit_sha": "b" * 40,
            "runtime_bundle_id": "rtb_" + ("a" * 64),
            "release_role": "COMMERCIAL_7A",
            "acceptance_identity_hmac_key": "h" * 32,
        }
        values.update(overrides)
        return patch.multiple(generation_module.settings, **values)

    @staticmethod
    def _session_maker(*, failure: Exception | None = None):
        db = AsyncMock()
        if failure is not None:
            db.execute.side_effect = failure

        @asynccontextmanager
        async def maker():
            yield db

        return maker, db

    async def test_probe_validates_runtime_entrypoints_and_generation_tables(self) -> None:
        maker, db = self._session_maker()

        with self._runtime_patch(), patch.object(
            generation_module,
            "async_session_maker",
            maker,
        ):
            ok, detail = await GenerationService().probe_backend_capability()

        self.assertTrue(ok)
        self.assertEqual(detail, "website_backend_generation_contract_v1")
        self.assertEqual(db.execute.await_count, 2)
        statements = [str(call.args[0]) for call in db.execute.await_args_list]
        self.assertIn("public.generation_jobs", statements[0])
        self.assertIn("public.generation_attempts", statements[1])

    async def test_probe_rejects_disabled_backend_mode(self) -> None:
        with self._runtime_patch(task_execution_mode="queue"):
            with self.assertRaisesRegex(
                RuntimeError,
                "generation_backend_execution_disabled",
            ):
                await GenerationService().probe_backend_capability()

    async def test_probe_rejects_invalid_runtime_coordinates_and_digest(self) -> None:
        with self._runtime_patch(vercel_deployment_id=""):
            with self.assertRaisesRegex(
                RuntimeError,
                "generation_backend_runtime_coordinates_invalid",
            ):
                await GenerationService().probe_backend_capability()
        with self._runtime_patch(vercel_git_commit_sha=""):
            with self.assertRaisesRegex(
                RuntimeError,
                "generation_backend_executor_digest_invalid",
            ):
                await GenerationService().probe_backend_capability()

    async def test_probe_rejects_missing_executor_entrypoint(self) -> None:
        with self._runtime_patch(), patch.object(
            executor_module,
            "execute_generation_job",
            None,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "generation_backend_entrypoint_unavailable",
            ):
                await GenerationService().probe_backend_capability()

    async def test_probe_fails_closed_when_generation_schema_is_not_queryable(self) -> None:
        maker, _db = self._session_maker(
            failure=RuntimeError("generation_table_permission_denied"),
        )

        with self._runtime_patch(), patch.object(
            generation_module,
            "async_session_maker",
            maker,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "generation_table_permission_denied",
            ):
                await GenerationService().probe_backend_capability()
