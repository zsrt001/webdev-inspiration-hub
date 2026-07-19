"""The cleanup runner seals only a genuine protected read-after-delete result."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from scripts.release._acceptance_evidence import canonical
from scripts.release.run_account_cleanup_verification import run


ROOT = Path(__file__).resolve().parents[2]
TMP = ROOT / ".tmp" / "account-cleanup-verification"
KEY = b"account-cleanup-verification-key-32-bytes"
BINDING = {
    "source_sha": "a" * 40,
    "runtime_bundle_id": "rtb_" + "b" * 64,
    "deployment_id": "dpl-cleanup",
    "manifest_sha256": "c" * 64,
    "user_subject_hmac_sha256": "d" * 64,
}


def _commercial() -> dict:
    report = {
        "schema": "vowpic.linked-commercial-acceptance.v1",
        "phase": "commercial-before-delete",
        "passed": True,
        **BINDING,
        "links": {"user_id": "00000000-0000-4000-8000-000000000001"},
    }
    signature = hmac.new(
        KEY,
        canonical(report).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {**report, "signature": f"hmac-sha256:{signature}"}


class AccountCleanupVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True)
        self.commercial_path = TMP / "commercial.json"
        self.commercial_path.write_text(
            canonical(_commercial()) + "\n",
            encoding="utf-8",
        )
        os.chmod(self.commercial_path, 0o600)

    def tearDown(self) -> None:
        shutil.rmtree(TMP, ignore_errors=True)

    def test_retries_cleanup_and_seals_exact_absence_response(self) -> None:
        calls = [
            (200, {"success": True}),
            (409, {"detail": {"code": "acceptance_media_absence_not_proven"}}),
            (200, {"success": True}),
            (
                200,
                {
                    "schema": "vowpic.acceptance-media-absence.v1",
                    "passed": True,
                    **BINDING,
                    "verified_asset_count": 3,
                    "storage_read_outcome": "NOT_FOUND",
                    "facts_sha256": "e" * 64,
                },
            ),
        ]
        with (
            patch.dict(os.environ, {"RUNNER_TEMP": str(TMP)}),
            patch(
                "scripts.release.run_account_cleanup_verification._post_json",
                side_effect=calls,
            ),
            patch("scripts.release.run_account_cleanup_verification.time.sleep"),
        ):
            report = run(
                base_url="https://staged.example",
                commercial_report_path=self.commercial_path,
                output_path=TMP / "absence.json",
                cron_token="x" * 32,
                key=KEY,
            )
        self.assertEqual(report["verified_asset_count"], 3)
        self.assertEqual(report["cleanup_iterations"], 2)
        self.assertEqual(report["storage_read_outcome"], "NOT_FOUND")

    def test_rejects_success_response_with_wrong_release_binding(self) -> None:
        response = {
            "schema": "vowpic.acceptance-media-absence.v1",
            "passed": True,
            **BINDING,
            "deployment_id": "dpl-other",
            "verified_asset_count": 1,
            "storage_read_outcome": "NOT_FOUND",
            "facts_sha256": "e" * 64,
        }
        with (
            patch.dict(os.environ, {"RUNNER_TEMP": str(TMP)}),
            patch(
                "scripts.release.run_account_cleanup_verification._post_json",
                side_effect=[(200, {"success": True}), (200, response)],
            ),
        ):
            with self.assertRaisesRegex(ValueError, "response is invalid"):
                run(
                    base_url="https://staged.example",
                    commercial_report_path=self.commercial_path,
                    output_path=TMP / "absence.json",
                    cron_token="x" * 32,
                    key=KEY,
                )


if __name__ == "__main__":
    unittest.main()
