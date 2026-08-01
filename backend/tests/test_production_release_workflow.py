"""Production release state-machine and approved Worker-host truth contracts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
import yaml
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "scripts" / "release" / "register_bundle.py"
PRIVATE_STORE = ROOT / "scripts" / "release" / "private_evidence_store.py"
AUTH_ORIGIN = ROOT / "scripts" / "release" / "configure_staged_auth_origin.py"
BINDING_CLEANUP = ROOT / "scripts" / "release" / "cleanup_acceptance_bindings.py"
ACTIVATION_PLAN = ROOT / "scripts" / "release" / "apply_activation_plan.py"
ACTIVATE_CANARIES = ROOT / "scripts" / "release" / "activate_with_canaries.py"
PRODUCTION_RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "production-release.yml"
OBSERVATION_LOGIN_WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "production-observation-database-logins.yml"
)
PYTHON = shutil.which("python") or "python"


def _register_module():
    spec = importlib.util.spec_from_file_location("register_bundle", REGISTER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _private_store_module():
    return importlib.import_module("scripts.release.private_evidence_store")


def _path_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductionReleaseCredentialContractTest(unittest.TestCase):
    def test_uses_the_proven_inventory_login_for_all_control_reads(self) -> None:
        workflow = PRODUCTION_RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("PRODUCTION_CONTROL_READ_DATABASE_URL", workflow)
        self.assertEqual(
            workflow.count("--database-url-env PRODUCTION_READ_ONLY_DATABASE_URL"),
            5,
        )

    def test_legacy_outbox_retirement_reports_are_persisted_fail_closed(self) -> None:
        payload = yaml.load(
            PRODUCTION_RELEASE_WORKFLOW.read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        steps = payload["jobs"]["invalidate-activate-observe"]["steps"]
        names = [step.get("name", "") for step in steps]
        retire_index = names.index("Inventory and retire reconciled legacy outbox")
        upload_index = names.index("Persist sanitized legacy outbox retirement evidence")
        observe_index = names.index("Start one durable observation row and exit")
        self.assertLess(retire_index, upload_index)
        self.assertLess(upload_index, observe_index)
        upload = steps[upload_index]
        self.assertEqual(upload["if"], "${{ always() }}")
        self.assertEqual(
            upload["uses"],
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        )
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertIn(
            "artifacts/production-release/legacy-outbox-inventory.json",
            upload["with"]["path"],
        )
        self.assertIn(
            "artifacts/production-release/legacy-outbox-retirement.json",
            upload["with"]["path"],
        )

    def test_observation_login_rotation_is_manual_exact_main_and_self_cleaning(self) -> None:
        workflow = OBSERVATION_LOGIN_WORKFLOW.read_text(encoding="utf-8")
        payload = yaml.load(workflow, Loader=yaml.BaseLoader)
        self.assertEqual(set(payload["on"]), {"workflow_dispatch"})
        job = payload["jobs"]["provision"]
        self.assertEqual(job["environment"], "production")
        self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertIn("github.sha == inputs.required_main_sha", job["if"])
        self.assertIn(
            "secrets.PRODUCTION_MIGRATION_DATABASE_URL",
            workflow,
        )
        self.assertIn(
            "secrets.ONE_TIME_OBSERVATION_SECRET_PUBLISH_TOKEN",
            workflow,
        )
        self.assertIn("provision_observation_database_logins.py", workflow)
        self.assertIn("--github-environment production-observation", workflow)
        self.assertIn(
            "gh secret delete ONE_TIME_OBSERVATION_SECRET_PUBLISH_TOKEN",
            workflow,
        )
        self.assertNotIn("PRODUCTION_CONTROL_PLANE_DATABASE_URL", workflow)


class ProductionCanaryCleanupTest(unittest.TestCase):
    @staticmethod
    def _activation() -> dict:
        return {
            "source_sha": "a" * 40,
            "runtime_bundle_id": f"rtb_{'b' * 64}",
            "api_deployment_id": "dpl_exactCanary",
            "manifest_sha256": "c" * 64,
        }

    def test_cleanup_report_is_signed_and_exactly_release_bound(self) -> None:
        module = _path_module("activate_with_canaries", ACTIVATE_CANARIES)
        activation = self._activation()
        key = b"k" * 32
        unsigned = {
            "schema": "vowpic.production-canary-cleanup.v1",
            "passed": True,
            "source_sha": activation["source_sha"],
            "runtime_bundle_id": activation["runtime_bundle_id"],
            "deployment_id": activation["api_deployment_id"],
            "manifest_sha256": activation["manifest_sha256"],
            "user_id": str(uuid.uuid4()),
            "user_subject_hmac_sha256": "d" * 64,
            "observations": {
                "account_closed": True,
                "media_cleanup_requested": True,
                "post_close_session_denied": True,
            },
            "produced_at": datetime.now(timezone.utc).isoformat(),
        }
        signature = hmac.new(
            key,
            module._canonical(unsigned),
            hashlib.sha256,
        ).hexdigest()
        validated = module._validate_cleanup_report(
            {**unsigned, "signature": f"hmac-sha256:{signature}"},
            activation=activation,
            signing_key=key,
        )
        self.assertEqual(validated["user_id"], unsigned["user_id"])
        wrong_binding = {**unsigned, "source_sha": "e" * 40}
        wrong_signature = hmac.new(
            key,
            module._canonical(wrong_binding),
            hashlib.sha256,
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "release binding mismatch"):
            module._validate_cleanup_report(
                {
                    **wrong_binding,
                    "signature": f"hmac-sha256:{wrong_signature}",
                },
                activation=activation,
                signing_key=key,
            )

    def test_cleanup_absence_requires_physical_not_found_proof(self) -> None:
        module = _path_module("activate_with_canaries_absence", ACTIVATE_CANARIES)
        activation = self._activation()
        cleanup_report = {
            "user_id": str(uuid.uuid4()),
            "user_subject_hmac_sha256": "d" * 64,
            "source_sha": activation["source_sha"],
            "runtime_bundle_id": activation["runtime_bundle_id"],
            "deployment_id": activation["api_deployment_id"],
            "manifest_sha256": activation["manifest_sha256"],
        }
        absence = {
            "schema": "vowpic.acceptance-media-absence.v1",
            "passed": True,
            "source_sha": activation["source_sha"],
            "runtime_bundle_id": activation["runtime_bundle_id"],
            "deployment_id": activation["api_deployment_id"],
            "manifest_sha256": activation["manifest_sha256"],
            "user_subject_hmac_sha256": "d" * 64,
            "verified_asset_count": 2,
            "storage_read_outcome": "NOT_FOUND",
            "facts_sha256": "f" * 64,
        }
        with patch.object(
            module,
            "_post_json",
            side_effect=[(200, {"success": True}), (200, absence)],
        ):
            result = module._verify_cleanup_absence(
                base_url="https://www.vowpic.com",
                cleanup_report=cleanup_report,
                cron_token="t" * 24,
                attempts=1,
            )
        self.assertTrue(result["passed"])
        self.assertEqual(result["storage_read_outcome"], "NOT_FOUND")
        self.assertEqual(result["verified_asset_count"], 2)


class BackendExecutionContractTest(unittest.TestCase):
    def test_production_release_has_no_external_worker_host_dependency(self) -> None:
        workflow = PRODUCTION_RELEASE_WORKFLOW.read_text(encoding="utf-8").lower()
        for forbidden in (
            "railway",
            "run_approved_worker_host.py",
            "dockerfile.worker",
            "production_redis_url",
            "task_execution_mode=arq",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow)

    def test_production_release_deploys_backend_execution_mode(self) -> None:
        workflow = PRODUCTION_RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('TASK_EXECUTION_MODE: backend', workflow)
        self.assertIn('--env "TASK_EXECUTION_MODE=backend"', workflow)
        self.assertIn("backend_execution_version", workflow)
        self.assertIn("backend_executor_digest", workflow)

    def test_backend_runtime_report_replaces_worker_heartbeat(self) -> None:
        workflow = PRODUCTION_RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("collect_runtime_report.py", workflow)
        self.assertIn("--backend-runtime-report", workflow)
        self.assertIn("--expected-release-role COMMERCIAL_7A", workflow)
        self.assertNotIn("--worker-heartbeat-report", workflow)

    def test_obsolete_worker_host_sources_are_absent(self) -> None:
        for path in (
            ROOT / "release" / "worker-host-contract.json",
            ROOT / "scripts" / "release" / "run_approved_worker_host.py",
            ROOT / "backend" / "Dockerfile.worker",
        ):
            with self.subTest(path=path):
                self.assertFalse(path.exists())


class PrivateEvidenceStoreTest(unittest.TestCase):
    def test_private_blob_is_create_once_read_back_and_store_id_bound(self) -> None:
        module = _private_store_module()
        objects: dict[str, bytes] = {}

        def putter(key, payload, **_kwargs):
            if key in objects:
                raise FileExistsError(key)
            objects[key] = bytes(payload)
            return {
                "pathname": key,
                "url": f"https://store-7a.private.blob.vercel-storage.com/{key}",
            }

        def getter(key, **_kwargs):
            return {"status_code": 200, "content": objects[key]}

        store = module.PrivateBlobEvidenceStore(
            store_id="store-7a",
            token="t" * 32,
            putter=putter,
            getter=getter,
        )
        first = store.put_create_once("artifacts/release/a/phase.json", b"{}\n")
        second = store.put_create_once("artifacts/release/a/phase.json", b"{}\n")
        self.assertEqual(first.state, "STORED")
        self.assertEqual(second.state, "ALREADY_STORED")
        self.assertNotIn("token", repr(first).lower())

    def test_private_blob_rejects_wrong_store_and_conflicting_existing_bytes(self) -> None:
        module = _private_store_module()
        wrong_store = module.PrivateBlobEvidenceStore(
            store_id="store-7a",
            token="t" * 32,
            putter=lambda key, _payload, **_kwargs: {
                "pathname": key,
                "url": f"https://other.private.blob.vercel-storage.com/{key}",
            },
            getter=lambda _key, **_kwargs: {"status_code": 200, "content": b"{}\n"},
        )
        with self.assertRaisesRegex(ValueError, "exact store"):
            wrong_store.put_create_once("artifacts/release/a/phase.json", b"new\n")

        conflict = module.PrivateBlobEvidenceStore(
            store_id="store-7a",
            token="t" * 32,
            putter=lambda *_args, **_kwargs: (_ for _ in ()).throw(FileExistsError()),
            getter=lambda _key, **_kwargs: {"status_code": 200, "content": b"old\n"},
        )
        with self.assertRaisesRegex(RuntimeError, "different bytes"):
            conflict.put_create_once("artifacts/release/a/phase.json", b"new\n")


class ProductionWorkflowStaticContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="vowpic-production-release-"
        )
        self.tmp_root = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_commercial_state_machine_is_exact_and_cannot_skip_or_regress(self) -> None:
        register = _register_module()
        expected = (
            "RESERVED",
            "ROLLBACK_BASELINE_VERIFIED",
            "API_TARGET_STAGED",
            "MANIFEST_SEALED",
            "SCHEMA_0020",
            "DATA_SWITCHED",
            "ACCEPTANCE_READY",
            "TARGET_ACCEPTED",
            "TARGET_PROMOTED",
            "PUBLIC_INVALIDATED",
            "ACTIVATED",
            "OBSERVING",
            "7A_ACCEPTED",
        )
        self.assertEqual(register.COMMERCIAL_7A_PHASES, expected)
        for current, target in zip(expected, expected[1:]):
            register.validate_production_phase_transition(
                "COMMERCIAL_7A", current, target
            )
        with self.assertRaises(ValueError):
            register.validate_production_phase_transition(
                "COMMERCIAL_7A", "RESERVED", "API_TARGET_STAGED"
            )
        with self.assertRaises(ValueError):
            register.validate_production_phase_transition(
                "COMMERCIAL_7A", "TARGET_PROMOTED", "TARGET_ACCEPTED"
            )

    def test_phase_evidence_is_deterministic_fail_closed_and_secret_free(self) -> None:
        register = _register_module()
        evidence_root = self.tmp_root / "phase-evidence"
        evidence_root.mkdir(parents=True, exist_ok=True)
        first = evidence_root / "first.json"
        second = evidence_root / "second.json"
        first.write_text(
            json.dumps({"schema": "proof.v1", "passed": True, "id": "a"}),
            encoding="utf-8",
        )
        second.write_text(
            json.dumps({"schema": "proof.v1", "decision": "PASS", "id": "b"}),
            encoding="utf-8",
        )
        one = register.build_phase_evidence(
            phase="DATA_SWITCHED",
            evidence={"commercial-report": first, "media-report": second},
            coordinates={"runtime_bundle_id": "rtb_" + "b" * 64},
        )
        two = register.build_phase_evidence(
            phase="DATA_SWITCHED",
            evidence={"media-report": second, "commercial-report": first},
            coordinates={"runtime_bundle_id": "rtb_" + "b" * 64},
        )
        self.assertEqual(one, two)
        self.assertRegex(one["phase_evidence_sha256"], r"^[0-9a-f]{64}$")

        failed = evidence_root / "failed.json"
        failed.write_text(json.dumps({"passed": False}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "failed"):
            register.build_phase_evidence(
                phase="DATA_SWITCHED",
                evidence={"commercial-report": failed},
                coordinates={},
            )

        secret = evidence_root / "secret.json"
        secret.write_text(json.dumps({"access_token": "forbidden"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sensitive"):
            register.build_phase_evidence(
                phase="DATA_SWITCHED",
                evidence={"commercial-report": secret},
                coordinates={},
            )

    def test_phase_cas_decision_is_idempotent_and_rejects_drift(self) -> None:
        register = _register_module()
        row = {
            "id": "00000000-0000-0000-0000-000000000071",
            "kind": "COMMERCIAL_7A",
            "phase": "RESERVED",
            "phase_rank": 0,
            "version": 1,
            "runtime_bundle_id": None,
            "worker_deployment_id": None,
            "worker_image_digest": None,
            "api_deployment_id": None,
            "api_deployment_url": None,
            "report_sha256": None,
        }
        evidence_hash = "e" * 64
        bindings = {"runtime_bundle_id": "rtb_" + "a" * 64}
        decision = register.decide_production_phase_advance(
            row,
            expected_phase="RESERVED",
            target_phase="ROLLBACK_BASELINE_VERIFIED",
            evidence_sha256=evidence_hash,
            bindings=bindings,
        )
        self.assertEqual(decision["state"], "ADVANCE")
        self.assertEqual(decision["updates"]["phase_rank"], 1)
        self.assertEqual(decision["updates"]["report_sha256"], evidence_hash)

        advanced = {**row, **decision["updates"], "version": 2}
        replay = register.decide_production_phase_advance(
            advanced,
            expected_phase="RESERVED",
            target_phase="ROLLBACK_BASELINE_VERIFIED",
            evidence_sha256=evidence_hash,
            bindings=bindings,
        )
        self.assertEqual(replay["state"], "ALREADY_ADVANCED")
        with self.assertRaisesRegex(ValueError, "drift"):
            register.decide_production_phase_advance(
                advanced,
                expected_phase="RESERVED",
                target_phase="ROLLBACK_BASELINE_VERIFIED",
                evidence_sha256="f" * 64,
                bindings=bindings,
            )

    def test_backend_runtime_is_required_at_acceptance_and_promotion(self) -> None:
        register = _register_module()
        self.assertEqual(
            register._PHASE_EVIDENCE_ARGUMENTS["ACCEPTANCE_READY"],
            ("backend_runtime_report",),
        )
        self.assertEqual(
            register._PHASE_EVIDENCE_ARGUMENTS["TARGET_PROMOTED"],
            ("promotion_report", "backend_runtime_report"),
        )
        self.assertNotIn("WORKER_STAGED", register.COMMERCIAL_7A_PHASES)
        self.assertNotIn("WORKER_RUNNING", register.COMMERCIAL_7A_PHASES)

    def test_pre_schema_phase_reports_form_a_create_once_hash_chain(self) -> None:
        register = _register_module()
        activation = {
            "id": "00000000-0000-0000-0000-000000000071",
            "environment": "production",
            "kind": "COMMERCIAL_7A",
            "source_sha": "a" * 40,
            "phase": "RESERVED",
            "report_sha256": None,
        }
        first_evidence = register.build_phase_evidence(
            phase="ROLLBACK_BASELINE_VERIFIED",
            evidence={"inspect-report": self._write_phase_report("baseline-inspect")},
            coordinates={"runtime_bundle_id": "rtb_" + "b" * 64},
        )
        first, first_raw, first_sha = register.build_chained_phase_report(
            activation=activation,
            phase_evidence=first_evidence,
            private_evidence_prefix="artifacts/release/a/run-1",
            previous_report=None,
        )
        self.assertEqual(first["previous_report_sha256"], None)
        self.assertEqual(first_sha, __import__("hashlib").sha256(first_raw).hexdigest())
        self.assertEqual(len(first["evidence_chain"]), 1)

        second_activation = {
            **activation,
            "phase": "ROLLBACK_BASELINE_VERIFIED",
            "phase_rank": 1,
            "report_sha256": first_sha,
        }
        second_evidence = register.build_phase_evidence(
            phase="API_TARGET_STAGED",
            evidence={"inspect-report": self._write_phase_report("target-inspect")},
            coordinates={"deployment_id": "dpl_target"},
        )
        second, _second_raw, _second_sha = register.build_chained_phase_report(
            activation=second_activation,
            phase_evidence=second_evidence,
            private_evidence_prefix="artifacts/release/a/run-1",
            previous_report=first,
        )
        self.assertEqual(second["previous_report_sha256"], first_sha)
        self.assertEqual(second["evidence_chain"][0]["report_sha256"], first_sha)
        self.assertEqual(
            [item["phase"] for item in second["evidence_chain"]],
            ["ROLLBACK_BASELINE_VERIFIED", "API_TARGET_STAGED"],
        )

    def test_migration_parent_is_exact_and_only_identical_retry_is_reusable(self) -> None:
        register = _register_module()
        activation = {
            "id": "00000000-0000-0000-0000-000000000071",
            "environment": "production",
            "kind": "COMMERCIAL_7A",
            "phase": "MANIFEST_SEALED",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "manifest_sha256": "c" * 64,
        }
        record = register.build_migration_parent_record(
            activation,
            inventory_sha256="d" * 64,
            approval="migration-approval",
            workflow_run_id="12345",
            workflow_attempt=2,
        )
        self.assertEqual(record["release_activation_id"], activation["id"])
        self.assertEqual(record["mode"], "COMMERCIAL_7A_PARENT")
        self.assertEqual(record["state"], "ACTIVE")
        replay = register.decide_migration_parent(existing=[record], requested=record)
        self.assertEqual(replay["id"], record["id"])
        with self.assertRaisesRegex(ValueError, "different coordinates"):
            register.decide_migration_parent(
                existing=[{**record, "inventory_sha256": "e" * 64}],
                requested=record,
            )

    def test_staged_auth_origin_state_is_deterministic_and_exactly_restorable(self) -> None:
        module = _path_module("configure_staged_auth_origin", AUTH_ORIGIN)
        activation = {
            "id": "00000000-0000-0000-0000-000000000071",
            "environment": "production",
            "kind": "COMMERCIAL_7A",
            "phase": "ACCEPTANCE_READY",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_target",
            "api_deployment_url": "https://vowpic-target.vercel.app",
            "manifest_sha256": "c" * 64,
        }
        first = module.build_state(
            activation,
            manifest_sha256="c" * 64,
            project_ref="abcdefghijklmnopqrst",
            current_allow_list="https://www.vowpic.com/pages/auth/callback",
        )
        second = module.build_state(
            activation,
            manifest_sha256="c" * 64,
            project_ref="abcdefghijklmnopqrst",
            current_allow_list="https://www.vowpic.com/pages/auth/callback",
        )
        self.assertEqual(first, second)
        module.validate_state(first)
        self.assertEqual(
            first["target_uri_allow_list"],
            "https://www.vowpic.com/pages/auth/callback,"
            "https://vowpic-target.vercel.app/pages/auth/callback",
        )
        with self.assertRaisesRegex(ValueError, "already exists"):
            module.build_state(
                activation,
                manifest_sha256="c" * 64,
                project_ref="abcdefghijklmnopqrst",
                current_allow_list=first["target_uri_allow_list"],
            )

    def test_acceptance_binding_cleanup_report_fails_if_unused_rows_remain(self) -> None:
        module = _path_module("cleanup_acceptance_bindings", BINDING_CLEANUP)
        activation = {
            "id": "00000000-0000-0000-0000-000000000071",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
        }
        before = {
            "total": 2,
            "consumed": 1,
            "revoked": 0,
            "unused_unrevoked": 1,
            "active_unused": 1,
            "expired_unused": 0,
        }
        after = {
            "total": 2,
            "consumed": 1,
            "revoked": 1,
            "unused_unrevoked": 0,
            "active_unused": 0,
            "expired_unused": 0,
        }
        report = module.build_cleanup_report(
            activation=activation,
            deployment_id="dpl_target",
            before=before,
            revoked_now=1,
            after=after,
            require_zero_unused=True,
            completed_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
        )
        self.assertTrue(report["passed"])
        self.assertTrue(report["zero_unused"])
        with self.assertRaisesRegex(ValueError, "unused"):
            module.build_cleanup_report(
                activation=activation,
                deployment_id="dpl_target",
                before=before,
                revoked_now=0,
                after=before,
                require_zero_unused=True,
            )

    def test_activation_plan_is_ordered_and_never_bulk_enables_google_only(self) -> None:
        module = _path_module("apply_activation_plan", ACTIVATION_PLAN)
        plan = json.loads((ROOT / "release/activation-plan.json").read_text(encoding="utf-8"))
        module.validate_plan(plan)
        activation = {
            "id": "00000000-0000-0000-0000-000000000071",
            "api_deployment_id": "dpl_target",
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "worker_image_digest": "sha256:" + "c" * 64,
            "manifest_sha256": "d" * 64,
        }
        google = module._desired_row(
            capability="google_auth",
            phase="google-auth-only",
            activation=activation,
            cohort_user_id=None,
            expires_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        generation = module._desired_row(
            capability="generation",
            phase="google-auth-only",
            activation=activation,
            cohort_user_id=None,
            expires_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(google["state"], "ACCEPTANCE_COHORT")
        self.assertEqual(generation["state"], "OFF")
        self.assertIsNone(generation["deployment_id"])
        first = uuid.UUID("00000000-0000-4000-8000-000000000001")
        second = uuid.UUID("00000000-0000-4000-8000-000000000002")
        partner = module._desired_row(
            capability="partner_invite",
            phase="staged-user-cohort",
            activation=activation,
            cohort_user_ids=(first, second),
            expires_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(
            partner["cohort_user_ids"],
            [str(first), str(second)],
        )

    def _write_phase_report(self, name: str) -> Path:
        root = self.tmp_root / "chain-evidence"
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{name}.json"
        target.write_text(json.dumps({"schema": "proof.v1", "passed": True}), encoding="utf-8")
        return target

    def test_workflow_is_manual_exact_sha_only_and_derives_backend_runtime(self) -> None:
        workflow_path = ROOT / ".github" / "workflows" / "production-release.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        parsed = yaml.load(workflow, Loader=yaml.BaseLoader)
        self.assertEqual(set(parsed["on"]), {"workflow_dispatch"})
        self.assertEqual(
            set(parsed["on"]["workflow_dispatch"]["inputs"]),
            {
                "required_source_sha",
                "preview_run_id",
                "preview_run_attempt",
            },
        )
        for name in ("required_source_sha", "preview_run_id", "preview_run_attempt"):
            with self.subTest(input=name):
                self.assertEqual(
                    parsed["on"]["workflow_dispatch"]["inputs"][name]["required"],
                    "true",
                )
        authorize = parsed["jobs"]["authorize"]
        authorize_text = json.dumps(authorize, sort_keys=True)
        self.assertIn("vowpic-preview-release-", authorize_text)
        self.assertIn("verify_preview_release_package.py verify", authorize_text)
        self.assertIn("verify_github_workflow_run.py", authorize_text)
        self.assertIn(
            '--evidence-root \\"$RUNNER_TEMP/preview-release\\"',
            authorize_text,
        )
        self.assertIn("${{ inputs.preview_run_id }}", authorize_text)
        self.assertIn("${{ inputs.preview_run_attempt }}", authorize_text)
        self.assertNotIn("secrets.PRODUCTION_DATABASE_URL", authorize_text)
        self.assertNotIn("secrets.VERCEL_TOKEN", authorize_text)
        self.assertLess(
            workflow.index("verify_github_workflow_run.py"),
            workflow.index("actions/download-artifact"),
        )
        self.assertLess(
            workflow.index("verify_preview_release_package.py verify"),
            workflow.index("register_bundle.py reserve"),
        )
        for forbidden in (
            "\n  push:\n",
            "\n  pull_request:",
            "\n  schedule:",
            "\n  repository_dispatch:",
            "\n  workflow_call:",
        ):
            self.assertNotIn(forbidden, workflow)
        self.assertIn("group: vowpic-production-release", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertEqual(workflow.count("vars.PRODUCTION_BASE_URL"), 14)
        self.assertNotIn("secrets.PRODUCTION_BASE_URL", workflow)
        for impossible_preprovisioned_evidence in (
            "CREEM_TEST_EVIDENCE_BASE64",
            "PRODUCTION_PROVIDER_GRANT_REFERENCE_B64",
            "PRODUCTION_QUALITY_REVIEW_DRAFT_BASE64",
        ):
            self.assertNotIn(impossible_preprovisioned_evidence, workflow)
        self.assertNotIn("docker/build-push-action@", workflow)
        self.assertIn("verify_provider_capabilities.py", workflow)
        self.assertNotIn("verify_provider_grant_fetch.py", workflow)
        self.assertNotIn("provider-reconciliation-contract", workflow)
        for forbidden_fault_action in (
            "arm-response-drop-once",
            "inspect-response-drop",
            "disarm-response-drop",
            "provider-fault-cleanup",
            "provider-unknown-intent",
        ):
            self.assertNotIn(forbidden_fault_action, workflow)
        self.assertNotIn("inputs.worker_image_digest", workflow)
        self.assertLess(
            workflow.index("verify_provider_capabilities.py"),
            workflow.index("build_runtime_bundle_id.py"),
        )
        self.assertIn("backend_execution_version", workflow)
        self.assertIn("backend_executor_digest", workflow)

    def test_quality_review_is_submitted_after_the_exact_cases_exist(self) -> None:
        workflow_path = ROOT / ".github/workflows/production-release.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        parsed = yaml.load(workflow, Loader=yaml.BaseLoader)
        review_workflow_path = ROOT / ".github/workflows/production-quality-review.yml"
        review_workflow = review_workflow_path.read_text(encoding="utf-8")
        review_parsed = yaml.load(review_workflow, Loader=yaml.BaseLoader)
        self.assertEqual(
            parsed["jobs"]["quality-human-review"]["needs"],
            "linked-production-acceptance-prepare",
        )
        self.assertEqual(
            set(parsed["jobs"]["linked-production-acceptance"]["needs"]),
            {"reserve-stage-seal", "quality-human-review"},
        )
        self.assertIn("quality_review_handoff.py prepare", workflow)
        self.assertIn("--quality-review-request", workflow)
        self.assertLess(
            workflow.index("quality_review_handoff.py prepare"),
            workflow.index("Wait for the separately submitted review artifact"),
        )
        self.assertLess(
            workflow.index("Wait for the separately submitted review artifact"),
            workflow.index("--phase TARGET_ACCEPTED"),
        )
        prepare_job = parsed["jobs"]["linked-production-acceptance-prepare"]
        wait_job = parsed["jobs"]["quality-human-review"]
        final_job = parsed["jobs"]["linked-production-acceptance"]
        prepare_run = "\n".join(
            (step.get("run") or "") for step in prepare_job["steps"]
        )
        wait_serialized = json.dumps(wait_job, sort_keys=True)
        review_serialized = json.dumps(
            review_parsed["jobs"]["submit-review"], sort_keys=True
        )
        final_run = "\n".join(
            (step.get("run") or "") for step in final_job["steps"]
        )
        self.assertNotIn("PRODUCTION_QUALITY_REVIEW_DRAFT_BASE64", workflow)
        self.assertNotIn("QUALITY_REVIEW_SIGNING_KEY", wait_serialized)
        self.assertIn("gh api --method GET", wait_serialized)
        self.assertIn("review_run_id", wait_serialized)
        self.assertIn("github-token", wait_serialized)
        self.assertEqual(
            set(review_parsed["on"]["workflow_dispatch"]["inputs"]),
            {
                "required_source_sha",
                "release_run_id",
                "release_run_attempt",
                "completed_review_draft_base64",
            },
        )
        self.assertEqual(
            review_parsed["jobs"]["submit-review"]["environment"],
            "production",
        )
        self.assertIn(
            "${{ inputs.completed_review_draft_base64 }}",
            review_serialized,
        )
        self.assertIn(
            "${{ secrets.ACCEPTANCE_EVIDENCE_SIGNING_KEY }}",
            review_serialized,
        )
        self.assertIn(
            "${{ secrets.QUALITY_REVIEW_SIGNING_KEY }}",
            review_serialized,
        )
        self.assertIn("quality_review_handoff.py sign", review_serialized)
        self.assertIn("quality-review-completed-draft.json", review_serialized)
        self.assertNotIn("PRODUCTION_QUALITY_REVIEW_DRAFT_BASE64", review_workflow)
        self.assertNotIn("set-dispatch", prepare_run)
        self.assertNotIn("set-dispatch", final_run)
        self.assertIn("run_quality_acceptance.mjs", final_run)

    def test_release_never_collects_or_uses_payment_credentials(self) -> None:
        workflow = PRODUCTION_RELEASE_WORKFLOW.read_text(encoding="utf-8")
        linked_flow = (ROOT / "frontend/e2e/linked-production-flow.spec.ts").read_text(
            encoding="utf-8"
        )
        action_root = ROOT / "release/linked-acceptance-actions"
        quality = json.loads((action_root / "quality.json").read_text(encoding="utf-8"))
        account_finalize = json.loads(
            (action_root / "account_finalize.json").read_text(encoding="utf-8")
        )
        fixture_text = json.dumps([quality, account_finalize], sort_keys=True)
        cases_contract = json.loads(
            (ROOT / "release/quality-cases.json").read_text(encoding="utf-8")
        )["cases"]
        expected_ids = [item["id"] for item in cases_contract]
        self.assertEqual(quality["schema"], "vowpic.linked-production-action.v1")
        self.assertEqual(quality["phase"], "quality")
        self.assertEqual([item["id"] for item in quality["cases"]], expected_ids)
        expected_subjects = {item["id"]: item["subjects"] for item in cases_contract}
        for item in quality["cases"]:
            self.assertEqual(
                set(item), {"id", "template_id", "asset_paths", "style_text"}
            )
            self.assertEqual(len(item["asset_paths"]), expected_subjects[item["id"]])
            self.assertTrue(item["template_id"])
            self.assertTrue(item["style_text"])
        self.assertEqual(
            account_finalize,
            {
                "schema": "vowpic.linked-production-action.v1",
                "phase": "account_finalize",
                "currency": "USD",
                "cost_cap_minor_units": 1,
            },
        )
        asset_paths = {
            relative
            for item in quality["cases"]
            for relative in item["asset_paths"]
        }
        self.assertEqual(
            asset_paths,
            {
                "assets/primary_woman.jpg",
                "assets/partner_man.jpg",
                "assets/golden_woman.jpg",
                "assets/golden_man.jpg",
            },
        )
        for relative in asset_paths:
            asset = action_root / relative
            self.assertLess(asset.stat().st_size, 1_000_000)
            with Image.open(asset) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (768, 960))
        for forbidden in (
            "LINKED_ACCEPTANCE_PHASE=commercial",
            "LINKED_ACCEPTANCE_PHASE=subscription",
            "--staged-acceptance-report",
            "--subscription-acceptance-report",
            "payment_instrument",
            "card_number",
            "cvc",
            "completeCreemCheckout",
            "LINKED_ACCEPTANCE_ACTION_BUNDLE_BASE64",
            "extract_linked_acceptance_bundle.py",
        ):
            self.assertNotIn(forbidden, workflow + linked_flow + fixture_text)
        self.assertEqual(
            workflow.count(
                'LINKED_ACCEPTANCE_ACTION_ROOT="$GITHUB_WORKSPACE/release/linked-acceptance-actions"'
            ),
            3,
        )
        self.assertIn("LINKED_ACCEPTANCE_PHASE=account_finalize", workflow)
        self.assertIn('--account-cleanup-report "$RUNNER_TEMP/commercial-chain.json"', workflow)
        self.assertIn('--identity-report "$HANDOFF/auth-security.json"', workflow)

    def test_workflow_persists_every_commercial_phase_in_order(self) -> None:
        workflow = (ROOT / ".github/workflows/production-release.yml").read_text(
            encoding="utf-8"
        )
        phase_markers = [
            "--phase ROLLBACK_BASELINE_VERIFIED",
            "--phase API_TARGET_STAGED",
            "--phase MANIFEST_SEALED",
            "--phase SCHEMA_0020",
            "--phase DATA_SWITCHED",
            "--phase ACCEPTANCE_READY",
            "--phase TARGET_ACCEPTED",
            "--phase TARGET_PROMOTED",
            "--phase PUBLIC_INVALIDATED",
            "--phase ACTIVATED",
            "--phase OBSERVING",
        ]
        positions = [workflow.index(marker) for marker in phase_markers]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(workflow.count('"$VERCEL_CLI" build --prod'), 1)
        self.assertEqual(workflow.count("deploy --prebuilt --prod --skip-domain"), 1)
        self.assertEqual(workflow.lower().count('"$vercel_cli" promote'), 1)
        self.assertIn('"$vercel_cli" rollback', workflow.lower())

    def test_runtime_preflight_follows_reservation_and_precedes_external_deployment(self) -> None:
        workflow_path = ROOT / ".github" / "workflows" / "production-release.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        payload = yaml.load(workflow, Loader=yaml.BaseLoader)

        reserve_steps = payload["jobs"]["reserve-stage-seal"]["steps"]
        preflight = next(
            step
            for step in reserve_steps
            if step.get("name")
            == "Validate the complete immutable Production runtime before external deployment"
        )
        preflight_run = preflight["run"]
        self.assertIn("validate_production_runtime_environment.py", preflight_run)
        self.assertGreater(
            workflow.index("validate_production_runtime_environment.py"),
            workflow.index("register_bundle.py reserve"),
        )
        self.assertLess(
            workflow.index("validate_production_runtime_environment.py"),
            workflow.index("build_runtime_bundle_id.py"),
        )
        for name in (
            "PRODUCTION_RUNTIME_DATABASE_URL",
            "PRODUCTION_CONTROL_PLANE_DATABASE_URL",
            "PRODUCTION_SECRET_KEY",
            "ACCEPTANCE_IDENTITY_HMAC_KEY",
            "PRIVATE_BLOB_READ_WRITE_TOKEN",
            "VERCEL_TOKEN",
            "VERCEL_PROJECT_ID",
            "VERCEL_ORG_ID",
            "PRODUCTION_WENWEN_VISION_API_KEY",
            "CREEM_API_KEY",
            "CREEM_WEBHOOK_SECRET",
            "CLEANUP_CRON_TOKEN",
            "PRODUCTION_SUPABASE_URL",
            "PRODUCTION_SUPABASE_ANON_KEY",
            "PRODUCTION_SUPPORT_MONITORED",
            "PRODUCTION_CANARY_MAX_COST_MINOR",
        ):
            self.assertIn(name, json.dumps(preflight, sort_keys=True))
        self.assertIn("verify_vercel_runtime_secret.py", preflight_run)
        self.assertIn("--secret-name EVOLINK_API_KEY", preflight_run)
        self.assertNotIn("secrets.EVOLINK_API_KEY", json.dumps(preflight, sort_keys=True))

        coordinate_step = next(
            step
            for step in reserve_steps
            if step.get("name")
            == "Read inventory, resolve exact Preview, and reserve the release lease"
        )
        self.assertEqual(coordinate_step["env"]["GITHUB_TOKEN"], "${{ github.token }}")
        self.assertNotIn("RELEASE_PRIVATE_EVIDENCE_ROOT", workflow)
        self.assertNotIn("--private-evidence-root-env", coordinate_step["run"])

        deploy_step = next(
            step
            for step in reserve_steps
            if step.get("name")
            == "Build once and deploy one staged website-backend target"
        )
        deploy_run = deploy_step["run"]
        self.assertNotIn(
            "VERCEL_AUTOMATION_BYPASS_SECRET",
            json.dumps(deploy_step, sort_keys=True),
        )
        self.assertEqual(deploy_run.count("${DEPLOY_ENV_ARGS[@]}"), 1)
        self.assertEqual(deploy_run.count("${DEPLOY_META_ARGS[@]}"), 1)
        for binding in (
            'RUNTIME_ENVIRONMENT=production',
            'RELEASE_ROLE=COMMERCIAL_7A',
            'DATABASE_URL=$DATABASE_URL',
            'CONTROL_PLANE_DATABASE_URL=$CONTROL_PLANE_DATABASE_URL',
            'TASK_EXECUTION_MODE=backend',
            'STORAGE_PROVIDER=vercel',
            'WENWEN_VISION_API_KEY=$WENWEN_VISION_API_KEY',
            'CREEM_API_KEY=$CREEM_API_KEY',
            'SUPABASE_URL=$SUPABASE_URL',
            'SUPPORT_MONITORED=$SUPPORT_MONITORED',
            'CRON_SECRET=$CLEANUP_CRON_TOKEN',
        ):
            self.assertIn(binding, deploy_run)
        self.assertNotIn('EVOLINK_API_KEY=$EVOLINK_API_KEY', deploy_run)

    def test_migrations_cleanup_reconciliation_and_observation_are_separate(self) -> None:
        workflow = (ROOT / ".github/workflows/production-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("uses: ./.github/workflows/data-migration.yml", workflow)
        self.assertIn("expected_media_delete_dry_sha256", workflow)
        self.assertIn("if: ${{ failure() || cancelled() }}", workflow)
        self.assertIn("reconcile-failure:", workflow)
        self.assertNotIn("provider-fault-cleanup:", workflow)
        self.assertIn("observe_release.py start", workflow)
        self.assertNotRegex(workflow, r"(?i)(sleep|timeout).*24\s*(h|hour)")
        observation = ROOT / ".github" / "workflows" / "release-observation.yml"
        self.assertTrue(observation.exists(), "release-observation.yml is missing")
        observation_payload = yaml.load(
            observation.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )
        self.assertEqual(
            set(observation_payload["jobs"]["observation-recovery"]["needs"]),
            {
                "bootstrap",
                "sample",
                "finalize",
                "emergency-handler",
                "resume-finalization",
            },
        )
        observation_source = observation.read_text(encoding="utf-8")
        self.assertIn("RESUME_FINALIZATION", observation_source)
        self.assertIn("ALREADY_PASSED", observation_source)
        self.assertIn(
            "needs.emergency-handler.outputs.action == 'FAIL_AND_SHUTDOWN'",
            observation_source,
        )
        self.assertIn("read_release_observation_metrics_v1", (
            ROOT
            / "backend/alembic/versions/20260710_0020_partner_consent.py"
        ).read_text(encoding="utf-8"))
        self.assertIn("ensure_observation_cleanup_cycle.py", observation_source)
        self.assertIn("collect_observation_metrics.py", observation_source)
        self.assertIn("OBSERVATION_READ_DATABASE_URL", observation_source)
        self.assertIn("OBSERVATION_WRITE_DATABASE_URL", observation_source)
        self.assertIn(
            'test -n "$OBSERVATION_READ_DATABASE_URL"',
            observation_source,
        )
        self.assertIn(
            'if test -z "$OBSERVATION_READ_DATABASE_URL"',
            observation_source,
        )
        self.assertIn(
            'test "$RELEASE_ROLE" = "SAFE_BASELINE"',
            observation_source,
        )
        self.assertIn(
            '${PRODUCTION_BASE_URL%/}/api/v1/version',
            observation_source,
        )
        self.assertIn("environment: production-observation", observation_source)
        sample_job = observation_payload["jobs"]["sample"]
        sample_run = "\n".join(
            step.get("run") or "" for step in sample_job["steps"]
        )
        self.assertIn(
            "--database-url-env OBSERVATION_WRITE_DATABASE_URL",
            sample_run,
        )
        self.assertNotIn("PRODUCTION_MIGRATION_DATABASE_URL", json.dumps(sample_job))
        observation_provisioner = (
            ROOT
            / "scripts"
            / "release"
            / "provision_observation_database_logins.py"
        ).read_text(encoding="utf-8")
        self.assertIn("OBSERVATION_READER_LOGIN", observation_provisioner)
        self.assertIn("OBSERVATION_WRITER_LOGIN", observation_provisioner)
        self.assertNotIn("vowpic_control_writer_login", observation_provisioner)
        self.assertNotIn(
            "secrets.OBSERVATION_METRICS_INPUT_PATH", observation_source
        )
        bootstrap = observation_payload["jobs"]["bootstrap"]
        bootstrap_run = "\n".join(
            step.get("run") or "" for step in bootstrap["steps"]
        )
        self.assertIn("psql", bootstrap_run)
        self.assertNotIn("actions/checkout", json.dumps(bootstrap, sort_keys=True))

        release_payload = yaml.load(
            (ROOT / ".github/workflows/production-release.yml").read_text(
                encoding="utf-8"
            ),
            Loader=yaml.BaseLoader,
        )
        failure_step = next(
            step
            for step in release_payload["jobs"]["reconcile-failure"]["steps"]
            if step.get("name")
            == "Keep flags OFF and use rollback rather than re-Promote"
        )
        self.assertEqual(
            failure_step["env"]["PRIVATE_EVIDENCE_READ_WRITE_TOKEN"],
            "${{ secrets.PRIVATE_EVIDENCE_READ_WRITE_TOKEN }}",
        )
        self.assertIn(
            "backend/scripts/rollback_baseline_evidence.py read",
            failure_step["run"],
        )

    def test_every_phase_advance_receives_the_private_evidence_store(self) -> None:
        workflow_path = ROOT / ".github" / "workflows" / "production-release.yml"
        payload = yaml.load(
            workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )
        found = 0
        for job in payload["jobs"].values():
            job_env = job.get("env") or {}
            for step in job.get("steps") or []:
                if "register_bundle.py advance" not in (step.get("run") or ""):
                    continue
                found += 1
                effective_env = {**job_env, **(step.get("env") or {})}
                self.assertIn("PRIVATE_EVIDENCE_STORE_ID", effective_env)
                self.assertIn("PRIVATE_EVIDENCE_WRITE_TOKEN", effective_env)
        self.assertEqual(found, 9)

    def test_every_python_release_job_installs_the_hash_locked_runtime(self) -> None:
        for relative_path in (
            ".github/workflows/production-release.yml",
            ".github/workflows/release-observation.yml",
        ):
            payload = yaml.load(
                (ROOT / relative_path).read_text(encoding="utf-8"),
                Loader=yaml.BaseLoader,
            )
            for job_name, job in payload["jobs"].items():
                steps = job.get("steps") or []
                run_text = "\n".join(step.get("run") or "" for step in steps)
                if "python " not in run_text:
                    continue
                uses = {step.get("uses") for step in steps if step.get("uses")}
                self.assertIn(
                    "./.github/actions/setup-release-python",
                    uses,
                    f"{relative_path}:{job_name}",
                )
        action = (
            ROOT / ".github/actions/setup-release-python/action.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
            action,
        )
        self.assertIn(
            "--require-hashes -r backend/requirements.lock.txt",
            action,
        )
        self.assertIn("python -m pip check", action)

    def test_run_bound_canaries_and_failure_cleanup_are_fail_closed(self) -> None:
        workflow = (ROOT / ".github/workflows/production-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("PRODUCTION_CANARY_INPUT_BUNDLE_BASE64", workflow)
        self.assertNotIn("PRODUCTION_CANARY_GOOGLE_STORAGE_STATE_BASE64", workflow)
        self.assertNotIn("extract_production_canary_bundle.py", workflow)
        self.assertIn(
            "secrets.PRODUCTION_GOOGLE_PARTNER_STORAGE_STATE_BASE64",
            workflow,
        )
        self.assertIn("secrets.PRODUCTION_GOOGLE_PARTNER_EMAIL", workflow)
        canary = (ROOT / "frontend/e2e/production-canary.spec.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("PRODUCTION_CANARY_STATE_PATH", canary)
        self.assertIn("return value(asset || {}, 'id');", canary)
        self.assertIn("@cleanup close the exact Production canary account", canary)
        self.assertIn("media_cleanup_pending", canary)
        activation = (ROOT / "scripts/release/activate_with_canaries.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_verify_cleanup_absence", activation)
        self.assertIn('"storage_read_outcome": "NOT_FOUND"', activation)
        self.assertIn("configure_staged_auth_origin.py cleanup", workflow)
        self.assertIn("cleanup_acceptance_bindings.py", workflow)
        self.assertIn('test "$ACCEPTANCE_CLEANUP_STATUS" -eq 0', workflow)
        self.assertLess(
            workflow.index("apply_activation_plan.py --phase emergency-off"),
            workflow.index("configure_staged_auth_origin.py cleanup"),
        )
        self.assertLess(
            workflow.index("configure_staged_auth_origin.py cleanup"),
            workflow.index('"$VERCEL_CLI" rollback'),
        )

    def test_protected_playwright_has_distinct_preview_and_production_modes(self) -> None:
        config = (ROOT / "frontend/playwright.config.ts").read_text(encoding="utf-8")
        main_flow = (ROOT / "frontend/e2e/main-flow.spec.ts").read_text(encoding="utf-8")
        self.assertIn("RUN_PREVIEW_E2E", config)
        self.assertIn("RUN_PRODUCTION_E2E", config)
        self.assertIn("selectedModes !== 1", config)
        self.assertIn("projects: protectedRun ?", config)
        for header in (
            "X-User-OpenID",
            "X-Visitor-Id",
            "X-Forwarded-User",
            "X-Admin-Token",
        ):
            self.assertIn(header, main_flow)
        self.assertIn("AUTH_ACCEPTANCE_INPUT_PATH", main_flow)
        self.assertIn("createHmac", main_flow)
        self.assertNotIn("/api/v1/admin", main_flow)


if __name__ == "__main__":
    unittest.main()
