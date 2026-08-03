"""Version-specific EvoLink callback alias binding tests."""

from __future__ import annotations

from unittest.mock import patch
import unittest

import httpx

from backend.scripts.configure_evolink_callback_origin import (
    _verify_runtime,
    bind_alias,
    build_callback_host,
    build_state,
)


SOURCE_SHA = "a" * 40
RUNTIME_ID = "rtb_" + "b" * 64
DEPLOYMENT_ID = "dpl_preview_exact"
PROJECT_ID = "prj_vowpic"
TEAM_ID = "team_vowpic"


class EvolinkCallbackOriginTest(unittest.TestCase):
    def test_host_is_deterministic_for_one_workflow_attempt(self) -> None:
        self.assertEqual(
            build_callback_host(
                source_sha=SOURCE_SHA,
                workflow_run_id="123456789",
                workflow_attempt=2,
            ),
            "vowpic-evolink-aaaaaaaaaaaa-123456789-2.vercel.app",
        )
        with self.assertRaisesRegex(ValueError, "workflow"):
            build_callback_host(
                source_sha=SOURCE_SHA,
                workflow_run_id="123456789",
                workflow_attempt=0,
            )

    def test_alias_is_bound_to_the_exact_deployment_and_runtime(self) -> None:
        state = build_state(
            source_sha=SOURCE_SHA,
            workflow_run_id="123456789",
            workflow_attempt=2,
            deployment_url="https://preview-exact.vercel.app",
            deployment_id=DEPLOYMENT_ID,
            runtime_bundle_id=RUNTIME_ID,
        )
        alias_reads = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal alias_reads
            if request.url.path.startswith("/v4/aliases/"):
                alias_reads += 1
                if alias_reads == 1:
                    return httpx.Response(404)
                return httpx.Response(
                    200,
                    json={
                        "alias": state["callback_host"],
                        "projectId": PROJECT_ID,
                        "deploymentId": DEPLOYMENT_ID,
                    },
                )
            if request.url.path == "/api/v1/version":
                return httpx.Response(
                    200,
                    json={
                        "schema": "vowpic.runtime-bundle-report.v1",
                        "source_sha": SOURCE_SHA,
                        "runtime_bundle_id": RUNTIME_ID,
                        "deployment_id": DEPLOYMENT_ID,
                    },
                )
            return httpx.Response(500)

        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
            patch(
                "backend.scripts.configure_evolink_callback_origin.subprocess.run"
            ) as run,
        ):
            result = bind_alias(
                state,
                vercel_cli="/tmp/vercel",
                token="vercel-token",
                project_id=PROJECT_ID,
                team_id=TEAM_ID,
                bypass_secret="bypass-secret",
                probe_secret="provider-probe-secret-" + "x" * 32,
                client=client,
            )

        self.assertTrue(result["passed"])
        self.assertEqual(result["deployment_id"], DEPLOYMENT_ID)
        self.assertEqual(result["binding_action"], "BOUND_EXACT_ALIAS")
        run.assert_called_once()

    def test_existing_alias_for_another_deployment_fails_closed(self) -> None:
        state = build_state(
            source_sha=SOURCE_SHA,
            workflow_run_id="123456789",
            workflow_attempt=2,
            deployment_url="https://preview-exact.vercel.app",
            deployment_id=DEPLOYMENT_ID,
            runtime_bundle_id=RUNTIME_ID,
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "alias": state["callback_host"],
                    "projectId": PROJECT_ID,
                    "deploymentId": "dpl_other",
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(ValueError, "another deployment"):
                bind_alias(
                    state,
                    vercel_cli="/tmp/vercel",
                    token="vercel-token",
                    project_id=PROJECT_ID,
                    team_id=TEAM_ID,
                    bypass_secret="",
                    probe_secret="provider-probe-secret-" + "x" * 32,
                    client=client,
                )

    def test_runtime_failure_reports_only_safe_diagnostics(self) -> None:
        state = build_state(
            source_sha=SOURCE_SHA,
            workflow_run_id="123456789",
            workflow_attempt=2,
            deployment_url="https://preview-exact.vercel.app",
            deployment_id=DEPLOYMENT_ID,
            runtime_bundle_id=RUNTIME_ID,
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="sensitive runtime body")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(ValueError, '"status_code": 503') as raised:
                _verify_runtime(
                    state,
                    bypass_secret="bypass-secret",
                    probe_secret="provider-probe-secret-" + "x" * 32,
                    client=client,
                    attempts=1,
                    delay_seconds=0,
                )
        self.assertNotIn("sensitive runtime body", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
