"""Immutable bundle-manifest and append-only evidence contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import httpx


ROOT = Path(__file__).resolve().parents[2]
RELEASE_SCRIPTS = ROOT / "scripts" / "release"
MANIFEST_SCRIPT = RELEASE_SCRIPTS / "build_manifest.py"
EVIDENCE_SCRIPT = ROOT / "scripts" / "release" / "append_evidence_index.py"
COLLECT_SCRIPT = RELEASE_SCRIPTS / "collect_runtime_report.py"
VERIFY_SCRIPT = RELEASE_SCRIPTS / "verify_bundle.py"
REGISTER_SCRIPT = RELEASE_SCRIPTS / "register_bundle.py"


def _load(path: Path, name: str):
    if not path.exists():
        raise AssertionError(f"release contract script is missing: {path.name}")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_payload(role: str = "PREVIEW_COMMERCIAL") -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "vowpic.bundle-manifest.v1",
        "release_role": role,
        "repository": "owner/vowpic",
        "project_id": "prj_vowpic",
        "runtime_bundle_id": "rtb_" + "a" * 64,
        "source_sha": "b" * 40,
        "api_build_sha256": "c" * 64,
        "api_deployment_id": "dpl_preview_api",
        "preview_id": "preview-123",
        "private_compatible_baseline_deployment_id": None,
        "staged_target_deployment_id": None,
        "worker_image_digest": None,
        "worker_deployment_id": None,
        "schema_revision": "20260710_0020",
        "api_compatibility_version": "vowpic-api.v1",
        "backend_execution_version": "vowpic-backend-executor.v1",
        "backend_executor_digest": "sha256:" + "d" * 64,
        "worker_compatibility_version": None,
        "job_payload_min": "generation-job.v1",
        "job_payload_max": "generation-job.v1",
        "contract_hashes": {
            "provider": "1" * 64,
            "model": "2" * 64,
            "policy": "3" * 64,
            "catalog": "4" * 64,
            "flag": "5" * 64,
            "pre_activation_off_snapshot": "6" * 64,
            "target_snapshot": "7" * 64,
            "gate": "8" * 64,
            "runtime": "9" * 64,
            "database_roles": "a" * 64,
        },
        "tool_versions": {
            "python": "3.11.9",
            "node": "22.17.0",
            "vercel": "56.2.0",
        },
    }
    if role == "COMMERCIAL_7A":
        payload.update(
            {
                "api_deployment_id": "dpl_target",
                "preview_id": None,
                "private_compatible_baseline_deployment_id": "dpl_baseline",
                "staged_target_deployment_id": "dpl_target",
            }
        )
    return payload


class ReleaseBundleTest(unittest.TestCase):
    def test_manifest_is_canonical_role_bound_and_create_once(self) -> None:
        module = _load(MANIFEST_SCRIPT, "build_manifest")
        payload = _manifest_payload()

        canonical = module.canonical_manifest_bytes(payload)
        self.assertEqual(canonical, module.canonical_manifest_bytes(dict(reversed(list(payload.items())))))
        self.assertTrue(canonical.endswith(b"\n"))
        self.assertEqual(json.loads(canonical), payload)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "00-bundle-manifest.json"
            digest = module.write_manifest_create_once(output, payload)
            self.assertEqual(digest, hashlib.sha256(canonical).hexdigest())
            self.assertEqual(output.read_bytes(), canonical)
            with self.assertRaises(FileExistsError):
                module.write_manifest_create_once(output, payload)
            with self.assertRaises(FileExistsError):
                module.write_manifest_create_once(output, {**payload, "source_sha": "f" * 40})

    def test_manifest_rejects_mutable_future_and_role_substitution_fields(self) -> None:
        module = _load(MANIFEST_SCRIPT, "build_manifest")
        base = _manifest_payload()
        for field in (
            "observed_current_flag_snapshot_hash",
            "current_snapshot_hash",
            "report_sha256",
            "observation_result",
            "activation_result",
            "decision",
            "manifest_sha256",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                module.canonical_manifest_bytes({**base, field: "f" * 64})

        with self.assertRaises(ValueError):
            module.canonical_manifest_bytes(
                {**base, "worker_image_digest": "vowpic-worker:latest"}
            )
        with self.assertRaises(ValueError):
            module.canonical_manifest_bytes({**base, "tool_versions": {}})
        with self.assertRaises(ValueError):
            module.canonical_manifest_bytes({**base, "schema_revision": "future"})
        with self.assertRaises(ValueError):
            module.canonical_manifest_bytes(
                {**base, "release_role": "COMMERCIAL_7A"}
            )
        with self.assertRaises(ValueError):
            module.canonical_manifest_bytes(
                {**_manifest_payload("COMMERCIAL_7A"), "preview_id": "preview-123"}
            )

    def test_backend_manifest_rejects_worker_coordinates(self) -> None:
        module = _load(MANIFEST_SCRIPT, "build_manifest")
        manifest = _manifest_payload()
        for field, value in (
            ("worker_image_digest", "sha256:" + "e" * 64),
            ("worker_deployment_id", "worker-preview-1"),
            ("worker_compatibility_version", "vowpic-worker.v1"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Worker"):
                module.validate_manifest({**manifest, field: value})

    def test_evidence_index_is_manifest_bound_and_rejects_duplicate_case_ids(self) -> None:
        manifest_module = _load(MANIFEST_SCRIPT, "build_manifest_for_evidence")
        evidence_module = _load(EVIDENCE_SCRIPT, "append_evidence_index")
        now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "00-bundle-manifest.json"
            manifest_sha = manifest_module.write_manifest_create_once(
                manifest_path, _manifest_payload()
            )
            report_path = root / "case-report.json"
            report_path.write_text('{"passed":true}\n', encoding="utf-8")
            entry = {
                "schema": "vowpic.evidence-entry.v1",
                "manifest_sha256": manifest_sha,
                "evidence_type": "integration-case",
                "case_id": "provider-fetch",
                "run_id": "123456",
                "attempt": 1,
                "deployment_id": "dpl_preview_api",
                "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                "produced_at": now.isoformat(),
                "observed_at": now.isoformat(),
                "freshness_result": "PASS",
                "reviewer_approval_ref": "approval-123",
                "decision": "PASS",
            }
            index = root / "evidence-index.ndjson"
            evidence_module.append_evidence(
                index,
                manifest_path=manifest_path,
                report_path=report_path,
                entry=entry,
            )
            self.assertEqual(json.loads(index.read_text(encoding="utf-8")), entry)
            with self.assertRaises(ValueError):
                evidence_module.append_evidence(
                    index,
                    manifest_path=manifest_path,
                    report_path=report_path,
                    entry=entry,
                )
            with self.assertRaises(ValueError):
                evidence_module.append_evidence(
                    index,
                    manifest_path=manifest_path,
                    report_path=report_path,
                    entry={**entry, "manifest_sha256": "0" * 64, "case_id": "other-case"},
                )
            self.assertEqual(manifest_path.read_bytes(), manifest_module.canonical_manifest_bytes(_manifest_payload()))

    def test_release_json_schemas_are_strict_and_versioned(self) -> None:
        for name, schema_id in (
            ("bundle-manifest.schema.json", "https://vowpic.com/schemas/bundle-manifest.v1.json"),
            ("evidence-index.schema.json", "https://vowpic.com/schemas/evidence-index.v1.json"),
        ):
            path = ROOT / "release" / name
            self.assertTrue(path.exists(), f"release schema is missing: {name}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["$id"], schema_id)
            self.assertFalse(payload["additionalProperties"])

    def test_bundle_manifest_schema_matches_the_canonical_backend_manifest(self) -> None:
        manifest_module = _load(MANIFEST_SCRIPT, "build_manifest_for_schema")
        schema = json.loads(
            (ROOT / "release/bundle-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        canonical = json.loads(
            manifest_module.canonical_manifest_bytes(_manifest_payload())
        )

        self.assertEqual(
            set(schema["required"]),
            manifest_module.MANIFEST_FIELDS,
        )
        self.assertEqual(
            set(schema["properties"]),
            manifest_module.MANIFEST_FIELDS,
        )
        self.assertEqual(set(canonical), set(schema["required"]))
        self.assertEqual(
            schema["properties"]["backend_execution_version"]["type"],
            "string",
        )
        self.assertEqual(
            schema["properties"]["backend_executor_digest"]["pattern"],
            "^sha256:[0-9a-f]{64}$",
        )
        for retired in (
            "worker_image_digest",
            "worker_deployment_id",
            "worker_compatibility_version",
        ):
            self.assertEqual(schema["properties"][retired]["type"], "null")
            self.assertIsNone(canonical[retired])
        contract_properties = set(
            schema["properties"]["contract_hashes"]["properties"]
        )
        expected_contract_properties = set(
            manifest_module.BASE_CONTRACT_HASH_FIELDS
        ) | {"database_roles"}
        self.assertEqual(contract_properties, expected_contract_properties)
        self.assertNotIn("worker_host", contract_properties)

        with self.assertRaises(ValueError):
            manifest_module.canonical_manifest_bytes(
                {**canonical, "worker_deployment_id": "worker-retired"}
            )
        with self.assertRaises(ValueError):
            manifest_module.canonical_manifest_bytes(
                {**canonical, "unexpected": "forbidden"}
            )

    def test_api_runtime_report_uses_exact_endpoints_and_rejects_redirects_or_mismatch(self) -> None:
        collect = _load(COLLECT_SCRIPT, "collect_runtime_report")
        verify = _load(VERIFY_SCRIPT, "verify_bundle")
        manifest_module = _load(MANIFEST_SCRIPT, "build_manifest_for_runtime_report")
        manifest = _manifest_payload()
        manifest_sha = hashlib.sha256(manifest_module.canonical_manifest_bytes(manifest)).hexdigest()
        signing_key = b"runtime-report-signing-key-32bytes"
        now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        version = {
            "schema": "vowpic.runtime-bundle-report.v1",
            "source_sha": manifest["source_sha"],
            "runtime_bundle_id": manifest["runtime_bundle_id"],
            "deployment_id": manifest["api_deployment_id"],
            "release_role": manifest["release_role"],
            "runtime_environment": "preview",
            "schema_revision": manifest["schema_revision"],
            "api_compatibility_version": manifest["api_compatibility_version"],
            "backend_execution_version": manifest["backend_execution_version"],
            "backend_executor_digest": manifest["backend_executor_digest"],
            "job_payload_min": manifest["job_payload_min"],
            "job_payload_max": manifest["job_payload_max"],
            "provider_policy_hash": manifest["contract_hashes"]["provider"],
            "flag_contract_hash": manifest["contract_hashes"]["flag"],
        }
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.headers.get("x-vercel-protection-bypass"), "bypass-value")
            if request.url.path == "/health":
                return httpx.Response(
                    200,
                    json={"status": "healthy", "kind": "liveness", "readiness": "/health/ready"},
                )
            if request.url.path == "/health/ready":
                return httpx.Response(200, json={"ready": True, "checks": {"database": {"ok": True}}})
            if request.url.path == "/api/v1/version":
                return httpx.Response(200, json=version)
            return httpx.Response(404)

        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            report = collect.collect_api_runtime_report(
                client,
                base_url="https://preview.example.test",
                manifest=manifest,
                manifest_sha256=manifest_sha,
                bypass_secret="bypass-value",
                signing_key=signing_key,
                now=now,
            )
        self.assertEqual(
            [request.url.path for request in requests],
            ["/health", "/health/ready", "/api/v1/version"],
        )
        self.assertNotIn("bypass-value", json.dumps(report, sort_keys=True))
        verify.verify_api_report(
            manifest,
            manifest_sha256=manifest_sha,
            report=report,
            signing_key=signing_key,
            now=now,
        )

        with httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(307, headers={"location": "https://other.test"})),
            follow_redirects=False,
        ) as client, self.assertRaises(ValueError):
            collect.collect_api_runtime_report(
                client,
                base_url="https://preview.example.test",
                manifest=manifest,
                manifest_sha256=manifest_sha,
                bypass_secret="bypass-value",
                signing_key=signing_key,
                now=now,
            )

    def test_production_runtime_coordinate_report_binds_rollback_deployment(self) -> None:
        collect = _load(COLLECT_SCRIPT, "collect_runtime_coordinate_report")
        source_sha = "a" * 40
        runtime_bundle_id = "rtb_" + "b" * 64
        deployment_id = "dpl_private_baseline"
        signing_key = b"runtime-report-signing-key-32bytes"
        now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
        version = {
            "schema": "vowpic.runtime-bundle-report.v1",
            "source_sha": source_sha,
            "runtime_bundle_id": runtime_bundle_id,
            "deployment_id": deployment_id,
            "release_role": "COMMERCIAL_7A",
            "runtime_environment": "production",
            "schema_revision": "20260710_0020",
            "api_compatibility_version": "api-v1",
            "backend_execution_version": "vowpic-backend-executor.v1",
            "backend_executor_digest": "sha256:" + "c" * 64,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(
                    200,
                    json={
                        "status": "healthy",
                        "kind": "liveness",
                        "readiness": "/health/ready",
                    },
                )
            if request.url.path == "/health/ready":
                return httpx.Response(200, json={"ready": True})
            if request.url.path == "/api/v1/version":
                return httpx.Response(200, json=version)
            return httpx.Response(404)

        with httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            report = collect.collect_api_runtime_coordinate_report(
                client,
                base_url="https://www.vowpic.test",
                expected_deployment_id=deployment_id,
                expected_runtime_bundle_id=runtime_bundle_id,
                expected_source_sha=source_sha,
                expected_schema="20260710_0020",
                expected_release_role="COMMERCIAL_7A",
                expected_runtime_environment="production",
                bypass_secret="",
                signing_key=signing_key,
                now=now,
            )
        self.assertTrue(report["passed"])
        self.assertEqual(report["api_deployment_id"], deployment_id)
        version["deployment_id"] = "dpl_wrong"
        with httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client, self.assertRaisesRegex(ValueError, "coordinates"):
            collect.collect_api_runtime_coordinate_report(
                client,
                base_url="https://www.vowpic.test",
                expected_deployment_id=deployment_id,
                expected_runtime_bundle_id=runtime_bundle_id,
                expected_source_sha=source_sha,
                expected_schema="20260710_0020",
                expected_release_role="COMMERCIAL_7A",
                expected_runtime_environment="production",
                bypass_secret="",
                signing_key=signing_key,
                now=now,
            )

    def test_private_manifest_registration_is_content_addressed_and_read_back(self) -> None:
        register = _load(REGISTER_SCRIPT, "register_bundle")
        manifest_module = _load(MANIFEST_SCRIPT, "build_manifest_for_registration")
        manifest = _manifest_payload()

        class Store:
            def __init__(self):
                self.values: dict[str, bytes] = {}

            def put_private(self, key, data, _content_type):
                if key in self.values:
                    raise FileExistsError(key)
                self.values[key] = bytes(data)

            def read_private(self, key):
                if key not in self.values:
                    raise FileNotFoundError(key)
                return self.values[key]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "00-bundle-manifest.json"
            manifest_module.write_manifest_create_once(path, manifest)
            store = Store()
            first = register.store_manifest_create_once(
                store,
                manifest_path=path,
                run_id="12345",
                attempt=2,
            )
            self.assertEqual(first["state"], "STORED")
            self.assertIn(first["manifest_sha256"], first["object_key"])
            self.assertNotIn("latest", first["object_key"].lower())
            second = register.store_manifest_create_once(
                store,
                manifest_path=path,
                run_id="12345",
                attempt=2,
            )
            self.assertEqual(second["state"], "ALREADY_STORED")
            store.values[first["object_key"]] = b"tampered"
            with self.assertRaises(ValueError):
                register.store_manifest_create_once(
                    store,
                    manifest_path=path,
                    run_id="12345",
                    attempt=2,
                )

    def test_registration_record_is_role_bound_and_contains_no_environment_mutation(self) -> None:
        register = _load(REGISTER_SCRIPT, "register_bundle_record")
        self.assertTrue(hasattr(register, "build_registration_record"), "registration CAS record builder is missing")
        manifest = _manifest_payload()
        stored = {
            "manifest_sha256": "f" * 64,
            "evidence_prefix": "artifacts/release/a/run/dpl/f",
        }
        record = register.build_registration_record(
            manifest,
            stored=stored,
            api_deployment_url="https://preview.example.test",
            workflow_run_id="12345",
            workflow_attempt=2,
            approval="preview-approval-123",
            report_sha256="e" * 64,
        )
        self.assertEqual(record["environment"], "preview")
        self.assertEqual(record["kind"], "PREVIEW_COMMERCIAL")
        self.assertEqual(record["api_role"], "PREVIEW_COMMERCIAL_API")
        self.assertIsNone(record["worker_role"])
        self.assertEqual(record["phase"], "COMPLETED")
        for forbidden in ("vercel_token", "environment_variables", "deployment_env", "secret"):
            self.assertNotIn(forbidden, record)

    def test_production_reservation_requires_exact_cleaned_preview_commercial(self) -> None:
        register = _load(REGISTER_SCRIPT, "register_bundle_reservation")
        now = datetime(2026, 7, 14, tzinfo=timezone.utc)
        preview = {
            "activation_id": "00000000-0000-0000-0000-000000000018",
            "environment": "preview",
            "kind": "PREVIEW_COMMERCIAL",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "manifest_sha256": "c" * 64,
            "report_sha256": "d" * 64,
            "api_deployment_id": "dpl_preview_commercial",
            "api_deployment_url": "https://preview-commercial.vercel.app",
            "api_role": "PREVIEW_COMMERCIAL_API",
            "worker_deployment_id": None,
            "worker_role": None,
            "worker_image_digest": None,
            "private_evidence_prefix": "artifacts/release/a/run/dpl/c",
            "workflow_run_id": "12345",
            "workflow_attempt": 1,
            "phase": "CLEANED",
        }
        record, preview_hash = register.build_production_reservation(
            kind="COMMERCIAL_7A",
            environment="production",
            source_sha="a" * 40,
            preview_resolution=preview,
            workflow_run_id="98765",
            workflow_attempt=2,
            approval="prod-approval-1",
            now=now,
        )
        self.assertEqual(record["phase"], "RESERVED")
        self.assertEqual(record["reservation_expires_at"], now + timedelta(hours=2))
        self.assertEqual(record["runtime_bundle_id"], None)
        self.assertEqual(record["api_role"], "COMMERCIAL_7A_API")
        self.assertIsNone(record["worker_role"])
        self.assertRegex(preview_hash, r"^[0-9a-f]{64}$")
        for changed in (
            {"phase": "COMPLETED"},
            {"kind": "PREVIEW_IDENTITY"},
            {"source_sha": "f" * 40},
            {"worker_role": "PREVIEW_IDENTITY_WORKER"},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                register.build_production_reservation(
                    kind="COMMERCIAL_7A",
                    environment="production",
                    source_sha="a" * 40,
                    preview_resolution={**preview, **changed},
                    workflow_run_id="98765",
                    workflow_attempt=2,
                    approval="prod-approval-1",
                    now=now,
                )

    def test_production_reservation_reuses_only_the_same_live_attempt(self) -> None:
        register = _load(REGISTER_SCRIPT, "register_bundle_reservation_decision")
        now = datetime(2026, 7, 14, tzinfo=timezone.utc)
        requested = {
            "id": "00000000-0000-0000-0000-000000000020",
            "environment": "production",
            "kind": "COMMERCIAL_7A",
            "source_sha": "a" * 40,
            "runtime_bundle_id": None,
            "workflow_run_id": "98765",
            "workflow_attempt": 2,
            "phase": "RESERVED",
            "phase_rank": 0,
            "version": 1,
            "approval": "prod-approval-1",
            "reservation_expires_at": now + timedelta(hours=2),
        }
        existing = {**requested, "id": "00000000-0000-0000-0000-000000000021"}
        reused = register.decide_production_reservation(
            active_rows=[existing], same_attempt_rows=[existing], requested=requested, now=now
        )
        self.assertEqual(reused["id"], existing["id"])
        with self.assertRaisesRegex(ValueError, "active Production release"):
            register.decide_production_reservation(
                active_rows=[{**existing, "workflow_run_id": "111"}],
                same_attempt_rows=[],
                requested=requested,
                now=now,
            )
        with self.assertRaisesRegex(ValueError, "expired"):
            register.decide_production_reservation(
                active_rows=[{**existing, "reservation_expires_at": now}],
                same_attempt_rows=[{**existing, "reservation_expires_at": now}],
                requested=requested,
                now=now,
            )


if __name__ == "__main__":
    unittest.main()
