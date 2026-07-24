"""Signed Creem webhook normalization, replay, and hash-conflict tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.credit_grant_lot import CreditGrantLot
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus, PurchaseIntentState
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.payment_event import PaymentCaptureFact, PaymentEvent, PaymentEventProcessingState
from app.models.payment_reconciliation_case import PaymentReconciliationCase
from app.models.user_credit import UserCredit
from app.services.creem_event_service import (
    CreemEventError,
    InvalidWebhookSignature,
    PaymentEventHashConflict,
    ingest_verified_creem_event,
    normalize_creem_event,
    parse_creem_raw_body,
    verify_creem_signature,
)
from app.schemas.payment import AcceptedPaymentEvent
from app.services.payment_service import PaymentService


SECRET = b"test-webhook-secret"


def _checkout_payload(*, event_id: str = "evt_checkout_1") -> dict:
    return {
        "id": event_id,
        "eventType": "checkout.completed",
        "created_at": 1783944000000,
        "object": {
            "id": "ch_1",
            "object": "checkout",
            "request_id": "cp_request_1",
            "status": "completed",
            "metadata": {"vowpic_purchase_ref": "00000000-0000-4000-8000-000000000001"},
            "order": {
                "id": "ord_1",
                "transaction": "tran_1",
                "customer": "cust_1",
                "product": "prod_pack_50",
                "sub_total": 1290,
                "tax_amount": 103,
                "amount_paid": 1393,
                "currency": "USD",
                "status": "paid",
            },
            "product": {
                "id": "prod_pack_50",
                "price": 1290,
                "currency": "USD",
            },
        },
    }


def _raw(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _signature(raw_body: bytes) -> str:
    return hmac.new(SECRET, raw_body, hashlib.sha256).hexdigest()


class _IngestDb:
    def __init__(self, *, fail_flush: bool = False):
        self.event: PaymentEvent | None = None
        self.fail_flush = fail_flush
        self.commit_count = 0
        self.execute_count = 0

    async def execute(self, _statement, _params=None):
        self.execute_count += 1

    async def scalar(self, _statement):
        return self.event

    def add(self, value):
        if isinstance(value, PaymentEvent):
            self.event = value

    async def flush(self):
        if self.fail_flush:
            raise RuntimeError("database unavailable")

    async def commit(self):
        self.commit_count += 1


class _Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)


class _ApplyDb:
    def __init__(self, purchase, credit, *, existing_capture=None):
        self.purchase = purchase
        self.credit = credit
        self.existing_capture = existing_capture
        self.added = []

    async def scalars(self, statement):
        if "credit_purchases" in str(statement):
            return _Rows([self.purchase])
        return _Rows([])

    async def scalar(self, statement):
        sql = str(statement)
        if "payment_capture_facts" in sql:
            return self.existing_capture
        if "user_credits" in sql:
            return self.credit
        if "payment_reconciliation_cases" in sql:
            return None
        return None

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


def _ready_purchase(*, credits=50) -> CreditPurchase:
    purchase_id = uuid.uuid4()
    return CreditPurchase(
        id=purchase_id,
        user_id=uuid.uuid4(),
        provider="creem",
        package_id="pack_50",
        credits=credits,
        price_cents=1290,
        currency="USD",
        status=CreditPurchaseStatus.PENDING,
        provider_request_id="cp_request_1",
        intent_state=PurchaseIntentState.READY,
        request_hash="a" * 64,
        catalog_version_id=uuid.uuid4(),
        catalog_snapshot={
            "provider_product_id": "prod_pack_50",
            "pre_tax_minor_units": 1290,
            "currency": "USD",
            "retention_tier": "paid_90d",
        },
        internal_metadata_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        captured_minor_units=0,
        tax_minor_units=0,
        refunded_minor_units=0,
        disputed_minor_units=0,
        dispute_state="NONE",
        stored_response={
            "purchase_id": str(purchase_id),
            "provider": "creem",
            "status": "READY",
            "checkout_url": "https://checkout.creem.io/ch_1",
        },
    )


def _capture_event(*, pre_tax=1290, tax=103) -> PaymentEvent:
    return PaymentEvent(
        id=uuid.uuid4(),
        provider="creem",
        event_id="evt_checkout_1",
        event_type="checkout.completed",
        object_id="ch_1",
        raw_payload_sha256="b" * 64,
        occurred_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        request_id="cp_request_1",
        customer_id="cust_1",
        pre_tax_minor_units=pre_tax,
        tax_minor_units=tax,
        currency="USD",
        normalized_status="paid",
        business_metadata={
            "provider_product_id": "prod_pack_50",
            "provider_payment_id": "tran_1",
            "provider_checkout_id": "ch_1",
            "vowpic_purchase_ref": "00000000-0000-4000-8000-000000000001",
        },
        processing_state=PaymentEventProcessingState.RECEIVED,
    )


class PaymentWebhookTest(unittest.IsolatedAsyncioTestCase):
    def test_signature_uses_raw_body_and_constant_time_comparison(self) -> None:
        raw_body = _raw(_checkout_payload())
        signature = _signature(raw_body)
        with patch(
            "app.services.creem_event_service.hmac.compare_digest",
            wraps=hmac.compare_digest,
        ) as compare:
            verify_creem_signature(raw_body, signature, SECRET)
        compare.assert_called_once_with(signature, signature)

        reformatted = json.dumps(_checkout_payload(), indent=2).encode("utf-8")
        with self.assertRaises(InvalidWebhookSignature):
            verify_creem_signature(reformatted, signature, SECRET)

    def test_invalid_signature_and_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaises(InvalidWebhookSignature):
            verify_creem_signature(_raw(_checkout_payload()), "bad", SECRET)
        with self.assertRaises(CreemEventError) as duplicate:
            parse_creem_raw_body(b'{"id":"evt_1","id":"evt_2"}')
        self.assertEqual(duplicate.exception.code, "webhook_json_duplicate_key")

    def test_checkout_event_normalizes_money_and_tax_independently(self) -> None:
        payload = _checkout_payload()
        raw_body = _raw(payload)
        normalized = normalize_creem_event(payload, hashlib.sha256(raw_body).hexdigest())

        self.assertEqual(normalized.event_id, "evt_checkout_1")
        self.assertEqual(normalized.event_type, "checkout.completed")
        self.assertEqual(normalized.request_id, "cp_request_1")
        self.assertEqual(normalized.pre_tax_minor_units, 1290)
        self.assertEqual(normalized.tax_minor_units, 103)
        self.assertEqual(normalized.currency, "USD")
        self.assertEqual(normalized.business_metadata["provider_payment_id"], "tran_1")
        self.assertEqual(normalized.business_metadata["provider_product_id"], "prod_pack_50")

    async def test_duplicate_signed_event_is_one_durable_event(self) -> None:
        db = _IngestDb()
        raw_body = _raw(_checkout_payload())
        signature = _signature(raw_body)

        first = await ingest_verified_creem_event(
            db,
            raw_body=raw_body,
            signature=signature,
            webhook_secret=SECRET,
        )
        second = await ingest_verified_creem_event(
            db,
            raw_body=raw_body,
            signature=signature,
            webhook_secret=SECRET,
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(db.event.raw_payload_sha256, hashlib.sha256(raw_body).hexdigest())

    async def test_duplicate_webhook_retries_unapplied_event(self) -> None:
        accepted = AcceptedPaymentEvent(
            event_id="evt_checkout_1",
            created=False,
            processing_state="RECEIVED",
        )
        event = SimpleNamespace(
            id=uuid.uuid4(),
            processing_state=PaymentEventProcessingState.RECEIVED,
        )
        db = AsyncMock()
        db.scalar.return_value = event
        service = PaymentService()
        with (
            patch(
                "app.services.payment_service.ingest_verified_creem_event",
                new=AsyncMock(return_value=accepted),
            ),
            patch.object(service, "apply_payment_event", new=AsyncMock()) as apply,
        ):
            result = await service.process_webhook_event(
                db,
                body=b"{}",
                signature_header="signed",
            )

        self.assertIs(result, accepted)
        apply.assert_awaited_once_with(db, payment_event_id=event.id)
        db.commit.assert_awaited_once()

    async def test_reconciliation_required_event_is_terminal_for_replay(self) -> None:
        db = AsyncMock()
        db.scalar.return_value = SimpleNamespace(
            processing_state=PaymentEventProcessingState.RECONCILIATION_REQUIRED,
        )

        result = await PaymentService().apply_payment_event(
            db,
            payment_event_id=uuid.uuid4(),
        )

        self.assertIsNone(result)

    async def test_same_event_id_with_different_signed_body_is_conflict(self) -> None:
        db = _IngestDb()
        first_body = _raw(_checkout_payload())
        await ingest_verified_creem_event(
            db,
            raw_body=first_body,
            signature=_signature(first_body),
            webhook_secret=SECRET,
        )
        changed = _checkout_payload()
        changed["object"]["order"]["tax_amount"] = 104
        changed_body = _raw(changed)
        with self.assertRaises(PaymentEventHashConflict):
            await ingest_verified_creem_event(
                db,
                raw_body=changed_body,
                signature=_signature(changed_body),
                webhook_secret=SECRET,
            )

    async def test_unknown_signed_event_is_retained_unhandled(self) -> None:
        db = _IngestDb()
        payload = {
            "id": "evt_future_1",
            "eventType": "future.event",
            "created_at": 1783944000000,
            "object": {"id": "future_1", "status": "new"},
        }
        raw_body = _raw(payload)
        accepted = await ingest_verified_creem_event(
            db,
            raw_body=raw_body,
            signature=_signature(raw_body),
            webhook_secret=SECRET,
        )

        self.assertEqual(accepted.processing_state, "UNHANDLED")
        self.assertEqual(db.event.processing_state, PaymentEventProcessingState.UNHANDLED)
        self.assertEqual(db.event.payload_json, payload)

    async def test_database_failure_is_not_reported_as_accepted(self) -> None:
        db = _IngestDb(fail_flush=True)
        raw_body = _raw(_checkout_payload())
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            await ingest_verified_creem_event(
                db,
                raw_body=raw_body,
                signature=_signature(raw_body),
                webhook_secret=SECRET,
            )
        self.assertEqual(db.commit_count, 0)

    async def test_verified_capture_grants_one_root_lot_and_records_tax(self) -> None:
        purchase = _ready_purchase()
        credit = UserCredit(user_id=purchase.user_id, balance=2, reserved_balance=0)
        db = _ApplyDb(purchase, credit)
        event = _capture_event()

        result = await PaymentService()._apply_checkout_capture(db, event)

        self.assertIs(result, purchase)
        roots = [item for item in db.added if isinstance(item, CreditTransaction)]
        lots = [item for item in db.added if isinstance(item, CreditGrantLot)]
        captures = [item for item in db.added if isinstance(item, PaymentCaptureFact)]
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].transaction_type, CreditTransactionType.PURCHASE)
        self.assertEqual(roots[0].amount, 50)
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].root_transaction_id, roots[0].id)
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].pre_tax_minor_units, 1290)
        self.assertEqual(captures[0].tax_minor_units, 103)
        self.assertEqual(purchase.captured_minor_units, 1393)
        self.assertEqual(purchase.tax_minor_units, 103)
        self.assertEqual(purchase.intent_state, PurchaseIntentState.CONFIRMED)
        self.assertEqual(credit.balance, 52)

    async def test_purchase_grant_offsets_existing_debt_before_becoming_spendable(self) -> None:
        purchase = _ready_purchase()
        credit = UserCredit(user_id=purchase.user_id, balance=-20, reserved_balance=0)
        db = _ApplyDb(purchase, credit)

        await PaymentService()._apply_checkout_capture(db, _capture_event())

        lot = next(item for item in db.added if isinstance(item, CreditGrantLot))
        self.assertEqual(lot.original_amount, 50)
        self.assertEqual(lot.debt_offset_amount, 20)
        self.assertEqual(lot.spendable_amount, 30)
        self.assertEqual(credit.balance, 30)

    async def test_catalog_amount_mismatch_opens_case_without_grant(self) -> None:
        purchase = _ready_purchase()
        credit = UserCredit(user_id=purchase.user_id, balance=2, reserved_balance=0)
        db = _ApplyDb(purchase, credit)
        event = _capture_event(pre_tax=1200)

        await PaymentService()._apply_checkout_capture(db, event)

        self.assertEqual(
            [item for item in db.added if isinstance(item, CreditTransaction)],
            [],
        )
        cases = [item for item in db.added if isinstance(item, PaymentReconciliationCase)]
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].reason_code, "checkout_money_or_catalog_mismatch")
        self.assertEqual(event.processing_state, PaymentEventProcessingState.RECONCILIATION_REQUIRED)
        self.assertEqual(credit.balance, 2)

    async def test_replayed_provider_payment_does_not_create_second_grant(self) -> None:
        purchase = _ready_purchase()
        transaction_id = uuid.uuid4()
        lot_id = uuid.uuid4()
        existing = PaymentCaptureFact(
            id=uuid.uuid4(),
            purchase_id=purchase.id,
            payment_event_id=uuid.uuid4(),
            provider="creem",
            provider_payment_id="tran_1",
            pre_tax_minor_units=1290,
            tax_minor_units=103,
            currency="USD",
            grant_transaction_id=transaction_id,
            grant_lot_id=lot_id,
            occurred_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        )
        db = _ApplyDb(
            purchase,
            UserCredit(user_id=purchase.user_id, balance=52, reserved_balance=0),
            existing_capture=existing,
        )
        event = _capture_event()

        await PaymentService()._apply_checkout_capture(db, event)

        self.assertEqual([item for item in db.added if isinstance(item, CreditTransaction)], [])
        self.assertEqual(event.processing_state, PaymentEventProcessingState.APPLIED)


if __name__ == "__main__":
    unittest.main()
