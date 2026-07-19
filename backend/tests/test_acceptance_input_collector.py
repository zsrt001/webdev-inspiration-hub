"""Cross-runtime truth tests for read-only COMMERCIAL_7A input collection."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release._acceptance_evidence import seal_collected_input
from scripts.release._acceptance_phase_facts import (
    AUTH_ASSERTIONS,
    collect_commercial_before_delete,
)


TMP = ROOT / ".tmp" / "acceptance-input-collector"
NODE = shutil.which("node") or "node"
KEY = b"acceptance-collector-test-key-at-least-32-bytes"


class _Cursor:
    def __init__(self, rows: dict[str, dict]):
        self.rows = rows
        self.current: list[dict] = []

    def execute(self, query: str, _params: tuple) -> None:
        if "JOIN qa_verdicts" in query:
            name = "trial"
        elif "JOIN order_entitlements" in query:
            name = "paid"
        elif "FROM payment_refund_facts" in query:
            name = "refund"
        elif "JOIN payment_capture_facts" in query:
            name = "purchase"
        elif "FROM credit_purchases p" in query:
            name = "second"
        elif "AND order_id = %s" in query:
            name = "preview"
        elif "FROM media_assets" in query:
            name = "upload"
        else:
            raise AssertionError(f"unrecognized collector query: {query}")
        self.current = [self.rows[name]]

    def fetchall(self) -> list[dict]:
        return self.current


def _browser() -> dict:
    return {
        "source_sha": "a" * 40,
        "runtime_bundle_id": "rtb_" + "b" * 64,
        "deployment_id": "dpl-commercial",
        "manifest_sha256": "c" * 64,
        "user_subject_hmac_sha256": "d" * 64,
        "currency": "USD",
        "cost_cap_minor_units": 500,
        "observations": {
            "private_upload_response": True,
            "watermarked_preview_response": True,
            "private_final_download_response": True,
            "account_export_response": True,
        },
        "links": {
            "user_id": "user-01",
            "upload_asset_id": "upload-01",
            "trial_order_id": "trial-order-01",
            "trial_preview_asset_id": "trial-preview-01",
            "purchase_id": "purchase-01",
            "paid_order_id": "paid-order-01",
            "paid_final_asset_id": "paid-final-01",
            "second_purchase_id": "purchase-02",
            "account_export_id": "export-01",
        },
    }


def _auth() -> dict:
    return {
        **{name: value for name, value in _browser().items() if name not in {"observations", "links", "currency", "cost_cap_minor_units"}},
        "assertions": {name: True for name in AUTH_ASSERTIONS},
        "links": {"user_id": "user-01"},
    }


def _rows() -> dict[str, dict]:
    return {
        "upload": {
            "id": "upload-01",
            "owner_user_id": "user-01",
            "role": "source",
            "status": "ACTIVE",
            "access_level": "private",
        },
        "trial": {
            "order_id": "trial-order-01",
            "user_id": "user-01",
            "order_status": "READY",
            "reservation_id": "trial-reservation-01",
            "job_id": "trial-job-01",
            "reservation_status": "CAPTURED",
            "job_status": "FINISHED",
            "attempt_id": "trial-attempt-01",
            "candidate_asset_id": "trial-candidate-01",
            "submission_accounting_state": "CAPTURED",
            "qa_verdict_id": "trial-qa-01",
            "qa_decision": "PASS",
        },
        "preview": {
            "id": "trial-preview-01",
            "owner_user_id": "user-01",
            "order_id": "trial-order-01",
            "job_id": "trial-job-01",
            "role": "preview_watermarked",
            "status": "ACTIVE",
            "access_level": "private",
        },
        "purchase": {
            "purchase_id": "purchase-01",
            "user_id": "user-01",
            "provider_checkout_id": "checkout-01",
            "purchase_status": "refunded",
            "intent_state": "CONFIRMED",
            "currency": "USD",
            "captured_minor_units": 200,
            "refunded_minor_units": 200,
            "credits": 10,
            "grant_lot_id": "grant-01",
            "grant_transaction_id": "grant-tx-01",
            "payment_event_id": "payment-event-01",
            "processing_state": "APPLIED",
            "raw_payload_sha256": "e" * 64,
            "original_amount": 10,
            "debt_offset_amount": 0,
            "consumed_amount": 10,
        },
        "paid": {
            "order_id": "paid-order-01",
            "order_status": "READY",
            "reservation_id": "paid-reservation-01",
            "job_id": "paid-job-01",
            "reservation_status": "CAPTURED",
            "job_status": "FINISHED",
            "attempt_id": "paid-attempt-01",
            "submission_accounting_state": "CAPTURED",
            "entitlement_id": "entitlement-01",
            "entitlement_status": "ACTIVE",
            "unlock_grant_lot_id": None,
            "unlock_root_transaction_id": None,
            "funding_grant_lot_id": "grant-01",
            "final_asset_id": "paid-final-01",
            "final_role": "final_master",
            "final_status": "ACTIVE",
            "access_level": "private",
        },
        "refund": {
            "refund_id": "refund-01",
            "purchase_id": "purchase-01",
            "payment_event_id": "refund-event-01",
            "refund_minor_units": 200,
            "currency": "USD",
            "classification": "FULL",
            "reversal_transaction_id": "reversal-01",
            "reversal_balance_after": -10,
            "purchase_status": "refunded",
            "refunded_minor_units": 200,
            "captured_minor_units": 200,
        },
        "second": {
            "purchase_id": "purchase-02",
            "purchase_status": "paid",
            "intent_state": "CONFIRMED",
            "currency": "USD",
            "captured_minor_units": 300,
            "credits": 15,
            "grant_lot_id": "grant-02",
            "grant_transaction_id": "grant-tx-02",
            "debt_offset_amount": 10,
            "original_amount": 15,
            "balance_after": 5,
            "balance": 5,
            "reserved_balance": 0,
        },
    }


class AcceptanceInputCollectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        if TMP.parent.exists() and not any(TMP.parent.iterdir()):
            TMP.parent.rmdir()

    def _run_validator(self, payload: dict, output_name: str) -> subprocess.CompletedProcess[str]:
        input_path = TMP / f"{output_name}.input.json"
        output_path = TMP / f"{output_name}.json"
        input_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        input_path.chmod(0o600)
        return subprocess.run(
            [
                NODE,
                str(ROOT / "scripts/release/run_linked_commercial_acceptance.mjs"),
                "--phase",
                "commercial-before-delete",
                "--base-url",
                "https://staged.example",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "RUNNER_TEMP": str(TMP),
                "ACCEPTANCE_EVIDENCE_SIGNING_KEY": KEY.decode("utf-8"),
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def test_read_only_facts_seal_a_payload_that_node_accepts(self) -> None:
        payload, facts = collect_commercial_before_delete(
            _Cursor(_rows()),
            browser=_browser(),
            auth=_auth(),
        )
        sealed = seal_collected_input(
            payload,
            phase="commercial-before-delete",
            browser_report_sha256="f" * 64,
            database_facts=facts,
            key=KEY,
        )
        completed = self._run_validator(sealed, "pass")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads((TMP / "pass.json").read_text(encoding="utf-8"))
        self.assertTrue(report["assertions"]["debt_offset_exact"])
        self.assertEqual(report["links"]["trial_preview_asset_id"], "trial-preview-01")

    def test_any_post_collection_mutation_breaks_the_proof(self) -> None:
        payload, facts = collect_commercial_before_delete(
            _Cursor(_rows()),
            browser=_browser(),
            auth=_auth(),
        )
        sealed = seal_collected_input(
            payload,
            phase="commercial-before-delete",
            browser_report_sha256="f" * 64,
            database_facts=facts,
            key=KEY,
        )
        sealed["links"]["paid_order_id"] = "unrelated-order"
        completed = self._run_validator(sealed, "tampered")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("input hash mismatch", completed.stderr)
        self.assertFalse((TMP / "tampered.json").exists())

    def test_wrong_debt_offset_is_not_collectable(self) -> None:
        rows = _rows()
        rows["second"]["debt_offset_amount"] = 9
        with self.assertRaisesRegex(ValueError, "debt offset"):
            collect_commercial_before_delete(
                _Cursor(rows),
                browser=_browser(),
                auth=_auth(),
            )

    def test_checkout_coordinate_must_come_from_the_database(self) -> None:
        rows = _rows()
        rows["purchase"]["provider_checkout_id"] = None
        with self.assertRaisesRegex(ValueError, "signed captured fact"):
            collect_commercial_before_delete(
                _Cursor(rows),
                browser=_browser(),
                auth=_auth(),
            )


if __name__ == "__main__":
    unittest.main()
