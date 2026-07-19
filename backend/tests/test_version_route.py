"""Public runtime liveness and version contracts."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import httpx


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import main  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.services.runtime_bundle_service import public_runtime_bundle_json  # noqa: E402


class VersionRouteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._had_runtime_blocker = hasattr(main.app.state, "runtime_config_blocked")
        self._runtime_blocker = getattr(main.app.state, "runtime_config_blocked", False)
        main.app.state.runtime_config_blocked = False

    async def asyncTearDown(self) -> None:
        if self._had_runtime_blocker:
            main.app.state.runtime_config_blocked = self._runtime_blocker
        elif hasattr(main.app.state, "runtime_config_blocked"):
            delattr(main.app.state, "runtime_config_blocked")

    async def _get(self, path: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    async def test_liveness_exposes_process_state_only(self) -> None:
        response = await self._get("/health")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "status": "healthy",
                "kind": "liveness",
                "readiness": "/health/ready",
            },
        )

    async def test_version_is_registered_under_the_versioned_api(self) -> None:
        response = await self._get("/api/v1/version")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["schema"], "vowpic.runtime-bundle-report.v1")
        self.assertEqual(
            set(payload),
            {
                "schema",
                "source_sha",
                "runtime_bundle_id",
                "deployment_id",
                "release_role",
                "runtime_environment",
                "schema_revision",
                "api_compatibility_version",
                "worker_compatibility_version",
                "job_payload_min",
                "job_payload_max",
                "worker_image_digest",
                "provider_policy_hash",
                "flag_contract_hash",
            },
        )
        serialized = response.text.lower()
        for forbidden in (
            "worker_heartbeat",
            "token",
            "secret",
            "database_url",
            "current_feature_snapshot",
            "target_feature_snapshot",
        ):
            self.assertNotIn(forbidden, serialized)

    async def test_version_schema_matches_the_release_role_contract(self) -> None:
        common = {
            "_env_file": None,
            "runtime_environment": "production",
            "vercel_deployment_id": "dpl_exact",
            "vercel_git_commit_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "acceptance_identity_hmac_key": "k" * 32,
        }

        safe_baseline = public_runtime_bundle_json(
            Settings(**common, release_role="SAFE_BASELINE")
        )
        commercial = public_runtime_bundle_json(
            Settings(**common, release_role="COMMERCIAL_7A")
        )

        self.assertEqual(safe_baseline["schema_revision"], "20260712_0014")
        self.assertEqual(commercial["schema_revision"], "20260710_0020")


if __name__ == "__main__":
    unittest.main()
