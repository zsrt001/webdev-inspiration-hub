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
    def test_in_progress_commercial_7a_uses_hash_bound_sealed_coordinates(self) -> None:
        module = _module()
        now = datetime.now(timezone.utc)
        manifest_coordinates = {
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "manifest_sha256": "c" * 64,
            "private_compatible_baseline_deployment_id": "dpl_baseline",
            "private_compatible_baseline_deployment_url": "https://baseline.vercel.app",
            "staged_target_deployment_id": "dpl_target",
            "staged_target_deployment_url": "https://target.vercel.app",
            "worker_deployment_id": "worker-production-1",
            "worker_image_digest": "sha256:" + "d" * 64,
        }
        report = {
            "schema": "vowpic.release-phase-report.v1",
            "activation_id": "00000000-0000-0000-0000-000000000071",
            "environment": "production",
            "kind": "COMMERCIAL_7A",
            "source_sha": "a" * 40,
            "phase": "MANIFEST_SEALED",
            "phase_rank": 4,
            "previous_report_sha256": "e" * 64,
            "phase_evidence": {
                "phase": "MANIFEST_SEALED",
                "evidence": {"manifest": {"sha256": "c" * 64}},
                "coordinates": manifest_coordinates,
            },
            "evidence_chain": [
                {
                    "phase": "MANIFEST_SEALED",
                    "phase_rank": 4,
                    "phase_evidence_sha256": "f" * 64,
                    "coordinates": manifest_coordinates,
                }
            ],
        }
        raw = json.dumps(
            report, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        report_sha = hashlib.sha256(raw).hexdigest()
        activation = {
            "id": report["activation_id"],
            "environment": "production",
            "kind": "COMMERCIAL_7A",
            "source_sha": manifest_coordinates["source_sha"],
            "runtime_bundle_id": manifest_coordinates["runtime_bundle_id"],
            "manifest_sha256": manifest_coordinates["manifest_sha256"],
            "api_deployment_id": manifest_coordinates["staged_target_deployment_id"],
            "api_deployment_url": manifest_coordinates["staged_target_deployment_url"],
            "api_role": "COMMERCIAL_7A_API",
            "worker_deployment_id": manifest_coordinates["worker_deployment_id"],
            "worker_role": "COMMERCIAL_7A_WORKER",
            "worker_image_digest": manifest_coordinates["worker_image_digest"],
            "private_evidence_prefix": "artifacts/release/a/123-1",
            "workflow_run_id": "123",
            "workflow_attempt": 1,
            "phase": "MANIFEST_SEALED",
            "report_sha256": report_sha,
            "updated_at": now.isoformat(),
        }
        resolved = module.resolve_records(
            "commercial-7a",
            [activation],
            {**report, "_content_sha256": report_sha},
            now=now,
            expected_phase="MANIFEST_SEALED",
            manifest_phase_coordinates=manifest_coordinates,
        )
        self.assertEqual(
            resolved["private_compatible_baseline_deployment_id"], "dpl_baseline"
        )
        self.assertEqual(resolved["api_deployment_id"], "dpl_target")
        self.assertEqual(resolved["staged_target_deployment_url"], "https://target.vercel.app")
        with self.assertRaisesRegex(ValueError, "baseline and target"):
            module.resolve_records(
                "commercial-7a",
                [activation],
                {**report, "_content_sha256": report_sha},
                now=now,
                expected_phase="MANIFEST_SEALED",
                manifest_phase_coordinates={
                    **manifest_coordinates,
                    "private_compatible_baseline_deployment_id": "dpl_target",
                },
            )

    def test_commercial_resolver_reads_only_the_hash_bound_canonical_manifest(self) -> None:
        module = _module()
        manifest_module = importlib.util.spec_from_file_location(
            "build_manifest_for_resolver_test",
            ROOT / "scripts/release/build_manifest.py",
        )
        assert manifest_module and manifest_module.loader
        build_manifest = importlib.util.module_from_spec(manifest_module)
        manifest_module.loader.exec_module(build_manifest)
        payload = {
            "schema": "vowpic.bundle-manifest.v1",
            "release_role": "COMMERCIAL_7A",
            "repository": "owner/vowpic",
            "project_id": "prj_vowpic",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_build_sha256": "6" * 64,
            "schema_revision": "20260710_0020",
            "api_deployment_id": "dpl_target",
            "staged_target_deployment_id": "dpl_target",
            "private_compatible_baseline_deployment_id": "dpl_baseline",
            "worker_deployment_id": "worker-production-1",
            "worker_image_digest": "sha256:" + "c" * 64,
            "preview_id": None,
            "api_compatibility_version": "vowpic-api.v1",
            "worker_compatibility_version": "vowpic-worker.v1",
            "job_payload_min": "generation-job.v1",
            "job_payload_max": "generation-job.v1",
            "contract_hashes": {
                "provider": "1" * 64,
                "model": "2" * 64,
                "policy": "3" * 64,
                "catalog": "4" * 64,
                "flag": "5" * 64,
                "pre_activation_off_snapshot": "1" * 64,
                "target_snapshot": "2" * 64,
                "gate": "3" * 64,
                "runtime": "4" * 64,
                "database_roles": "5" * 64,
                "worker_host": "6" * 64,
            },
            "tool_versions": {
                "python": "3.11.15",
                "node": "24.17.0",
                "vercel": "56.2.0",
                "docker": "27.5.1",
            },
        }
        raw = build_manifest.canonical_manifest_bytes(
            build_manifest.validate_manifest(payload)
        )

        class Store:
            def read(self, key):
                self.key = key
                return raw

        store = Store()
        activation = {
            "private_evidence_prefix": "artifacts/release/a/123-1",
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        }
        self.assertEqual(module._read_sealed_manifest(activation, store), raw)
        self.assertEqual(store.key, "artifacts/release/a/123-1/00-bundle-manifest.json")

    def test_preview_identity_requires_one_fresh_matching_activation_and_report(self) -> None:
        module = _module()
        now = datetime.now(timezone.utc)
        activation = {
            "id": "00000000-0000-0000-0000-000000000007",
            "environment": "preview",
            "kind": "PREVIEW_IDENTITY",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "api_deployment_url": "https://preview.vercel.app",
            "phase": "COMPLETED",
            "report_sha256": "c" * 64,
            "updated_at": now.isoformat(),
        }
        report = {
            "activation_id": activation["id"],
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
            "id": "00000000-0000-0000-0000-000000000007",
            "environment": "preview",
            "kind": "PREVIEW_IDENTITY",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "api_deployment_url": "https://preview.vercel.app",
            "phase": "COMPLETED",
            "report_sha256": "c" * 64,
            "updated_at": now.isoformat(),
        }
        report = {
            "activation_id": base["id"],
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

    def test_only_release_role_coordinate_kinds_are_allowlisted(self) -> None:
        module = _module()
        self.assertTrue(hasattr(module, "COORDINATE_KINDS"), "role-bound resolver kinds are missing")
        self.assertEqual(
            set(module.COORDINATE_KINDS),
            {
                "preview-identity",
                "preview-commercial",
                "preview-commercial-cleaned",
                "safe-baseline",
                "commercial-7a",
                "commercial-7a-failure",
                "contract-7b",
            },
        )
        with self.assertRaises(ValueError):
            module.resolve_records("production", [], {}, now=datetime.now(timezone.utc))

    def test_preview_commercial_requires_manifest_worker_and_active_role_binding(self) -> None:
        module = _module()
        self.assertIn("preview-commercial", module.SPEC_BY_KIND)
        now = datetime.now(timezone.utc)
        activation = {
            "id": "00000000-0000-0000-0000-000000000008",
            "environment": "preview",
            "kind": "PREVIEW_COMMERCIAL",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "manifest_sha256": "c" * 64,
            "api_deployment_id": "dpl_preview_commercial",
            "api_deployment_url": "https://preview-commercial.vercel.app",
            "api_role": "PREVIEW_COMMERCIAL_API",
            "worker_deployment_id": "worker-preview-1",
            "worker_role": "PREVIEW_COMMERCIAL_WORKER",
            "worker_image_digest": "sha256:" + "d" * 64,
            "private_evidence_prefix": "artifacts/release/a/run/dpl/c/",
            "workflow_run_id": "12345",
            "workflow_attempt": 1,
            "phase": "COMPLETED",
            "report_sha256": "e" * 64,
            "updated_at": now.isoformat(),
        }
        report = {
            "activation_id": activation["id"],
            "environment": activation["environment"],
            "kind": activation["kind"],
            "source_sha": activation["source_sha"],
            "runtime_bundle_id": activation["runtime_bundle_id"],
            "manifest_sha256": activation["manifest_sha256"],
            "api_deployment_id": activation["api_deployment_id"],
            "api_role": activation["api_role"],
            "worker_deployment_id": activation["worker_deployment_id"],
            "worker_role": activation["worker_role"],
            "worker_image_digest": activation["worker_image_digest"],
            "phase": activation["phase"],
            "sha256": activation["report_sha256"],
            "created_at": now.isoformat(),
        }

        resolved = module.resolve_records("preview-commercial", [activation], report, now=now)
        self.assertEqual(resolved["manifest_sha256"], activation["manifest_sha256"])
        self.assertEqual(resolved["worker_image_digest"], activation["worker_image_digest"])
        self.assertEqual(resolved["private_evidence_prefix"], activation["private_evidence_prefix"])

        for changed in (
            {"phase": "CLEANED"},
            {"environment": "production"},
            {"kind": "PREVIEW_IDENTITY"},
            {"worker_image_digest": "sha256:" + "f" * 64},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                module.resolve_records(
                    "preview-commercial",
                    [{**activation, **changed}],
                    report,
                    now=now,
                )

    def test_cleaned_preview_commercial_uses_the_original_completed_report(self) -> None:
        module = _module()
        now = datetime.now(timezone.utc)
        activation = {
            "id": "00000000-0000-0000-0000-000000000018",
            "environment": "preview",
            "kind": "PREVIEW_COMMERCIAL",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "manifest_sha256": "c" * 64,
            "api_deployment_id": "dpl_preview_commercial",
            "api_deployment_url": "https://preview-commercial.vercel.app",
            "api_role": "PREVIEW_COMMERCIAL_API",
            "worker_deployment_id": "worker-preview-1",
            "worker_role": "PREVIEW_COMMERCIAL_WORKER",
            "worker_image_digest": "sha256:" + "d" * 64,
            "private_evidence_prefix": "artifacts/release/a/run/dpl/c/",
            "workflow_run_id": "12345",
            "workflow_attempt": 1,
            "phase": "CLEANED",
            "report_sha256": "e" * 64,
            "updated_at": now.isoformat(),
        }
        report = {
            "activation_id": activation["id"],
            "environment": activation["environment"],
            "kind": activation["kind"],
            "source_sha": activation["source_sha"],
            "runtime_bundle_id": activation["runtime_bundle_id"],
            "manifest_sha256": activation["manifest_sha256"],
            "api_deployment_id": activation["api_deployment_id"],
            "api_role": activation["api_role"],
            "worker_deployment_id": activation["worker_deployment_id"],
            "worker_role": activation["worker_role"],
            "worker_image_digest": activation["worker_image_digest"],
            "phase": "COMPLETED",
            "sha256": activation["report_sha256"],
            "created_at": now.isoformat(),
        }

        resolved = module.resolve_records(
            "preview-commercial-cleaned", [activation], report, now=now
        )
        self.assertEqual(resolved["phase"], "CLEANED")
        self.assertEqual(resolved["activation_id"], activation["id"])
        with self.assertRaisesRegex(ValueError, "activation phase"):
            module.resolve_records(
                "preview-commercial-cleaned",
                [{**activation, "phase": "COMPLETED"}],
                report,
                now=now,
            )

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

    def test_bound_build_lookup_recovers_the_original_owner_from_exact_id(self) -> None:
        module = _artifact_module()
        digest = "sha256:" + "a" * 64

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                request.url.path,
                "/repos/owner/repo/actions/artifacts/8428394104",
            )
            self.assertEqual(request.headers.get("Authorization"), "Bearer github-token")
            return httpx.Response(
                200,
                json={
                    "id": 8428394104,
                    "name": "vowpic-safe-baseline-29640135684-2-build",
                    "expired": False,
                    "digest": digest,
                    "workflow_run": {"id": 29640135684},
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = module.lookup_bound_build_artifact(
                repository="owner/repo",
                artifact_id="8428394104",
                artifact_digest=digest,
                token="github-token",
                client=client,
            )

        self.assertEqual(
            result,
            {
                "state": "FOUND",
                "artifact_id": "8428394104",
                "artifact_digest": digest,
                "artifact_name": "vowpic-safe-baseline-29640135684-2-build",
                "workflow_run_id": "29640135684",
                "workflow_attempt": "2",
            },
        )

    def test_bound_build_lookup_fails_closed_on_coordinate_conflicts(self) -> None:
        module = _artifact_module()
        digest = "sha256:" + "a" * 64

        def response(payload: dict[str, object]) -> httpx.Client:
            return httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, json=payload)
                )
            )

        conflicting = (
            {
                "id": 999,
                "name": "vowpic-safe-baseline-456-2-build",
                "expired": False,
                "digest": digest,
                "workflow_run": {"id": 456},
            },
            {
                "id": 123,
                "name": "vowpic-safe-baseline-456-2-build",
                "expired": False,
                "digest": "sha256:" + "b" * 64,
                "workflow_run": {"id": 456},
            },
            {
                "id": 123,
                "name": "untrusted-build",
                "expired": False,
                "digest": digest,
                "workflow_run": {"id": 456},
            },
            {
                "id": 123,
                "name": "vowpic-safe-baseline-456-2-build",
                "expired": False,
                "digest": digest,
                "workflow_run": {"id": 789},
            },
        )
        for payload in conflicting:
            with self.subTest(payload=payload), response(payload) as client:
                with self.assertRaises(module.GitHubArtifactError):
                    module.lookup_bound_build_artifact(
                        repository="owner/repo",
                        artifact_id="123",
                        artifact_digest=digest,
                        token="github-token",
                        client=client,
                    )

        for status, payload in (
            (404, {}),
            (
                200,
                {
                    "id": 123,
                    "name": "vowpic-safe-baseline-456-2-build",
                    "expired": True,
                    "digest": digest,
                    "workflow_run": {"id": 456},
                },
            ),
        ):
            with self.subTest(status=status), httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request, status=status, payload=payload: httpx.Response(
                        status,
                        json=payload,
                    )
                )
            ) as client:
                result = module.lookup_bound_build_artifact(
                    repository="owner/repo",
                    artifact_id="123",
                    artifact_digest=digest,
                    token="github-token",
                    client=client,
                )
                self.assertEqual(result, {"state": "NOT_FOUND"})

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
