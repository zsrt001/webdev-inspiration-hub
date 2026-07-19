"""Production release state-machine and approved Worker-host truth contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import importlib
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import unittest
from unittest.mock import patch
import uuid
import yaml


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "release" / "run_approved_worker_host.py"
REGISTER = ROOT / "scripts" / "release" / "register_bundle.py"
PRIVATE_STORE = ROOT / "scripts" / "release" / "private_evidence_store.py"
PROVIDER_CANARY = ROOT / "scripts" / "release" / "prepare_provider_unknown_canary.py"
AUTH_ORIGIN = ROOT / "scripts" / "release" / "configure_staged_auth_origin.py"
BINDING_CLEANUP = ROOT / "scripts" / "release" / "cleanup_acceptance_bindings.py"
ACTIVATION_PLAN = ROOT / "scripts" / "release" / "apply_activation_plan.py"
CANARY_EXTRACTOR = ROOT / "scripts" / "release" / "extract_production_canary_bundle.py"
CONTRACT = ROOT / "release" / "worker-host-contract.json"
TMP = ROOT / ".tmp" / "production-release-workflow"
CANARY_TMP = ROOT / ".tmp" / "production-canary-extractor"
PYTHON = shutil.which("python") or "python"


def _module():
    spec = importlib.util.spec_from_file_location("run_approved_worker_host", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _register_module():
    spec = importlib.util.spec_from_file_location("register_bundle", REGISTER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _private_store_module():
    return importlib.import_module("scripts.release.private_evidence_store")


def _provider_canary_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_provider_unknown_canary", PROVIDER_CANARY
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _path_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductionCanaryBundleExtractorTest(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(CANARY_TMP, ignore_errors=True)
        CANARY_TMP.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(CANARY_TMP, ignore_errors=True)
        if CANARY_TMP.parent.exists() and not any(CANARY_TMP.parent.iterdir()):
            CANARY_TMP.parent.rmdir()

    @staticmethod
    def _input_bytes(capability: str) -> bytes:
        return (
            json.dumps(
                {
                    "schema": "vowpic.production-capability-canary-input.v1",
                    "capability": capability,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )

    def _archive(self, name: str, *, extra_member: tarfile.TarInfo | None = None) -> Path:
        module = _path_module("extract_production_canary_bundle", CANARY_EXTRACTOR)
        path = CANARY_TMP / name
        with tarfile.open(path, "w:gz") as bundle:
            for capability in module.CAPABILITIES:
                raw = self._input_bytes(capability)
                member = tarfile.TarInfo(f"{capability}.json")
                member.size = len(raw)
                bundle.addfile(member, BytesIO(raw))
            if extra_member is not None:
                raw = b"escape\n"
                extra_member.size = len(raw)
                bundle.addfile(extra_member, BytesIO(raw))
        return path

    def test_extracts_only_the_exact_capability_inputs(self) -> None:
        module = _path_module("extract_production_canary_bundle", CANARY_EXTRACTOR)
        archive = self._archive("valid.tar.gz")
        destination = CANARY_TMP / "inputs"
        with patch.dict(os.environ, {"RUNNER_TEMP": str(CANARY_TMP)}):
            report = module.extract_bundle(archive, destination)
        self.assertTrue(report["passed"])
        self.assertEqual(
            set(report["capability_input_sha256"]), set(module.CAPABILITIES)
        )
        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {f"{capability}.json" for capability in module.CAPABILITIES},
        )

    def test_rejects_path_traversal_without_writing_outside_the_destination(self) -> None:
        module = _path_module("extract_production_canary_bundle", CANARY_EXTRACTOR)
        archive = self._archive("traversal.tar.gz", extra_member=tarfile.TarInfo("../escape.txt"))
        with patch.dict(os.environ, {"RUNNER_TEMP": str(CANARY_TMP)}):
            with self.assertRaisesRegex(ValueError, "escaped"):
                module.extract_bundle(archive, CANARY_TMP / "traversal")
        self.assertFalse((CANARY_TMP / "escape.txt").exists())

    def test_rejects_links_and_other_non_regular_members(self) -> None:
        module = _path_module("extract_production_canary_bundle", CANARY_EXTRACTOR)
        link = tarfile.TarInfo("assets/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../google_auth.json"
        archive = self._archive("link.tar.gz", extra_member=link)
        with patch.dict(os.environ, {"RUNNER_TEMP": str(CANARY_TMP)}):
            with self.assertRaisesRegex(ValueError, "non-regular"):
                module.extract_bundle(archive, CANARY_TMP / "links")


class WorkerHostContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        if TMP.parent.exists() and not any(TMP.parent.iterdir()):
            TMP.parent.rmdir()

    def test_committed_host_contract_is_complete_but_deliberately_unapproved(self) -> None:
        module = _module()
        payload, digest = module.load_contract(CONTRACT, require_approved=False)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertFalse(payload["approved"])
        self.assertEqual(payload["status"], "NOT_APPROVED")
        self.assertEqual(set(payload["actions"]), set(module.ALLOWED_ACTIONS))
        with self.assertRaises(module.WorkerHostNotRun):
            module.load_contract(CONTRACT, require_approved=True)

    def test_unapproved_contract_exits_not_run_without_external_process_or_report(self) -> None:
        output = TMP / "worker-build.json"
        env = {**os.environ, "RUNNER_TEMP": str(TMP)}
        completed = subprocess.run(
            [
                PYTHON,
                str(RUNNER),
                "build-push",
                "--contract",
                str(CONTRACT),
                "--source-sha",
                "a" * 40,
                "--output",
                str(output),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 3)
        self.assertIn("NOT_RUN", completed.stderr)
        self.assertFalse(output.exists())
        self.assertEqual(list(TMP.glob("worker-host-*-request.json")), [])

    def test_mutable_image_and_unlisted_arguments_are_rejected(self) -> None:
        module = _module()
        payload, _ = module.load_contract(CONTRACT, require_approved=False)
        with self.assertRaisesRegex(ValueError, "immutable digest"):
            module.validate_action_inputs(
                payload,
                "deploy-suspended",
                {
                    "source-sha": "a" * 40,
                    "image-digest": "vowpic-worker:latest",
                    "runtime-bundle-id": "rtb_" + "b" * 64,
                },
            )
        with self.assertRaisesRegex(ValueError, "unexpected Worker host inputs"):
            module.validate_action_inputs(
                payload,
                "build-push",
                {"source-sha": "a" * 40, "shell-command": "echo"},
            )

    def test_host_response_must_be_exact_success_and_secret_free(self) -> None:
        module = _module()
        response = {
            "schema": module.RESPONSE_SCHEMA,
            "action": "build-push",
            "passed": True,
            "state": "BUILT",
            "coordinates": {"worker_image_digest": "sha256:" + "b" * 64},
            "observed_at": "2026-07-18T00:00:00+00:00",
        }
        self.assertEqual(
            module.validate_host_response(response, action="build-push")["state"],
            "BUILT",
        )
        with self.assertRaises(ValueError):
            module.validate_host_response(
                {**response, "coordinates": {"access_token": "secret"}},
                action="build-push",
            )
        with self.assertRaises(ValueError):
            module.validate_host_response(
                {**response, "passed": False},
                action="build-push",
            )


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
    def test_commercial_state_machine_is_exact_and_cannot_skip_or_regress(self) -> None:
        register = _register_module()
        expected = (
            "RESERVED",
            "WORKER_STAGED",
            "API_BASELINE_STAGED",
            "API_TARGET_STAGED",
            "MANIFEST_SEALED",
            "SCHEMA_0020",
            "WORKER_RUNNING",
            "BASELINE_PROMOTED",
            "DATA_SWITCHED",
            "WORKER_DISPATCH_ENABLED",
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
                "COMMERCIAL_7A", "RESERVED", "API_BASELINE_STAGED"
            )
        with self.assertRaises(ValueError):
            register.validate_production_phase_transition(
                "COMMERCIAL_7A", "TARGET_PROMOTED", "TARGET_ACCEPTED"
            )

    def test_phase_evidence_is_deterministic_fail_closed_and_secret_free(self) -> None:
        register = _register_module()
        evidence_root = TMP / "phase-evidence"
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
        bindings = {
            "runtime_bundle_id": "rtb_" + "a" * 64,
            "worker_deployment_id": "worker-7a-01",
            "worker_image_digest": "sha256:" + "b" * 64,
        }
        decision = register.decide_production_phase_advance(
            row,
            expected_phase="RESERVED",
            target_phase="WORKER_STAGED",
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
            target_phase="WORKER_STAGED",
            evidence_sha256=evidence_hash,
            bindings=bindings,
        )
        self.assertEqual(replay["state"], "ALREADY_ADVANCED")
        with self.assertRaisesRegex(ValueError, "drift"):
            register.decide_production_phase_advance(
                advanced,
                expected_phase="RESERVED",
                target_phase="WORKER_STAGED",
                evidence_sha256="f" * 64,
                bindings=bindings,
            )

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
            phase="WORKER_STAGED",
            evidence={"worker-build-report": self._write_phase_report("worker-build")},
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
            "phase": "WORKER_STAGED",
            "phase_rank": 1,
            "report_sha256": first_sha,
        }
        second_evidence = register.build_phase_evidence(
            phase="API_BASELINE_STAGED",
            evidence={"inspect-report": self._write_phase_report("baseline-inspect")},
            coordinates={"deployment_id": "dpl_baseline"},
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
            ["WORKER_STAGED", "API_BASELINE_STAGED"],
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

    def test_provider_response_drop_intent_is_deterministic_bounded_and_runtime_bound(self) -> None:
        register = _register_module()
        activation = {
            "id": "00000000-0000-0000-0000-000000000071",
            "environment": "production",
            "kind": "COMMERCIAL_7A",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "worker_deployment_id": "worker-production-1",
            "worker_image_digest": "sha256:" + "c" * 64,
            "workflow_run_id": "12345",
            "workflow_attempt": 2,
            "phase": "WORKER_DISPATCH_ENABLED",
        }
        expires = datetime(2026, 7, 19, tzinfo=timezone.utc)
        first = register.build_acceptance_fault_intent(
            activation,
            request_sha256="d" * 64,
            max_provider_submits=1,
            max_cost_minor_units=500,
            expires_at=expires,
        )
        second = register.build_acceptance_fault_intent(
            activation,
            request_sha256="d" * 64,
            max_provider_submits=1,
            max_cost_minor_units=500,
            expires_at=expires,
        )
        self.assertEqual(first, second)
        self.assertRegex(first["fault_intent_id"], r"^afi_[0-9a-f]{32}$")
        self.assertEqual(first["max_provider_submits"], 1)
        with self.assertRaises(ValueError):
            register.build_acceptance_fault_intent(
                activation,
                request_sha256="d" * 64,
                max_provider_submits=2,
                max_cost_minor_units=500,
                expires_at=expires,
            )

        bound = {
            **activation,
            "acceptance_fault_intent_id": first["fault_intent_id"],
        }
        disarm = {
            "schema": "vowpic.worker-host-adapter-report.v1",
            "passed": True,
            "action": "disarm-response-drop",
            "state": "ABSENT",
            "coordinates": {
                "fault_intent_id": first["fault_intent_id"],
                "runtime_bundle_id": activation["runtime_bundle_id"],
                "worker_deployment_id": activation["worker_deployment_id"],
                "rule_present": False,
                "runtime_rule_count": 0,
                "tombstone_persisted": True,
            },
        }
        register._validate_worker_fault_report(
            disarm,
            action="disarm-response-drop",
            activation=bound,
            require_absence=True,
            require_tombstone=True,
        )
        with self.assertRaisesRegex(ValueError, "absence"):
            register._validate_worker_fault_report(
                {
                    **disarm,
                    "coordinates": {**disarm["coordinates"], "rule_present": True},
                },
                action="disarm-response-drop",
                activation=bound,
                require_absence=True,
                require_tombstone=True,
            )

    def test_provider_unknown_canary_is_bound_to_one_real_queued_attempt(self) -> None:
        module = _provider_canary_module()
        activation = {
            "id": "00000000-0000-0000-0000-000000000071",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "manifest_sha256": "c" * 64,
            "api_deployment_id": "dpl_target",
            "worker_deployment_id": "worker-production-1",
            "worker_image_digest": "sha256:" + "d" * 64,
            "workflow_run_id": "12345",
            "workflow_attempt": 2,
        }
        fault_id = "afi_" + hashlib.sha256(
            (
                f"{activation['id']}:{activation['workflow_run_id']}:"
                f"{activation['workflow_attempt']}:provider-response-drop"
            ).encode("utf-8")
        ).hexdigest()[:32]
        links = {
            "order_id": "00000000-0000-4000-8000-000000000081",
            "reservation_id": "00000000-0000-4000-8000-000000000082",
            "job_id": "00000000-0000-4000-8000-000000000083",
            "attempt_id": "00000000-0000-4000-8000-000000000084",
            "fault_intent_id": fault_id,
            "client_correlation_id": "00000000-0000-4000-8000-000000000085",
        }
        queued = {
            "links": links,
            "cost_cap_minor_units": 500,
        }
        facts = {
            "order_id": links["order_id"],
            "reservation_id": links["reservation_id"],
            "job_id": links["job_id"],
            "attempt_id": links["attempt_id"],
            "submission_correlation_id": links["client_correlation_id"],
            "api_deployment_id": activation["api_deployment_id"],
            "runtime_bundle_id": activation["runtime_bundle_id"],
            "expected_worker_image_digest": activation["worker_image_digest"],
            "order_status": "QUEUED",
            "job_status": "QUEUED",
            "attempt_status": "PREPARED",
            "provider_job_id": None,
            "submission_accounting_state": "NOT_CAPTURED",
            "client_request_id": links["client_correlation_id"],
        }
        dispatch = {
            "schema": "vowpic.worker-host-adapter-report.v1",
            "action": "set-dispatch",
            "passed": True,
            "coordinates": {
                "runtime_bundle_id": activation["runtime_bundle_id"],
                "worker_deployment_id": activation["worker_deployment_id"],
                "dispatch_mode": "disabled",
            },
        }
        report = module.build_canary_report(
            activation=activation,
            queued_report=queued,
            queued_report_sha256="e" * 64,
            dispatch_report=dispatch,
            facts=facts,
            manifest_sha256=activation["manifest_sha256"],
            signing_key=b"provider-canary-test-signing-key-32bytes",
            produced_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["fault_intent_id"], fault_id)
        with self.assertRaisesRegex(ValueError, "pre-submit"):
            module.build_canary_report(
                activation=activation,
                queued_report=queued,
                queued_report_sha256="e" * 64,
                dispatch_report=dispatch,
                facts={**facts, "provider_job_id": "provider-task"},
                manifest_sha256=activation["manifest_sha256"],
                signing_key=b"provider-canary-test-signing-key-32bytes",
            )

    def test_staged_auth_origin_state_is_deterministic_and_exactly_restorable(self) -> None:
        module = _path_module("configure_staged_auth_origin", AUTH_ORIGIN)
        activation = {
            "id": "00000000-0000-0000-0000-000000000071",
            "environment": "production",
            "kind": "COMMERCIAL_7A",
            "phase": "WORKER_DISPATCH_ENABLED",
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
        root = TMP / "chain-evidence"
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{name}.json"
        target.write_text(json.dumps({"schema": "proof.v1", "passed": True}), encoding="utf-8")
        return target

    def test_workflow_is_manual_exact_sha_only_and_resolves_worker_digest(self) -> None:
        workflow_path = ROOT / ".github" / "workflows" / "production-release.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        parsed = yaml.load(workflow, Loader=yaml.BaseLoader)
        self.assertEqual(set(parsed["on"]), {"workflow_dispatch"})
        self.assertEqual(
            set(parsed["on"]["workflow_dispatch"]["inputs"]),
            {"required_source_sha"},
        )
        self.assertEqual(
            parsed["on"]["workflow_dispatch"]["inputs"]["required_source_sha"]["required"],
            "true",
        )
        for forbidden in (
            "push:",
            "pull_request:",
            "schedule:",
            "repository_dispatch:",
            "workflow_call:",
        ):
            self.assertNotIn(forbidden, workflow)
        self.assertIn("group: vowpic-production-release", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertEqual(workflow.count("vars.PRODUCTION_BASE_URL"), 6)
        self.assertNotIn("secrets.PRODUCTION_BASE_URL", workflow)
        self.assertIn("run_approved_worker_host.py build-push", workflow)
        self.assertIn("verify_commercial_provider_readiness.py", workflow)
        self.assertNotIn("inputs.worker_image_digest", workflow)
        self.assertLess(
            workflow.index("verify_commercial_provider_readiness.py"),
            workflow.index("run_approved_worker_host.py build-push"),
        )
        self.assertLess(
            workflow.index("run_approved_worker_host.py build-push"),
            workflow.index("build_runtime_bundle_id.py"),
        )

    def test_quality_review_is_a_bound_two_stage_human_gate(self) -> None:
        workflow_path = ROOT / ".github/workflows/production-release.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        parsed = yaml.load(workflow, Loader=yaml.BaseLoader)
        self.assertEqual(
            parsed["jobs"]["quality-human-review"]["environment"],
            "production-quality-review",
        )
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
            workflow.index("environment: production-quality-review"),
        )
        self.assertLess(
            workflow.index("environment: production-quality-review"),
            workflow.index("--phase TARGET_ACCEPTED"),
        )
        prepare_job = parsed["jobs"]["linked-production-acceptance-prepare"]
        review_job = parsed["jobs"]["quality-human-review"]
        final_job = parsed["jobs"]["linked-production-acceptance"]
        prepare_run = "\n".join(
            (step.get("run") or "") for step in prepare_job["steps"]
        )
        review_serialized = json.dumps(review_job, sort_keys=True)
        final_run = "\n".join(
            (step.get("run") or "") for step in final_job["steps"]
        )
        self.assertNotIn(
            "PRODUCTION_QUALITY_REVIEW_DRAFT_BASE64",
            json.dumps(prepare_job, sort_keys=True),
        )
        self.assertNotIn(
            "QUALITY_REVIEW_SIGNING_KEY",
            json.dumps(prepare_job, sort_keys=True),
        )
        self.assertIn(
            "${{ secrets.PRODUCTION_QUALITY_REVIEW_DRAFT_BASE64 }}",
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
        self.assertNotIn(
            "PRODUCTION_QUALITY_REVIEW_DRAFT_BASE64",
            json.dumps(final_job, sort_keys=True),
        )
        self.assertNotIn("PRODUCTION_QUALITY_REVIEW_BASE64", workflow)
        self.assertIn("--reason quality-human-review", prepare_run)
        self.assertLess(
            prepare_run.index("--reason quality-human-review"),
            prepare_run.index("quality-review-handoff"),
        )
        self.assertIn("--reason quality-human-review-complete", final_run)
        self.assertLess(
            final_run.index("run_quality_acceptance.mjs"),
            final_run.index("--reason quality-human-review-complete"),
        )

    def test_workflow_persists_every_commercial_phase_in_order(self) -> None:
        workflow = (ROOT / ".github/workflows/production-release.yml").read_text(
            encoding="utf-8"
        )
        phase_markers = [
            "--phase WORKER_STAGED",
            "--phase API_BASELINE_STAGED",
            "--phase API_TARGET_STAGED",
            "--phase MANIFEST_SEALED",
            "--phase SCHEMA_0020",
            "--phase WORKER_RUNNING",
            "--phase BASELINE_PROMOTED",
            "--phase DATA_SWITCHED",
            "--phase WORKER_DISPATCH_ENABLED",
            "--phase TARGET_ACCEPTED",
            "--phase TARGET_PROMOTED",
            "--phase PUBLIC_INVALIDATED",
            "--phase ACTIVATED",
            "--phase OBSERVING",
        ]
        positions = [workflow.index(marker) for marker in phase_markers]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(workflow.count('"$VERCEL_CLI" build --prod'), 1)
        self.assertEqual(workflow.count("deploy --prebuilt --prod --skip-domain"), 2)
        self.assertEqual(workflow.lower().count('"$vercel_cli" promote'), 2)
        self.assertIn('"$vercel_cli" rollback', workflow.lower())

    def test_migrations_cleanup_reconciliation_and_observation_are_separate(self) -> None:
        workflow = (ROOT / ".github/workflows/production-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("uses: ./.github/workflows/data-migration.yml", workflow)
        self.assertIn("expected_media_delete_dry_sha256", workflow)
        self.assertIn("if: ${{ failure() || cancelled() }}", workflow)
        self.assertIn("if: ${{ always() }}", workflow)
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
        self.assertEqual(found, 12)

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

    def test_protected_canary_bundle_and_failure_cleanup_are_fail_closed(self) -> None:
        workflow = (ROOT / ".github/workflows/production-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("extract_production_canary_bundle.py", workflow)
        self.assertNotIn("tar -xzf", workflow)
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
