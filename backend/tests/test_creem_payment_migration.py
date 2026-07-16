"""Creem payment fact migration contract."""

from __future__ import annotations

from pathlib import Path
import unittest

from app.models.credit_purchase import CreditPurchase
from app.models.payment_event import (
    PaymentCaptureFact,
    PaymentDisputeFact,
    PaymentEvent,
    PaymentRefundFact,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/alembic/versions/20260710_0017_creem_payment_facts.py"


class CreemPaymentMigrationTest(unittest.TestCase):
    def test_revision_and_append_only_fact_tables_are_exact(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        self.assertIn('revision = "20260710_0017"', source)
        self.assertIn('down_revision = "20260710_0016"', source)
        for table in (
            "payment_capture_facts",
            "payment_refund_facts",
            "payment_dispute_facts",
        ):
            self.assertIn(f'"{table}"', source)
        self.assertIn("commercial_append_only_guard", source)
        self.assertIn("ENABLE ROW LEVEL SECURITY", source)

    def test_purchase_and_event_models_expose_independent_money_facts(self) -> None:
        for column in (
            "intent_state",
            "request_hash",
            "catalog_snapshot",
            "internal_metadata_id",
            "stored_response",
            "captured_minor_units",
            "tax_minor_units",
            "refunded_minor_units",
            "disputed_minor_units",
            "grant_transaction_id",
            "grant_lot_id",
        ):
            self.assertIn(column, CreditPurchase.__table__.columns)
        for column in (
            "raw_payload_sha256",
            "occurred_at",
            "request_id",
            "pre_tax_minor_units",
            "tax_minor_units",
            "currency",
            "normalized_status",
            "business_metadata",
            "processing_state",
        ):
            self.assertIn(column, PaymentEvent.__table__.columns)
        self.assertEqual(PaymentCaptureFact.__tablename__, "payment_capture_facts")
        self.assertEqual(PaymentRefundFact.__tablename__, "payment_refund_facts")
        self.assertEqual(PaymentDisputeFact.__tablename__, "payment_dispute_facts")


if __name__ == "__main__":
    unittest.main()
