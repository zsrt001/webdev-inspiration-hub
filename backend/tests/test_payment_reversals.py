"""Refund and dispute accounting classification tests."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus, PurchaseIntentState
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.payment_event import (
    PaymentCaptureFact,
    PaymentDisputeFact,
    PaymentEvent,
    PaymentEventProcessingState,
    PaymentRefundFact,
)
from app.services.payment_reconciliation_service import (
    PaymentReconciliationRequired,
    classify_monetary_reversal,
    freeze_unspent_purchase_lineage,
    unfreeze_purchase_lineage,
)
from app.services.payment_service import PaymentService


class PaymentReversalTest(unittest.TestCase):
    def test_only_exact_full_refund_can_reverse_the_credit_root(self) -> None:
        self.assertEqual(
            classify_monetary_reversal(
                captured_minor_units=1290,
                already_refunded_minor_units=0,
                event_minor_units=1290,
            ),
            "FULL",
        )
        with self.assertRaises(PaymentReconciliationRequired):
            classify_monetary_reversal(
                captured_minor_units=1290,
                already_refunded_minor_units=0,
                event_minor_units=500,
            )

    def test_over_refund_is_rejected_instead_of_clamped(self) -> None:
        with self.assertRaises(PaymentReconciliationRequired):
            classify_monetary_reversal(
                captured_minor_units=1290,
                already_refunded_minor_units=1000,
                event_minor_units=500,
            )

    def test_freeze_and_unfreeze_only_changes_unspent_lineage(self) -> None:
        lot = CreditGrantLot(
            user_id=uuid.uuid4(),
            root_transaction_id=uuid.uuid4(),
            source_type=GrantLotSourceType.PURCHASE,
            source_id="purchase-1",
            original_amount=50,
            debt_offset_amount=10,
            consumed_amount=15,
            reversed_amount=5,
            frozen_amount=0,
            retention_tier="paid_90d",
        )
        self.assertEqual(freeze_unspent_purchase_lineage(lot), 20)
        self.assertEqual(lot.spendable_amount, 0)
        self.assertEqual(unfreeze_purchase_lineage(lot), 20)
        self.assertEqual(lot.spendable_amount, 20)


class _ReversalDb:
    def __init__(self, purchase, capture):
        self.purchase = purchase
        self.capture = capture
        self.added = []

    async def scalar(self, statement):
        sql = str(statement)
        if "payment_capture_facts" in sql:
            return self.capture
        if "credit_purchases" in sql:
            return self.purchase
        if "payment_refund_facts" in sql or "payment_dispute_facts" in sql:
            return None
        return None

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


def _paid_purchase() -> tuple[CreditPurchase, PaymentCaptureFact]:
    purchase = CreditPurchase(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        provider="creem",
        package_id="pack_50",
        credits=50,
        price_cents=1290,
        currency="USD",
        status=CreditPurchaseStatus.PAID,
        provider_request_id="cp_1",
        intent_state=PurchaseIntentState.CONFIRMED,
        request_hash="a" * 64,
        captured_minor_units=1393,
        tax_minor_units=103,
        refunded_minor_units=0,
        disputed_minor_units=0,
        dispute_state="NONE",
        grant_transaction_id=uuid.uuid4(),
        grant_lot_id=uuid.uuid4(),
        confirmed_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )
    capture = PaymentCaptureFact(
        id=uuid.uuid4(),
        purchase_id=purchase.id,
        payment_event_id=uuid.uuid4(),
        provider="creem",
        provider_payment_id="tran_1",
        pre_tax_minor_units=1290,
        tax_minor_units=103,
        currency="USD",
        grant_transaction_id=purchase.grant_transaction_id,
        grant_lot_id=purchase.grant_lot_id,
        occurred_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )
    return purchase, capture


def _money_event(*, event_type: str, object_id: str, amount: int, status: str) -> PaymentEvent:
    key = "provider_refund_id" if event_type == "refund.created" else "provider_dispute_id"
    return PaymentEvent(
        id=uuid.uuid4(),
        provider="creem",
        event_id=f"evt_{object_id}",
        event_type=event_type,
        object_id=object_id,
        raw_payload_sha256="c" * 64,
        occurred_at=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
        currency="USD",
        normalized_status=status,
        business_metadata={
            key: object_id,
            "provider_payment_id": "tran_1",
            "event_minor_units": str(amount),
        },
        processing_state=PaymentEventProcessingState.RECEIVED,
    )


class PaymentReversalApplicationTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_refund_appends_purchase_reversal_and_fact(self) -> None:
        purchase, capture = _paid_purchase()
        db = _ReversalDb(purchase, capture)
        event = _money_event(
            event_type="refund.created",
            object_id="ref_1",
            amount=1393,
            status="succeeded",
        )
        reversal_transaction = CreditTransaction(
            id=uuid.uuid4(),
            user_id=purchase.user_id,
            transaction_type=CreditTransactionType.PURCHASE_REVERSAL,
            amount=-50,
            balance_after=0,
        )
        reversal = AsyncMock(
            return_value=SimpleNamespace(
                transaction=reversal_transaction,
                debt=0,
                replayed=False,
            )
        )
        with patch("app.services.payment_service.reverse_root_grant", new=reversal):
            await PaymentService()._apply_refund(db, event)

        reversal.assert_awaited_once()
        self.assertEqual(
            reversal.await_args.kwargs["transaction_type"],
            CreditTransactionType.PURCHASE_REVERSAL,
        )
        fact = next(item for item in db.added if isinstance(item, PaymentRefundFact))
        self.assertEqual(fact.classification, "FULL")
        self.assertEqual(fact.reversal_transaction_id, reversal_transaction.id)
        self.assertEqual(purchase.refunded_minor_units, 1393)
        self.assertEqual(purchase.status, CreditPurchaseStatus.REFUNDED)
        self.assertEqual(event.processing_state, PaymentEventProcessingState.APPLIED)

    async def test_partial_refund_is_fact_plus_freeze_and_open_case_not_ratio(self) -> None:
        purchase, capture = _paid_purchase()
        db = _ReversalDb(purchase, capture)
        event = _money_event(
            event_type="refund.created",
            object_id="ref_partial",
            amount=500,
            status="succeeded",
        )
        freeze = AsyncMock()
        with patch(
            "app.services.payment_service.freeze_purchase_and_open_case",
            new=freeze,
        ):
            await PaymentService()._apply_refund(db, event)

        fact = next(item for item in db.added if isinstance(item, PaymentRefundFact))
        self.assertEqual(fact.classification, "PARTIAL_RECONCILIATION_REQUIRED")
        self.assertIsNone(fact.reversal_transaction_id)
        self.assertEqual(purchase.refunded_minor_units, 500)
        freeze.assert_awaited_once()
        self.assertEqual(event.processing_state, PaymentEventProcessingState.RECONCILIATION_REQUIRED)

    async def test_dispute_open_freezes_and_lost_uses_dispute_reversal(self) -> None:
        purchase, capture = _paid_purchase()
        db = _ReversalDb(purchase, capture)
        open_event = _money_event(
            event_type="dispute.created",
            object_id="disp_1",
            amount=1393,
            status="open",
        )
        freeze = AsyncMock()
        with patch(
            "app.services.payment_service.freeze_purchase_and_open_case",
            new=freeze,
        ):
            await PaymentService()._apply_dispute(db, open_event)
        freeze.assert_awaited_once()
        self.assertEqual(purchase.dispute_state, "OPEN")
        self.assertTrue(any(isinstance(item, PaymentDisputeFact) for item in db.added))

        lost_event = _money_event(
            event_type="dispute.closed",
            object_id="disp_1",
            amount=1393,
            status="lost",
        )
        reversal_transaction = CreditTransaction(
            id=uuid.uuid4(),
            user_id=purchase.user_id,
            transaction_type=CreditTransactionType.DISPUTE_REVERSAL,
            amount=-50,
            balance_after=-10,
        )
        reversal = AsyncMock(
            return_value=SimpleNamespace(
                transaction=reversal_transaction,
                debt=10,
                replayed=False,
            )
        )
        with patch("app.services.payment_service.reverse_root_grant", new=reversal):
            await PaymentService()._apply_dispute(db, lost_event)
        self.assertEqual(
            reversal.await_args.kwargs["transaction_type"],
            CreditTransactionType.DISPUTE_REVERSAL,
        )
        self.assertEqual(purchase.dispute_state, "LOST")


if __name__ == "__main__":
    unittest.main()
