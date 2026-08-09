"""Preview release evidence rehash and GitHub workflow metadata tests."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from backend.scripts.verify_github_workflow_run import validate_workflow_run
from backend.scripts.verify_preview_release_package import (
    _contract,
    build_package,
    required_case_ids,
    verify_package,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE_SHA = "a" * 40
RUN_ID = "123456789"
RUN_ATTEMPT = 2
ACTIVATION_ID = "00000000-0000-4000-8000-000000000071"
DEPLOYMENT_ID = "dpl_preview_exact"
MANIFEST_SHA256 = "b" * 64


class PreviewReleasePackageTest(unittest.TestCase):
    def _evidence_root(self, root: Path) -> Path:
        evidence_root = root / "evidence"
        contract_path = ROOT / "release" / "gates.json"
        contract, gate_sha, cases = _contract(contract_path)
        required = required_case_ids(contract, cases)
        scope_runtime: dict[str, str] = {}
        for index, case_id in enumerate(sorted(required), start=1):
            scope = str(cases[case_id]["runtime_scope"])
            scope_runtime.setdefault(scope, f"rtb_{index:064x}")
            row = {
                "schema": "vowpic.gate-evidence.v1",
                "case_id": case_id,
                "status": "PASS",
                "execution": "completed",
                "source_sha": SOURCE_SHA,
                "gate_contract_sha256": gate_sha,
                "test_count": 1,
                "runtime_bundle_id": scope_runtime[scope],
            }
            if scope == "preview_commercial":
                row.update(
                    {
                        "activation_id": ACTIVATION_ID,
                        "workflow_run_id": RUN_ID,
                        "workflow_attempt": RUN_ATTEMPT,
                        "api_deployment_id": DEPLOYMENT_ID,
                        "manifest_sha256": MANIFEST_SHA256,
                    }
                )
            path = evidence_root / scope / f"{case_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(row), encoding="utf-8")
        return evidence_root

    def test_verification_rehashes_every_downloaded_case(self) -> None:
        contract_path = ROOT / "release" / "gates.json"
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = self._evidence_root(Path(directory))
            package = build_package(
                evidence_root=evidence_root,
                gate_contract_path=contract_path,
                source_sha=SOURCE_SHA,
                workflow_run_id=RUN_ID,
                workflow_attempt=RUN_ATTEMPT,
                now=datetime(2026, 7, 23, tzinfo=timezone.utc),
            )
            verified = verify_package(
                package,
                evidence_root=evidence_root,
                gate_contract_path=contract_path,
                source_sha=SOURCE_SHA,
                workflow_run_id=RUN_ID,
                workflow_attempt=RUN_ATTEMPT,
            )
            self.assertTrue(verified["passed"])
            self.assertEqual(
                verified["preview_commercial_binding"]["activation_id"],
                ACTIVATION_ID,
            )

            case_path = next(evidence_root.rglob("*.json"))
            tampered = json.loads(case_path.read_text(encoding="utf-8"))
            tampered["test_count"] = 2
            case_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash drift"):
                verify_package(
                    package,
                    evidence_root=evidence_root,
                    gate_contract_path=contract_path,
                    source_sha=SOURCE_SHA,
                    workflow_run_id=RUN_ID,
                    workflow_attempt=RUN_ATTEMPT,
                )

    def test_package_rejects_preview_binding_from_another_attempt(self) -> None:
        contract_path = ROOT / "release" / "gates.json"
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = self._evidence_root(Path(directory))
            for commercial in evidence_root.rglob("*.json"):
                row = json.loads(commercial.read_text(encoding="utf-8"))
                if row.get("activation_id") != ACTIVATION_ID:
                    continue
                row["workflow_attempt"] = RUN_ATTEMPT + 1
                commercial.write_text(json.dumps(row), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "workflow attempt"):
                build_package(
                    evidence_root=evidence_root,
                    gate_contract_path=contract_path,
                    source_sha=SOURCE_SHA,
                    workflow_run_id=RUN_ID,
                    workflow_attempt=RUN_ATTEMPT,
                    now=datetime(2026, 7, 23, tzinfo=timezone.utc),
                )

    def test_github_attempt_must_be_completed_success_on_exact_main_sha(self) -> None:
        payload = {
            "id": int(RUN_ID),
            "run_attempt": RUN_ATTEMPT,
            "head_sha": SOURCE_SHA,
            "head_branch": "main",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "path": ".github/workflows/integration.yml",
            "updated_at": "2026-07-23T12:00:00Z",
            "repository": {"full_name": "zsrt001/webdev-inspiration-hub"},
        }
        proof = validate_workflow_run(
            payload,
            repository="zsrt001/webdev-inspiration-hub",
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            source_sha=SOURCE_SHA,
        )
        self.assertEqual(
            proof["metadata_sha256"],
            hashlib.sha256(
                json.dumps(
                    {
                        key: proof[key]
                        for key in (
                            "repository",
                            "run_id",
                            "run_attempt",
                            "head_sha",
                            "head_branch",
                            "event",
                            "status",
                            "conclusion",
                            "path",
                            "updated_at",
                        )
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode()
            ).hexdigest(),
        )
        payload["conclusion"] = "failure"
        with self.assertRaisesRegex(ValueError, "exact successful"):
            validate_workflow_run(
                payload,
                repository="zsrt001/webdev-inspiration-hub",
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                source_sha=SOURCE_SHA,
            )

    def test_github_attempt_accepts_only_the_exact_protected_privacy_push(self) -> None:
        workflow_path = ".github/workflows/google-auth-protected-privacy.yml"
        payload = {
            "id": int(RUN_ID),
            "run_attempt": RUN_ATTEMPT,
            "head_sha": SOURCE_SHA,
            "head_branch": "main",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "path": workflow_path,
            "updated_at": "2026-08-09T12:00:00Z",
            "repository": {"full_name": "zsrt001/webdev-inspiration-hub"},
        }
        proof = validate_workflow_run(
            payload,
            repository="zsrt001/webdev-inspiration-hub",
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            source_sha=SOURCE_SHA,
            workflow_path=workflow_path,
        )
        self.assertTrue(proof["passed"])
        self.assertEqual(proof["event"], "push")
        self.assertEqual(proof["path"], workflow_path)
        rejected_attempts = {
            "status_queued": {"status": "queued"},
            "status_in_progress": {"status": "in_progress"},
            "conclusion_failure": {"conclusion": "failure"},
            "conclusion_cancelled": {"conclusion": "cancelled"},
            "conclusion_skipped": {"conclusion": "skipped"},
            "conclusion_timed_out": {"conclusion": "timed_out"},
            "conclusion_action_required": {"conclusion": "action_required"},
            "conclusion_neutral": {"conclusion": "neutral"},
            "conclusion_null": {"conclusion": None},
            "wrong_repository": {
                "repository": {"full_name": "someone-else/untrusted"}
            },
            "wrong_sha": {"head_sha": "b" * 40},
            "wrong_branch": {"head_branch": "release"},
            "wrong_event": {"event": "pull_request"},
            "wrong_workflow": {"path": ".github/workflows/ci.yml"},
            "wrong_run_id": {"id": int(RUN_ID) + 1},
            "wrong_run_attempt": {"run_attempt": RUN_ATTEMPT + 1},
        }
        for label, changes in rejected_attempts.items():
            rejected = {**payload, **changes}
            with self.subTest(case=label), self.assertRaisesRegex(
                ValueError, "exact successful"
            ):
                validate_workflow_run(
                    rejected,
                    repository="zsrt001/webdev-inspiration-hub",
                    run_id=RUN_ID,
                    run_attempt=RUN_ATTEMPT,
                    source_sha=SOURCE_SHA,
                    workflow_path=workflow_path,
                )
