"""Subscription acceptance may pass only from linked browser and database facts."""

from __future__ import annotations

from datetime import datetime, timezone
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
from scripts.release._acceptance_phase_facts import AUTH_ASSERTIONS
from scripts.release._acceptance_subscription_facts import collect_subscription


TMP = ROOT / ".tmp" / "acceptance-subscription-collector"
NODE = shutil.which("node") or "node"
KEY = b"subscription-collector-test-key-at-least-32-bytes"
NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, rows: dict[str, object]):
        self.rows = rows
        self.current: list[dict] = []

    def execute(self, query: str, _params: tuple) -> None:
        if "FROM user_subscriptions s" in query:
            name = "subscription"
        elif "FROM subscription_invoices inv" in query:
            self.current = list(self.rows["invoices"])
            return
        elif "FROM orders o" in query:
            name = "order"
        elif "FROM subscription_cancel_intents" in query:
            name = "cancel"
        else:
            raise AssertionError(f"unrecognized subscription query: {query}")
        self.current = [dict(self.rows[name])]

    def fetchall(self) -> list[dict]:
        return self.current


def _binding() -> dict:
    return {
        "source_sha": "a" * 40,
        "runtime_bundle_id": "rtb_" + "b" * 64,
        "deployment_id": "dpl-subscription",
        "manifest_sha256": "c" * 64,
        "user_subject_hmac_sha256": "d" * 64,
    }


def _browser() -> dict:
    return {
        **_binding(),
        "currency": "USD",
        "cost_cap_minor_units": 700,
        "observations": {
            "starter_checkout_response": True,
            "cancel_response": True,
            "active_until_period_end_response": True,
            "initial_order_response": True,
        },
        "links": {
            "user_id": "user-subscription",
            "subscription_id": "subscription-01",
            "initial_order_id": "subscription-order-01",
        },
    }


def _auth() -> dict:
    return {
        **_binding(),
        "assertions": {name: True for name in AUTH_ASSERTIONS},
        "links": {"user_id": "user-subscription"},
    }


def _invoice(
    *,
    invoice_id: str,
    transaction_id: str,
    payment_event_id: str,
    grant_id: str,
    lot_id: str,
    start_day: int,
    refunded: int = 0,
    reversed_amount: int = 0,
) -> dict:
    start = datetime(2026, start_day, 1, tzinfo=timezone.utc)
    end = datetime(2026, start_day + 1, 1, tzinfo=timezone.utc)
    return {
        "invoice_id": invoice_id,
        "provider_transaction_id": transaction_id,
        "payment_event_id": payment_event_id,
        "period_start": start,
        "period_end": end,
        "pre_tax_minor_units": 300,
        "tax_minor_units": 0,
        "currency": "USD",
        "refunded_minor_units": refunded,
        "disputed_minor_units": 0,
        "dispute_state": "NONE",
        "credit_grant_id": grant_id,
        "grant_lot_id": lot_id,
        "credit_transaction_id": f"{grant_id}-tx",
        "retention_tier": "subscription_180d",
        "original_amount": 20,
        "debt_offset_amount": 0,
        "reversed_amount": reversed_amount,
        "processing_state": "APPLIED",
    }


def _rows() -> dict[str, object]:
    return {
        "subscription": {
            "subscription_id": "subscription-01",
            "user_id": "user-subscription",
            "normalized_status": "CANCEL_REQUESTED",
            "cancel_at_period_end": True,
            "current_period_start": datetime(2026, 2, 1, tzinfo=timezone.utc),
            "current_period_end": datetime(2026, 3, 1, tzinfo=timezone.utc),
            "paid_through_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
            "last_provider_transaction_id": "txn-initial",
            "checkout_intent_id": "checkout-intent-01",
            "checkout_state": "CONFIRMED",
        },
        "invoices": [
            _invoice(
                invoice_id="invoice-initial",
                transaction_id="txn-initial",
                payment_event_id="event-initial",
                grant_id="grant-initial",
                lot_id="lot-initial",
                start_day=1,
            ),
        ],
        "order": {
            "order_id": "subscription-order-01",
            "order_status": "READY",
            "expires_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "reservation_id": "subscription-reservation-01",
            "reservation_status": "CAPTURED",
            "captured_retention_tier": "subscription_180d",
            "grant_lot_id": "lot-initial",
            "entitlement_status": "ACTIVE",
        },
        "cancel": {
            "id": "cancel-intent-01",
            "state": "CONFIRMED",
            "confirmed_at": NOW,
        },
    }


class AcceptanceSubscriptionCollectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        if TMP.parent.exists() and not any(TMP.parent.iterdir()):
            TMP.parent.rmdir()

    def test_verified_provider_and_database_facts_cross_validate_in_node(self) -> None:
        payload, facts = collect_subscription(
            _Cursor(_rows()),
            browser=_browser(),
            auth=_auth(),
        )
        sealed = seal_collected_input(
            payload,
            phase="subscription",
            browser_report_sha256="f" * 64,
            database_facts=facts,
            key=KEY,
        )
        input_path = TMP / "subscription.input.json"
        output_path = TMP / "subscription.json"
        input_path.write_text(json.dumps(sealed, sort_keys=True), encoding="utf-8")
        input_path.chmod(0o600)
        completed = subprocess.run(
            [
                NODE,
                str(ROOT / "scripts/release/run_subscription_acceptance.mjs"),
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
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertTrue(report["assertions"]["period_end_cancel_confirmed"])
        self.assertEqual(report["links"]["cancel_event_id"], "cancel-intent-01")
        self.assertFalse(
            any(name.startswith("test_mode_") for name in report["assertions"])
        )

    def test_production_does_not_manufacture_renewal_or_chargeback_rows(self) -> None:
        rows = _rows()
        rows["invoices"].append(
            _invoice(
                invoice_id="invoice-renewal",
                transaction_id="txn-renewal",
                payment_event_id="event-renewal",
                grant_id="grant-renewal",
                lot_id="lot-renewal",
                start_day=2,
            )
        )
        with self.assertRaisesRegex(ValueError, "exactly one initial"):
            collect_subscription(
                _Cursor(rows),
                browser=_browser(),
                auth=_auth(),
            )


if __name__ == "__main__":
    unittest.main()
