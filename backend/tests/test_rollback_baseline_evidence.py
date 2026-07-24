"""Signed rollback-baseline and observation recovery coordinate tests."""

from __future__ import annotations

from argparse import Namespace
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.scripts.complete_observation_recovery import _validate_inputs
from backend.scripts.rollback_baseline_evidence import (
    _verify_signed_runtime,
)
from scripts.release.collect_runtime_report import canonical_json_bytes
from scripts.release import observe_release


TARGET_SOURCE = "a" * 40
BASELINE_SOURCE = "b" * 40
TARGET_RUNTIME = "rtb_" + "c" * 64
BASELINE_RUNTIME = "rtb_" + "d" * 64
TARGET_DEPLOYMENT = "dpl_target123"
BASELINE_DEPLOYMENT = "dpl_baseline123"
SIGNING_KEY = b"k" * 32


def _signed_baseline_runtime() -> dict[str, object]:
    unsigned: dict[str, object] = {
        "schema": "vowpic.api-runtime-coordinate-report.v1",
        "passed": True,
        "source_sha": BASELINE_SOURCE,
        "runtime_bundle_id": BASELINE_RUNTIME,
        "api_deployment_id": BASELINE_DEPLOYMENT,
        "schema_revision": "20260710_0020",
        "release_role": "SAFE_BASELINE",
        "runtime_environment": "production",
        "backend_execution_version": "vowpic-backend-executor.v1",
        "backend_executor_digest": "e" * 64,
        "liveness_response_sha256": "f" * 64,
        "readiness_response_sha256": "1" * 64,
        "version_response_sha256": "2" * 64,
        "observed_at": "2026-07-23T12:00:00+00:00",
    }
    signature = hmac.new(
        SIGNING_KEY,
        canonical_json_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return {**unsigned, "signature": f"hmac-sha256:{signature}"}


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


class RollbackBaselineEvidenceTest(unittest.TestCase):
    def test_old_coordinate_mixing_recovery_command_is_not_executable(self) -> None:
        parser = observe_release._parser()
        self.assertNotIn("complete-recovery", parser._subparsers._group_actions[0].choices)

    def test_signed_baseline_runtime_is_not_the_target_runtime(self) -> None:
        report = _verify_signed_runtime(
            _signed_baseline_runtime(),
            signing_key=SIGNING_KEY,
        )
        self.assertEqual(report["release_role"], "SAFE_BASELINE")
        self.assertNotEqual(report["source_sha"], TARGET_SOURCE)
        self.assertNotEqual(report["runtime_bundle_id"], TARGET_RUNTIME)
        self.assertNotEqual(report["api_deployment_id"], TARGET_DEPLOYMENT)

    def test_signature_or_runtime_identity_drift_is_rejected(self) -> None:
        report = _signed_baseline_runtime()
        report["source_sha"] = TARGET_SOURCE
        with self.assertRaisesRegex(ValueError, "signature"):
            _verify_signed_runtime(report, signing_key=SIGNING_KEY)
        report = _signed_baseline_runtime()
        report["release_role"] = "COMMERCIAL_7A"
        with self.assertRaisesRegex(ValueError, "identity"):
            _verify_signed_runtime(report, signing_key=SIGNING_KEY)

    def test_observation_recovery_validates_target_off_and_baseline_on(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolution = {
                "schema": "vowpic.observation-recovery-resolution.v1",
                "passed": True,
                "release_role": "COMMERCIAL_7A",
                "source_sha": TARGET_SOURCE,
                "runtime_bundle_id": TARGET_RUNTIME,
                "api_deployment_id": TARGET_DEPLOYMENT,
                "private_compatible_baseline_deployment_id": BASELINE_DEPLOYMENT,
                "private_compatible_baseline_url": "https://baseline.vercel.app",
            }
            baseline = {
                "schema": "vowpic.rollback-baseline-resolution.v1",
                "passed": True,
                "target_source_sha": TARGET_SOURCE,
                "target_runtime_bundle_id": TARGET_RUNTIME,
                "target_deployment_id": TARGET_DEPLOYMENT,
                "baseline_deployment_url": "https://baseline.vercel.app",
                "baseline_deployment_id": BASELINE_DEPLOYMENT,
                "baseline_source_sha": BASELINE_SOURCE,
                "baseline_runtime_bundle_id": BASELINE_RUNTIME,
                "baseline_schema_revision": "20260710_0020",
                "baseline_release_role": "SAFE_BASELINE",
                "baseline_report_sha256": "3" * 64,
            }
            off_report = {
                "schema": "vowpic.activation-plan-report.v1",
                "passed": True,
                "phase": "emergency-off",
                "source_sha": TARGET_SOURCE,
                "runtime_bundle_id": TARGET_RUNTIME,
                "deployment_id": TARGET_DEPLOYMENT,
                "target_states": {"generation": "OFF", "billing": "OFF"},
            }
            paths = {
                "resolution_report": root / "resolution.json",
                "baseline_resolution": root / "baseline.json",
                "off_report": root / "off.json",
                "api_report": root / "api.json",
            }
            _write(paths["resolution_report"], resolution)
            _write(paths["baseline_resolution"], baseline)
            _write(paths["off_report"], off_report)
            _write(paths["api_report"], _signed_baseline_runtime())
            args = Namespace(
                **{name: str(path) for name, path in paths.items()},
                disposition="ROLLED_BACK_PRIVATE_BASELINE",
                api_signing_key_env="ACCEPTANCE_EVIDENCE_SIGNING_KEY",
            )
            with patch.dict(
                os.environ,
                {"ACCEPTANCE_EVIDENCE_SIGNING_KEY": SIGNING_KEY.decode()},
                clear=False,
            ):
                validated = _validate_inputs(args)
            self.assertEqual(validated[0]["source_sha"], TARGET_SOURCE)
            self.assertEqual(
                validated[2]["baseline_source_sha"],
                BASELINE_SOURCE,
            )
            self.assertEqual(validated[6]["api_deployment_id"], BASELINE_DEPLOYMENT)

            baseline["baseline_deployment_id"] = TARGET_DEPLOYMENT
            _write(paths["baseline_resolution"], baseline)
            with patch.dict(
                os.environ,
                {"ACCEPTANCE_EVIDENCE_SIGNING_KEY": SIGNING_KEY.decode()},
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "resolution"):
                    _validate_inputs(args)
