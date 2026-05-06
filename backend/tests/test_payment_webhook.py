"""Hosted payment webhook and status contract tests."""

from pathlib import Path
import sys
import unittest
import uuid


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus  # noqa: E402
from app.services.payment_service import PaymentService  # noqa: E402


class _FakeDb:
    def __init__(self):
        self.flush_count = 0
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1


class _IdempotentPaymentService(PaymentService):
    def __init__(self):
        self.credit_events = []

    async def _add_purchase_credits(self, db, purchase, checkout_payload):
        self.credit_events.append((str(purchase.id), int(purchase.credits)))
        return 100 + int(purchase.credits)


class PaymentWebhookTest(unittest.IsolatedAsyncioTestCase):
    def test_payment_status_contract_uses_commercial_states(self) -> None:
        self.assertEqual(CreditPurchaseStatus.PENDING.value, "pending")
        self.assertEqual(CreditPurchaseStatus.PAID.value, "paid")
        self.assertEqual(CreditPurchaseStatus.FAILED.value, "failed")
        self.assertEqual(CreditPurchaseStatus.EXPIRED.value, "expired")
        self.assertEqual(CreditPurchaseStatus.REFUNDED.value, "refunded")

    async def test_repeated_finalize_does_not_add_credits_twice(self) -> None:
        service = _IdempotentPaymentService()
        db = _FakeDb()
        purchase = CreditPurchase(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="creem",
            package_id="pack_50",
            credits=50,
            price_cents=1290,
            currency="USD",
            status=CreditPurchaseStatus.PENDING,
            provider_request_id="request-1",
            provider_checkout_id="checkout-1",
        )

        await service.finalize_purchase(
            db,
            purchase,
            checkout_payload={"checkout_id": "checkout-1", "payment_id": "payment-1"},
            webhook_event_id="event-1",
        )
        await service.finalize_purchase(
            db,
            purchase,
            checkout_payload={"checkout_id": "checkout-1", "payment_id": "payment-1"},
            webhook_event_id="event-1",
        )

        self.assertEqual(purchase.status, CreditPurchaseStatus.PAID)
        self.assertEqual(purchase.webhook_event_id, "event-1")
        self.assertEqual(service.credit_events, [(str(purchase.id), 50)])

    def test_invalid_webhook_signature_is_rejected(self) -> None:
        service = PaymentService()

        self.assertFalse(service.verify_webhook_signature(b'{"id":"evt_1"}', "bad-signature"))

    def test_creem_event_type_object_payload_is_supported(self) -> None:
        service = PaymentService()
        payload = {
            "id": "evt_1",
            "eventType": "checkout.completed",
            "object": {
                "id": "checkout_1",
                "request_id": "request-1",
                "order": {"id": "order_1", "status": "paid"},
            },
        }

        checkout = service._extract_checkout_dict(payload)

        self.assertEqual(service._extract_event_type(payload), "checkout.completed")
        self.assertEqual(service._extract_checkout_id(checkout), "checkout_1")
        self.assertEqual(checkout["request_id"], "request-1")
        self.assertEqual(service._extract_payment_id(checkout), "order_1")
        self.assertEqual(service._normalize_status(checkout), "paid")


if __name__ == "__main__":
    unittest.main()
