"""Exact workflow-attempt Preview coordinate selection."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import unittest

from backend.scripts.resolve_exact_preview_coordinates import (
    resolve_exact_records,
    select_exact_activation,
    validate_expected_binding,
)


class ExactPreviewCoordinatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.expected = validate_expected_binding(
            source_sha="a" * 40,
            workflow_run_id="123456789",
            workflow_attempt=2,
            activation_id="00000000-0000-4000-8000-000000000071",
            runtime_bundle_id="rtb_" + "b" * 64,
            api_deployment_id="dpl_preview_exact",
            manifest_sha256="c" * 64,
        )

    def _row(self, *, activation_id: str, run_id: str, attempt: int) -> dict:
        report = {
            "report_version": "vowpic.preview-commercial-activation.v1",
            "passed": True,
            "activation_id": activation_id,
            "environment": "preview",
            "kind": "PREVIEW_COMMERCIAL",
            "source_sha": self.expected["source_sha"],
            "runtime_bundle_id": self.expected["runtime_bundle_id"],
            "api_role": "PREVIEW_COMMERCIAL_API",
            "workflow_run_id": run_id,
            "workflow_attempt": attempt,
            "schema_revision": "20260710_0020",
            "api_deployment_id": self.expected["api_deployment_id"],
            "api_deployment_url": "https://preview-exact.vercel.app",
            "manifest_sha256": self.expected["manifest_sha256"],
            "worker_deployment_id": None,
            "worker_role": None,
            "worker_image_digest": None,
            "phase": "COMPLETED",
            "created_at": self.now.isoformat(),
        }
        raw = (
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
            + b"\n"
        )
        return {
            "id": activation_id,
            "environment": "preview",
            "kind": "PREVIEW_COMMERCIAL",
            "source_sha": self.expected["source_sha"],
            "runtime_bundle_id": self.expected["runtime_bundle_id"],
            "manifest_sha256": self.expected["manifest_sha256"],
            "report_sha256": hashlib.sha256(raw).hexdigest(),
            "api_deployment_id": self.expected["api_deployment_id"],
            "api_deployment_url": "https://preview-exact.vercel.app",
            "api_role": "PREVIEW_COMMERCIAL_API",
            "worker_deployment_id": None,
            "worker_role": None,
            "worker_image_digest": None,
            "phase": "CLEANED",
            "private_evidence_prefix": "github-artifact:v1:placeholder",
            "workflow_run_id": run_id,
            "workflow_attempt": attempt,
            "updated_at": self.now.isoformat(),
            "_report": {**report, "_content_sha256": hashlib.sha256(raw).hexdigest()},
        }

    def test_two_same_sha_runs_select_only_the_requested_attempt(self) -> None:
        exact = self._row(
            activation_id=self.expected["activation_id"],
            run_id=self.expected["workflow_run_id"],
            attempt=self.expected["workflow_attempt"],
        )
        other = self._row(
            activation_id="00000000-0000-4000-8000-000000000072",
            run_id="123456790",
            attempt=1,
        )
        selected = select_exact_activation([other, exact], expected=self.expected)
        self.assertEqual(str(selected["id"]), self.expected["activation_id"])
        resolved = resolve_exact_records(
            [other, exact],
            exact["_report"],
            expected=self.expected,
        )
        self.assertEqual(resolved["workflow_run_id"], self.expected["workflow_run_id"])
        self.assertEqual(
            resolved["workflow_attempt"], self.expected["workflow_attempt"]
        )

    def test_package_coordinate_drift_fails_closed(self) -> None:
        exact = self._row(
            activation_id=self.expected["activation_id"],
            run_id=self.expected["workflow_run_id"],
            attempt=self.expected["workflow_attempt"],
        )
        drifted = {**exact, "manifest_sha256": "d" * 64}
        with self.assertRaisesRegex(ValueError, "differs from the verified package"):
            select_exact_activation([drifted], expected=self.expected)

    def test_duplicate_exact_attempt_is_ambiguous(self) -> None:
        exact = self._row(
            activation_id=self.expected["activation_id"],
            run_id=self.expected["workflow_run_id"],
            attempt=self.expected["workflow_attempt"],
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            select_exact_activation([exact, dict(exact)], expected=self.expected)


if __name__ == "__main__":
    unittest.main()
