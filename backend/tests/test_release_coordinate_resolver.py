"""Fresh-job release-coordinate resolver trust boundary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import httpx


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "resolve_release_coordinates.py"
ARTIFACT_SCRIPT = ROOT / "scripts" / "release" / "github_artifact_evidence.py"


def _module():
    if not SCRIPT.exists():
        raise AssertionError("release-coordinate resolver is missing")
    spec = importlib.util.spec_from_file_location("resolve_release_coordinates", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact_module():
    if not ARTIFACT_SCRIPT.exists():
        raise AssertionError("GitHub artifact evidence helper is missing")
    spec = importlib.util.spec_from_file_location("github_artifact_evidence", ARTIFACT_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseCoordinateResolverTest(unittest.TestCase):
    def test_preview_identity_requires_one_fresh_matching_activation_and_report(self) -> None:
        module = _module()
        now = datetime.now(timezone.utc)
        activation = {
            "environment": "preview",
            "kind": "PREVIEW_IDENTITY",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "phase": "COMPLETED",
            "report_sha256": "c" * 64,
            "updated_at": now.isoformat(),
        }
        report = {
            "environment": "preview",
            "kind": "PREVIEW_IDENTITY",
            "source_sha": activation["source_sha"],
            "runtime_bundle_id": activation["runtime_bundle_id"],
            "api_deployment_id": activation["api_deployment_id"],
            "phase": "COMPLETED",
            "sha256": activation["report_sha256"],
            "created_at": now.isoformat(),
        }
        resolved = module.resolve_records(
            "preview-identity", [activation], report, now=now, maximum_age=timedelta(hours=2)
        )
        self.assertEqual(resolved["runtime_bundle_id"], activation["runtime_bundle_id"])
        self.assertNotIn("database_url", resolved)
        self.assertNotIn("token", resolved)

    def test_resolver_rejects_ambiguity_staleness_role_confusion_and_caller_pass(self) -> None:
        module = _module()
        now = datetime.now(timezone.utc)
        base = {
            "environment": "preview",
            "kind": "PREVIEW_IDENTITY",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "phase": "COMPLETED",
            "report_sha256": "c" * 64,
            "updated_at": now.isoformat(),
        }
        report = {
            "environment": "preview", "kind": "PREVIEW_IDENTITY",
            "source_sha": base["source_sha"], "runtime_bundle_id": base["runtime_bundle_id"],
            "api_deployment_id": base["api_deployment_id"], "phase": "COMPLETED",
            "sha256": base["report_sha256"], "created_at": now.isoformat(),
        }
        with self.assertRaises(ValueError):
            module.resolve_records("preview-identity", [base, dict(base)], report, now=now)
        with self.assertRaises(ValueError):
            module.resolve_records(
                "preview-identity",
                [{**base, "updated_at": (now - timedelta(days=1)).isoformat()}],
                report,
                now=now,
                maximum_age=timedelta(hours=2),
            )
        with self.assertRaises(ValueError):
            module.resolve_records(
                "safe-baseline", [{**base, "environment": "production"}], report, now=now
            )
        with self.assertRaises(ValueError):
            module.resolve_records(
                "preview-identity", [{**base, "caller_pass": True}], report, now=now
            )

    def test_only_initial_coordinate_kinds_are_allowlisted(self) -> None:
        module = _module()
        self.assertEqual(set(module.INITIAL_COORDINATE_KINDS), {"preview-identity", "safe-baseline"})
        with self.assertRaises(ValueError):
            module.resolve_records("production", [], {}, now=datetime.now(timezone.utc))

    def test_cli_report_reader_hashes_bytes_and_rejects_tampering(self) -> None:
        module = _module()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "preview" / "activation-report.json"
            report_path.parent.mkdir()
            raw = json.dumps({"phase": "COMPLETED"}, sort_keys=True).encode("utf-8")
            report_path.write_bytes(raw)
            activation = {
                "private_evidence_prefix": "preview",
                "report_sha256": hashlib.sha256(raw).hexdigest(),
            }
            loaded = module._read_report(activation, root)
            self.assertEqual(loaded["_content_sha256"], activation["report_sha256"])

            report_path.write_text('{"phase":"FAILED"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                module._read_report(activation, root)

    def test_safe_baseline_report_is_downloaded_from_its_exact_github_artifact_reference(self) -> None:
        artifact_module = _artifact_module()
        resolver = _module()
        report = {
            "environment": "production",
            "kind": "SAFE_BASELINE_INSTALL",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_safe",
            "phase": "COMPLETED",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        report_raw = json.dumps(report, sort_keys=True).encode("utf-8")
        archive_io = io.BytesIO()
        with zipfile.ZipFile(archive_io, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("safe-baseline-formal.json", report_raw)
        archive_raw = archive_io.getvalue()
        archive_digest = hashlib.sha256(archive_raw).hexdigest()
        reference = artifact_module.build_reference(
            repository="owner/repo",
            run_id="456",
            artifact_id="123",
            artifact_digest=f"sha256:{archive_digest}",
            report_name="safe-baseline-formal.json",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers.get("Authorization"), "Bearer github-token")
            if request.url.path == "/repos/owner/repo/actions/artifacts/123":
                return httpx.Response(
                    200,
                    json={
                        "id": 123,
                        "expired": False,
                        "digest": f"sha256:{archive_digest}",
                        "archive_download_url": "https://api.github.com/repos/owner/repo/actions/artifacts/123/zip",
                        "workflow_run": {"id": 456},
                    },
                )
            if request.url.path == "/repos/owner/repo/actions/artifacts/123/zip":
                return httpx.Response(200, content=archive_raw)
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            activation = {
                "private_evidence_prefix": reference,
                "report_sha256": hashlib.sha256(report_raw).hexdigest(),
            }
            loaded = resolver._read_report(
                activation,
                None,
                github_token="github-token",
                http_client=client,
            )
        finally:
            client.close()
        self.assertEqual(loaded["phase"], "COMPLETED")
        self.assertEqual(loaded["_content_sha256"], activation["report_sha256"])

    def test_artifact_lookup_distinguishes_confirmed_absence_from_api_failure(self) -> None:
        module = _artifact_module()

        def missing_handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params.get("name"), "reserved-build")
            return httpx.Response(200, json={"total_count": 0, "artifacts": []})

        with httpx.Client(transport=httpx.MockTransport(missing_handler)) as client:
            result = module.lookup_artifact(
                repository="owner/repo",
                run_id="456",
                name="reserved-build",
                token="github-token",
                client=client,
            )
        self.assertEqual(result["state"], "NOT_FOUND")

        with httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(503, text="unavailable"))
        ) as client:
            with self.assertRaises(module.GitHubArtifactError):
                module.lookup_artifact(
                    repository="owner/repo",
                    run_id="456",
                    name="reserved-build",
                    token="github-token",
                    client=client,
                )

    def test_stored_formal_reference_must_remain_downloadable_and_hash_bound(self) -> None:
        module = _artifact_module()
        report_raw = json.dumps({"passed": True}, sort_keys=True).encode("utf-8")
        archive_io = io.BytesIO()
        with zipfile.ZipFile(archive_io, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("safe-baseline-formal.json", report_raw)
        archive_raw = archive_io.getvalue()
        archive_digest = hashlib.sha256(archive_raw).hexdigest()
        reference = module.build_reference(
            repository="owner/repo",
            run_id="456",
            artifact_id="123",
            artifact_digest=f"sha256:{archive_digest}",
            report_name="safe-baseline-formal.json",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/actions/artifacts/123"):
                return httpx.Response(
                    200,
                    json={
                        "id": 123,
                        "expired": False,
                        "digest": f"sha256:{archive_digest}",
                        "workflow_run": {"id": 456},
                    },
                )
            if request.url.path.endswith("/actions/artifacts/123/zip"):
                return httpx.Response(200, content=archive_raw)
            return httpx.Response(404)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = module.verify_reference(
                reference,
                expected_report_sha256=hashlib.sha256(report_raw).hexdigest(),
                token="github-token",
                client=client,
            )
        self.assertTrue(result["passed"])

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(module.GitHubArtifactError):
                module.verify_reference(
                    reference,
                    expected_report_sha256="0" * 64,
                    token="github-token",
                    client=client,
                )


if __name__ == "__main__":
    unittest.main()
