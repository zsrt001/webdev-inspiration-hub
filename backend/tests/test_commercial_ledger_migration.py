"""Commercial-ledger model and migration contract tests."""

from __future__ import annotations

from pathlib import Path
import unittest

from app.models.credit_grant_lot import CreditGrantLot
from app.models.credit_reservation import (
    CreditReservation,
    CreditReservationAllocation,
    ReservationStatus,
)
from app.models.idempotency_record import IdempotencyRecord
from app.models.order_entitlement import EntitlementStatus, OrderEntitlement
from app.models.order_entitlement_funding import OrderEntitlementFunding
from app.models.outbox_event import OutboxEvent
from app.models.payment_reconciliation_case import PaymentReconciliationCase
from app.models.user_credit import UserCredit
from app.models.welcome_grant_claim import WelcomeGrantClaim


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/alembic/versions/20260710_0016_commercial_ledger.py"
GENERATION_MIGRATION = ROOT / "backend/alembic/versions/20260710_0019_generation_jobs.py"


class CommercialLedgerMigrationTest(unittest.TestCase):
    def test_revision_follows_private_media_head_and_creates_required_tables(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        self.assertIn('revision = "20260710_0016"', source)
        self.assertIn('down_revision = "20260710_0015"', source)
        for table in (
            "billing_catalog_versions",
            "billing_products",
            "billing_provider_products",
            "credit_grant_lots",
            "credit_reservations",
            "credit_reservation_allocations",
            "order_entitlements",
            "order_entitlement_fundings",
            "welcome_grant_claims",
            "payment_reconciliation_cases",
            "idempotency_records",
            "outbox_events",
        ):
            self.assertIn(f'"{table}"', source)
        self.assertIn("commercial_append_only_guard", source)
        self.assertIn("commercial_reservation_transition_guard", source)
        self.assertIn("commercial_entitlement_transition_guard", source)
        self.assertIn("commercial_entitlement_funding_guard", source)
        self.assertIn("uq_credit_transactions_request_type", source)
        self.assertIn("ENABLE ROW LEVEL SECURITY", source)

    def test_required_lineage_columns_and_statuses_exist(self) -> None:
        self.assertIn("reserved_balance", UserCredit.__table__.columns)
        self.assertIn("debt_offset_amount", CreditGrantLot.__table__.columns)
        self.assertIn("provider_attempt_id", CreditReservation.__table__.columns)
        self.assertIn("funding_policy_snapshot", CreditReservation.__table__.columns)
        self.assertIn("funding_policy_hash", CreditReservation.__table__.columns)
        self.assertIn("captured_retention_tier", CreditReservation.__table__.columns)
        self.assertIn(
            "provider_attempt_id",
            {fk.parent.name for fk in CreditReservation.__table__.foreign_keys},
        )
        self.assertIn("grant_lot_id", CreditReservationAllocation.__table__.columns)
        self.assertIn("reservation_allocation_id", OrderEntitlementFunding.__table__.columns)
        self.assertIn("request_hash", IdempotencyRecord.__table__.columns)
        self.assertIn("dedupe_key", OutboxEvent.__table__.columns)
        self.assertIn("payload_version", OutboxEvent.__table__.columns)
        self.assertIn("user_identity_id", WelcomeGrantClaim.__table__.columns)
        self.assertIn("raw_payload_sha256", PaymentReconciliationCase.__table__.columns)
        self.assertEqual(ReservationStatus.RESERVED.value, "RESERVED")
        self.assertEqual(EntitlementStatus.ACTIVE.value, "ACTIVE")
        self.assertIn("status", OrderEntitlement.__table__.columns)
        self.assertIn("unlock_grant_lot_id", OrderEntitlement.__table__.columns)
        self.assertIn("unlock_root_transaction_id", OrderEntitlement.__table__.columns)
        source = GENERATION_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("unlock_grant_lot_id", source)
        self.assertIn("unlock_root_transaction_id", source)
        self.assertIn("uq_order_entitlements_unlock_grant_lot", source)
        self.assertIn("ix_order_entitlements_unlock_grant_lot_id", source)
        self.assertIn("ix_order_entitlements_unlock_root_transaction_id", source)
        self.assertIn(
            "OLD.unlock_grant_lot_id IS DISTINCT FROM NEW.unlock_grant_lot_id",
            source,
        )

    def test_seed_catalog_casts_bound_identifiers_to_postgresql_uuid(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")

        self.assertEqual(source.count("CAST(:id AS uuid)"), 2)
        self.assertIn("CAST(:catalog_id AS uuid)", source)


if __name__ == "__main__":
    unittest.main()
