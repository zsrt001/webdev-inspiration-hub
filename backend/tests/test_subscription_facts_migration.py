"""Normalized subscription fact schema and migration contract."""

from __future__ import annotations

from pathlib import Path
import unittest

from app.models.subscription_cancel_intent import SubscriptionCancelIntent
from app.models.subscription_credit_grant import SubscriptionCreditGrant
from app.models.subscription_invoice import (
    SubscriptionInvoice,
    SubscriptionInvoiceAdjustmentFact,
)
from app.models.user_subscription import UserSubscription


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/alembic/versions/20260710_0018_subscription_facts.py"


class SubscriptionFactsMigrationTest(unittest.TestCase):
    def test_revision_tables_rls_and_immutability_are_declared(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        self.assertIn('revision = "20260710_0018"', source)
        self.assertIn('down_revision = "20260710_0017"', source)
        for table in (
            "subscription_invoices",
            "subscription_invoice_adjustment_facts",
            "subscription_cancel_intents",
        ):
            self.assertIn(f'"{table}"', source)
        self.assertIn("commercial_append_only_guard", source)
        self.assertIn("IF TG_OP = 'DELETE' THEN", source)
        self.assertIn("subscription invoice is append only", source)
        self.assertIn("ENABLE ROW LEVEL SECURITY", source)
        self.assertIn("uq_user_subscriptions_one_nonterminal", source)
        self.assertIn('"fk_subscription_grants_subscription"', source)
        self.assertIn('ondelete="RESTRICT"', source)

    def test_models_expose_transaction_period_and_root_lineage(self) -> None:
        for column in (
            "provider_transaction_id",
            "provider_invoice_id",
            "period_start",
            "period_end",
            "pre_tax_minor_units",
            "tax_minor_units",
            "currency",
            "catalog_snapshot",
            "credit_grant_id",
        ):
            self.assertIn(column, SubscriptionInvoice.__table__.columns)
        for column in (
            "invoice_id",
            "grant_lot_id",
            "period_start",
            "period_end",
        ):
            self.assertIn(column, SubscriptionCreditGrant.__table__.columns)
        for column in (
            "normalized_status",
            "paid_through_at",
            "last_provider_event_at",
            "last_provider_transaction_id",
            "catalog_snapshot",
        ):
            self.assertIn(column, UserSubscription.__table__.columns)
        self.assertEqual(SubscriptionCancelIntent.__tablename__, "subscription_cancel_intents")
        self.assertEqual(
            SubscriptionInvoiceAdjustmentFact.__tablename__,
            "subscription_invoice_adjustment_facts",
        )


if __name__ == "__main__":
    unittest.main()
