"""Truthfulness contracts for linked ordinary-user COMMERCIAL_7A acceptance."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from scripts.release._acceptance_evidence import canonical


ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node") or "node"
TMP = ROOT / ".tmp" / "production-acceptance-contract"
SIGNING_KEY = "production-acceptance-test-signing-key-32bytes"


def _links(names: list[str], prefix: str) -> dict[str, str]:
    return {
        name: f"{prefix}-{index:02d}"
        for index, name in enumerate(names, start=1)
    }


def _seal_collected_input(payload: dict[str, object], phase: str) -> dict[str, object]:
    unsigned = dict(payload)
    proof = {
        "schema": "vowpic.acceptance-collector-proof.v1",
        "phase": phase,
        "source_sha": payload["source_sha"],
        "runtime_bundle_id": payload["runtime_bundle_id"],
        "deployment_id": payload["deployment_id"],
        "manifest_sha256": payload["manifest_sha256"],
        "browser_report_sha256": "e" * 64,
        "database_facts_sha256": "f" * 64,
        "collected_at": "2026-07-19T00:00:00Z",
        "input_sha256": hashlib.sha256(
            canonical(unsigned).encode("utf-8")
        ).hexdigest(),
    }
    signature = hmac.new(
        SIGNING_KEY.encode("utf-8"),
        canonical(proof).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        **payload,
        "collector": {**proof, "signature": f"hmac-sha256:{signature}"},
    }


class ProductionAcceptanceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        parent = TMP.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def _run(
        self,
        script: str,
        input_payload: dict[str, object],
        *arguments: str,
        output_name: str,
    ) -> subprocess.CompletedProcess[str]:
        input_path = TMP / f"{output_name}.input.json"
        output_path = TMP / f"{output_name}.json"
        input_path.write_text(
            json.dumps(input_payload, sort_keys=True),
            encoding="utf-8",
        )
        input_path.chmod(0o600)
        env = {
            **os.environ,
            "RUNNER_TEMP": str(TMP),
            "ACCEPTANCE_EVIDENCE_SIGNING_KEY": SIGNING_KEY,
        }
        return subprocess.run(
            [
                NODE,
                str(ROOT / script),
                *arguments,
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

    @staticmethod
    def _binding() -> dict[str, object]:
        return {
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "deployment_id": "dpl-commercial-7a",
            "manifest_sha256": "c" * 64,
            "user_subject_hmac_sha256": "d" * 64,
        }

    def _commercial_input(self) -> dict[str, object]:
        assertions = {
            name: True
            for name in (
                "ordinary_google_user",
                "first_login",
                "welcome_grant_once",
                "refresh_rotated",
                "logout_revoked",
                "post_logout_denied",
                "second_login_same_account",
                "legacy_jwt_denied",
                "legacy_openid_header_denied",
                "legacy_visitor_header_denied",
                "forwarded_identity_spoof_denied",
                "browser_admin_token_denied",
                "private_upload",
                "trial_job_ready",
                "trial_qa_passed",
                "watermarked_preview",
                "signed_checkout_webhook",
                "exact_order_entitlement",
                "private_final_download",
                "paid_grant_consumed",
                "full_refund_verified",
                "refund_reversal_and_debt",
                "second_purchase_verified",
                "debt_offset_exact",
                "residual_spendable_exact",
                "account_export_complete",
                "no_admin_or_test_bypass",
            )
        }
        link_names = [
            "user_id",
            "upload_asset_id",
            "trial_order_id",
            "trial_reservation_id",
            "trial_job_id",
            "trial_attempt_id",
            "trial_candidate_asset_id",
            "trial_preview_asset_id",
            "trial_qa_verdict_id",
            "purchase_id",
            "checkout_id",
            "payment_event_id",
            "credit_grant_id",
            "paid_order_id",
            "paid_reservation_id",
            "paid_job_id",
            "paid_attempt_id",
            "paid_final_asset_id",
            "entitlement_id",
            "refund_id",
            "reversal_id",
            "debt_fact_id",
            "second_purchase_id",
            "second_grant_id",
            "debt_offset_fact_id",
            "account_export_id",
        ]
        return _seal_collected_input({
            "schema": "vowpic.commercial-acceptance-input.v1",
            "phase": "commercial-before-delete",
            **self._binding(),
            "currency": "USD",
            "cost_minor_units": 299,
            "cost_cap_minor_units": 500,
            "assertions": assertions,
            "links": _links(link_names, "commercial"),
        }, "commercial-before-delete")

    def _auth_input(self) -> dict[str, object]:
        assertion_names = (
            "ordinary_google_user",
            "first_login",
            "refresh_rotated",
            "logout_revoked",
            "post_logout_denied",
            "second_login_same_account",
            "legacy_jwt_denied",
            "legacy_openid_header_denied",
            "legacy_visitor_header_denied",
            "forwarded_identity_spoof_denied",
            "browser_admin_token_denied",
            "no_admin_or_test_bypass",
        )
        return {
            "schema": "vowpic.commercial-acceptance-input.v1",
            "phase": "first-login-and-auth-security",
            **self._binding(),
            "currency": "USD",
            "cost_minor_units": 0,
            "cost_cap_minor_units": 1,
            "assertions": {name: True for name in assertion_names},
            "links": _links(
                ["user_id", "first_session_id", "rotated_session_id", "second_session_id"],
                "auth",
            ),
        }

    def test_first_login_auth_security_phase_is_real_and_fail_closed(self) -> None:
        completed = self._run(
            "scripts/release/run_linked_commercial_acceptance.mjs",
            self._auth_input(),
            "--phase",
            "first-login-and-auth-security",
            "--base-url",
            "https://staged.example",
            output_name="auth-pass",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads((TMP / "auth-pass.json").read_text(encoding="utf-8"))
        self.assertTrue(report["assertions"]["post_logout_denied"])
        self.assertTrue(report["assertions"]["browser_admin_token_denied"])

        payload = self._auth_input()
        payload["assertions"]["legacy_openid_header_denied"] = False
        completed = self._run(
            "scripts/release/run_linked_commercial_acceptance.mjs",
            payload,
            "--phase",
            "first-login-and-auth-security",
            "--base-url",
            "https://staged.example",
            output_name="auth-false",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((TMP / "auth-false.json").exists())

    def test_any_false_commercial_assertion_exits_nonzero(self) -> None:
        payload = self._commercial_input()
        payload["assertions"]["exact_order_entitlement"] = False
        completed = self._run(
            "scripts/release/run_linked_commercial_acceptance.mjs",
            payload,
            "--phase",
            "commercial-before-delete",
            "--base-url",
            "https://staged.example",
            output_name="commercial-false",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("exact_order_entitlement", completed.stderr)
        self.assertFalse((TMP / "commercial-false.json").exists())

    def test_linked_commercial_report_is_signed_and_contains_no_identity_secret(
        self,
    ) -> None:
        completed = self._run(
            "scripts/release/run_linked_commercial_acceptance.mjs",
            self._commercial_input(),
            "--phase",
            "commercial-before-delete",
            "--base-url",
            "https://staged.example",
            output_name="commercial-pass",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(
            (TMP / "commercial-pass.json").read_text(encoding="utf-8")
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["phase"], "commercial-before-delete")
        self.assertRegex(report["signature"], r"^hmac-sha256:[0-9a-f]{64}$")
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("@", serialized)
        self.assertNotIn("access_token", serialized)
        self.assertNotIn("permanent_url", serialized)

    def test_real_secret_field_and_non_origin_base_url_are_rejected(self) -> None:
        payload = self._commercial_input()
        payload["access_token"] = "must-not-be-recorded"
        completed = self._run(
            "scripts/release/run_linked_commercial_acceptance.mjs",
            payload,
            "--phase",
            "commercial-before-delete",
            "--base-url",
            "https://staged.example",
            output_name="commercial-secret",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((TMP / "commercial-secret.json").exists())

        completed = self._run(
            "scripts/release/run_linked_commercial_acceptance.mjs",
            self._commercial_input(),
            "--phase",
            "commercial-before-delete",
            "--base-url",
            "https://staged.example/not-an-origin",
            output_name="commercial-path",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("exact HTTPS origin", completed.stderr)
        self.assertFalse((TMP / "commercial-path.json").exists())

    def _subscription_input(self) -> dict[str, object]:
        assertion_names = (
            "ordinary_google_user",
            "starter_checkout",
            "signed_initial_paid_event",
            "one_initial_invoice",
            "one_initial_grant",
            "paid_order_snapshot_180_days",
            "signed_renewal_paid_event",
            "renewal_transaction_unique",
            "renewal_invoice_unique",
            "period_end_cancel_confirmed",
            "cancel_remains_active_until_period_end",
            "full_invoice_refund_verified",
            "refund_reversal_and_debt",
            "access_revoked_after_refund",
            "duplicate_event_deduped",
            "out_of_order_event_reconciled",
            "past_due_recovery_verified",
            "partial_refund_anomaly_quarantined",
            "dispute_test_outcome_verified",
            "no_real_chargeback_manufactured",
            "no_admin_or_test_bypass",
        )
        link_names = [
            "user_id",
            "subscription_id",
            "checkout_id",
            "initial_transaction_id",
            "initial_invoice_id",
            "initial_grant_id",
            "initial_order_id",
            "renewal_transaction_id",
            "renewal_invoice_id",
            "renewal_grant_id",
            "cancel_event_id",
            "refund_id",
            "reversal_id",
            "debt_fact_id",
            "access_snapshot_id",
        ]
        return _seal_collected_input({
            "schema": "vowpic.subscription-acceptance-input.v1",
            **self._binding(),
            "currency": "USD",
            "cost_minor_units": 499,
            "cost_cap_minor_units": 700,
            "assertions": {name: True for name in assertion_names},
            "links": _links(link_names, "subscription"),
        }, "subscription")

    def test_subscription_duplicate_or_false_fact_cannot_pass(self) -> None:
        payload = self._subscription_input()
        payload["assertions"]["renewal_invoice_unique"] = False
        completed = self._run(
            "scripts/release/run_subscription_acceptance.mjs",
            payload,
            "--base-url",
            "https://staged.example",
            output_name="subscription-false",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((TMP / "subscription-false.json").exists())

    def _quality_input(self) -> dict[str, object]:
        case_ids = (
            "single_template",
            "single_text",
            "single_outdoor_text",
            "local_couple",
            "golden_anniversary",
            "partner_invite_remote_couple",
        )
        return _seal_collected_input({
            "schema": "vowpic.quality-acceptance-input.v1",
            **self._binding(),
            "cases": [
                {
                    "id": case_id,
                    "order_id": f"order-{index}",
                    "job_id": f"job-{index}",
                    "status": "READY",
                    "initial_candidate_count": 1,
                    "repair_candidate_count": 1,
                    "selected_candidate_id": f"candidate-{index}",
                    "review_asset_id": f"final-master-{index}",
                    "reviewer_ref": f"reviewer-{index}",
                    "scores": {
                        "identity": 5,
                        "composition": 4,
                        "attire_style": 4,
                        "naturalness_exposure": 5,
                    },
                    "hard_defects": [],
                    "passed": True,
                }
                for index, case_id in enumerate(case_ids, start=1)
            ],
        }, "quality")

    def test_unsigned_non_auth_inputs_cannot_become_acceptance_evidence(self) -> None:
        payload = self._commercial_input()
        payload.pop("collector")
        completed = self._run(
            "scripts/release/run_linked_commercial_acceptance.mjs",
            payload,
            "--phase",
            "commercial-before-delete",
            "--base-url",
            "https://staged.example",
            output_name="commercial-uncollected",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((TMP / "commercial-uncollected.json").exists())

    def test_quality_requires_exact_six_cases_and_fixed_human_rubric(self) -> None:
        payload = self._quality_input()
        completed = self._run(
            "scripts/release/run_quality_acceptance.mjs",
            payload,
            "--base-url",
            "https://staged.example",
            "--cases",
            str(ROOT / "release/quality-cases.json"),
            "--rubric",
            str(ROOT / "release/quality-rubric.json"),
            output_name="quality-pass",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(
            (TMP / "quality-pass.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(report["cases"]), 6)
        self.assertEqual(
            {item["id"] for item in report["cases"]},
            {
                "single_template",
                "single_text",
                "single_outdoor_text",
                "local_couple",
                "golden_anniversary",
                "partner_invite_remote_couple",
            },
        )

        payload = self._quality_input()
        payload["cases"][0]["hard_defects"] = ["identity"]
        payload["cases"][0]["passed"] = False
        completed = self._run(
            "scripts/release/run_quality_acceptance.mjs",
            payload,
            "--base-url",
            "https://staged.example",
            "--cases",
            str(ROOT / "release/quality-cases.json"),
            "--rubric",
            str(ROOT / "release/quality-rubric.json"),
            output_name="quality-false",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((TMP / "quality-false.json").exists())

    def test_no_acceptance_runner_calls_admin_or_generation_probe(self) -> None:
        for path in (
            ROOT / "scripts/release/run_linked_commercial_acceptance.mjs",
            ROOT / "scripts/release/run_subscription_acceptance.mjs",
            ROOT / "scripts/release/run_quality_acceptance.mjs",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("/admin/", source)
            self.assertNotIn("generation_probe", source)
            self.assertNotIn("console.log('ok')", source)


if __name__ == "__main__":
    unittest.main()
