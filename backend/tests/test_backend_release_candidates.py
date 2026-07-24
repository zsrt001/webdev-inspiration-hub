"""Promoted contracts for the website-backend-only release implementation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unittest
from uuid import uuid4

import httpx


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackendOnlyReleaseContractTest(unittest.TestCase):
    def test_retired_drain_and_duplicate_runtime_contract_are_absent(self) -> None:
        self.assertFalse((ROOT / "scripts/release/verify_runtime_drain.py").exists())
        self.assertFalse((ROOT / "release/runtime-contracts.json").exists())
        private_media = (
            ROOT / "scripts/release/verify_private_media.py"
        ).read_text(encoding="utf-8")
        for retired in (
            "runtime_drain_report",
            "--runtime-drain-report",
            "REDIS_URL",
            "arq:",
            "Worker heartbeat",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, private_media)

    def test_protected_workflows_have_no_external_generation_worker_dependency(self) -> None:
        forbidden = (
            "RAILWAY_",
            "setup-railway-cli",
            "run_approved_worker_host",
            "run_preview_worker",
            "PRODUCTION_REDIS_URL",
            "PREVIEW_REDIS_URL",
            "TASK_EXECUTION_MODE=arq",
            "Dockerfile.worker",
        )
        workflow_paths = {
            ".github/workflows/production-release.yml": (
                ROOT / ".github/workflows/production-release.yml"
            ),
            ".github/workflows/release-observation.yml": (
                ROOT / ".github/workflows/release-observation.yml"
            ),
            ".github/workflows/integration.yml": ROOT / ".github/workflows/integration.yml",
        }
        for relative, path in workflow_paths.items():
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=relative, token=token):
                    self.assertNotIn(token, text)
        production = workflow_paths[
            ".github/workflows/production-release.yml"
        ].read_text(encoding="utf-8")
        self.assertIn("TASK_EXECUTION_MODE=backend", production)
        self.assertIn("--backend-runtime-report", production)
        self.assertNotIn("WORKER_STAGED", production)
        self.assertNotIn("WORKER_RUNNING", production)

    def test_runtime_contract_hashes_the_current_backend_executor_sources(self) -> None:
        contract = json.loads(
            (ROOT / "backend/contracts/runtime-contracts.json").read_text(
                encoding="utf-8"
            )
        )
        sources = contract["source_sha256"]
        self.assertIn(
            "backend/app/services/generation_executor_service.py", sources
        )
        self.assertIn("backend/app/routers/ops.py", sources)
        self.assertIn("backend/app/routers/provider_callbacks.py", sources)
        self.assertIn("backend/app/services/evolink_callback_service.py", sources)
        self.assertIn("backend/app/services/runtime_bundle_service.py", sources)
        for relative, expected in sources.items():
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)

    def test_preview_registration_rejects_any_external_worker_coordinate(self) -> None:
        module = _load(
            "release_register_preview",
            "scripts/release/register_preview_activation.py",
        )
        coordinates = module.validate_coordinates(
            release_role="PREVIEW_COMMERCIAL",
            source_sha="1" * 40,
            runtime_bundle_id="rtb_" + "2" * 64,
            workflow_run_id="123",
            workflow_attempt=1,
        )
        self.assertEqual(coordinates["api_role"], "PREVIEW_COMMERCIAL_API")
        self.assertFalse(any(key.startswith("worker_") for key in coordinates))
        with self.assertRaisesRegex(ValueError, "website-backend-only"):
            module.validate_coordinates(
                release_role="PREVIEW_COMMERCIAL",
                source_sha="1" * 40,
                runtime_bundle_id="rtb_" + "2" * 64,
                worker_image_digest="sha256:" + "3" * 64,
                workflow_run_id="123",
                workflow_attempt=1,
            )

    def test_preview_backend_runtime_report_is_signed_and_bound(self) -> None:
        module = _load(
            "release_collect_runtime",
            "scripts/release/collect_runtime_report.py",
        )
        source = "1" * 40
        runtime = "rtb_" + "2" * 64
        deployment = "dpl_preview_backend_123"
        backend_digest = "sha256:" + "3" * 64
        responses = {
            "/health": {
                "status": "healthy",
                "kind": "liveness",
                "readiness": "/health/ready",
            },
            "/health/ready": {"ready": True},
            "/api/v1/version": {
                "schema": "vowpic.runtime-bundle-report.v1",
                "source_sha": source,
                "runtime_bundle_id": runtime,
                "deployment_id": deployment,
                "release_role": "PREVIEW_COMMERCIAL",
                "runtime_environment": "preview",
                "schema_revision": "20260710_0020",
                "backend_execution_version": "vowpic-backend-executor.v1",
                "backend_executor_digest": backend_digest,
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=responses[request.url.path])

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = module.collect_api_runtime_coordinate_report(
                client,
                base_url="https://preview.example.vercel.app",
                expected_deployment_id=deployment,
                expected_runtime_bundle_id=runtime,
                expected_source_sha=source,
                expected_schema="20260710_0020",
                expected_release_role="PREVIEW_COMMERCIAL",
                expected_runtime_environment="preview",
                bypass_secret="",
                signing_key=b"k" * 32,
                now=datetime(2026, 7, 23, tzinfo=timezone.utc),
            )
        self.assertTrue(report["passed"])
        self.assertEqual(report["backend_executor_digest"], backend_digest)
        self.assertRegex(report["signature"], r"^hmac-sha256:[0-9a-f]{64}$")

    def test_stage5_replaces_worker_heartbeat_with_backend_runtime_evidence(self) -> None:
        module = _load(
            "release_stage5",
            "scripts/release/materialize_stage5_evidence.py",
        )
        source = "1" * 40
        contract_hash = "2" * 64
        pr_runtime = "rtb_" + "3" * 64
        identity_runtime = "rtb_" + "4" * 64
        commercial_runtime = "rtb_" + "5" * 64
        api_deployment = "dpl_preview_backend_123"
        backend_digest = "sha256:" + "6" * 64
        activation_id = str(uuid4())

        def evidence(case_id: str, runtime: str) -> dict[str, object]:
            return {
                "schema": "vowpic.gate-evidence.v1",
                "case_id": case_id,
                "status": "PASS",
                "execution": "completed",
                "test_count": 1,
                "source_sha": source,
                "runtime_bundle_id": runtime,
                "gate_contract_sha256": contract_hash,
            }

        provider_capabilities = json.loads(
            (ROOT / "release/provider-capabilities.json").read_text(encoding="utf-8")
        )
        provider_hash = hashlib.sha256(
            json.dumps(
                provider_capabilities,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        result = module.build_stage5_materialization(
            source_sha=source,
            gate_contract_sha256=contract_hash,
            pr_evidence=[evidence("pr_case", pr_runtime)],
            expected_pr_case_ids={"pr_case"},
            identity_evidence=[
                evidence(case_id, identity_runtime)
                for case_id in module.IDENTITY_CASE_IDS
            ],
            identity_cleanup={
                "state": "CLEANED",
                "source_sha": source,
                "runtime_bundle_id": identity_runtime,
            },
            activation={
                "activation_id": activation_id,
                "source_sha": source,
                "runtime_bundle_id": commercial_runtime,
                "api_deployment_id": api_deployment,
            },
            provider_capabilities=provider_capabilities,
            backend_runtime={
                "schema": "vowpic.api-runtime-coordinate-report.v1",
                "passed": True,
                "source_sha": source,
                "runtime_bundle_id": commercial_runtime,
                "api_deployment_id": api_deployment,
                "release_role": "PREVIEW_COMMERCIAL",
                "runtime_environment": "preview",
                "backend_execution_version": "vowpic-backend-executor.v1",
                "backend_executor_digest": backend_digest,
            },
            provider_fetch={
                "passed": True,
                "activation_id": activation_id,
                "source_sha": source,
                "runtime_bundle_id": commercial_runtime,
                "api_deployment_id": api_deployment,
                "backend_executor_digest": backend_digest,
                "provider_capabilities_sha256": provider_hash,
                "network_submit_count": 1,
                "provider_fetch_count": 1,
                "provider_task_terminal_status": "completed",
                "callback_recovery": "BOUND_FROM_PROVIDER_CALLBACK",
                "submitter_provider_task_write_count": 0,
                "callback_attempt_status": "FINISHED",
                "callback_job_status": "FINISHED",
            },
            provider_case_cleanup={
                "state": "CLEANED",
                "activation_id": activation_id,
                "provider_task_bound": True,
                "terminal_generation_graph_preserved": True,
            },
            provider_origin_cleanup={"state": "REMOVED", "activation_id": activation_id},
            commercial_cleanup={
                "state": "CLEANED",
                "activation_id": activation_id,
                "source_sha": source,
                "runtime_bundle_id": commercial_runtime,
                "api_deployment_id": api_deployment,
                "worker_deployment_id": None,
            },
        )
        case_ids = {row["case_id"] for row in result["commercial_evidence"]}
        self.assertIn("preview_backend_runtime", case_ids)
        self.assertNotIn("preview_worker_heartbeat", case_ids)

    def test_release_contracts_name_only_the_backend_executor(self) -> None:
        gates = json.loads(
            (ROOT / "release/gates.json").read_text(encoding="utf-8")
        )
        serialized_gates = json.dumps(gates, sort_keys=True)
        self.assertIn("preview_backend_runtime", serialized_gates)
        self.assertNotIn("preview_worker_heartbeat", serialized_gates)
        preview = json.loads(
            (
                ROOT / "release/preview-runtime-contract.json"
            ).read_text(encoding="utf-8")
        )
        extension = preview["commercial_extension"]
        self.assertEqual(extension["backend_executor"]["host"], "vercel-api")
        self.assertNotIn("worker", extension)


if __name__ == "__main__":
    unittest.main()
