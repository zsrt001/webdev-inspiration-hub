"""Credit-pack checkout intent state and replay contract."""

from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.models.credit_purchase import CreditPurchase, PurchaseIntentState
from app.services.billing_catalog_service import (
    BillingCatalogUnavailable,
    BillingProductSnapshot,
    CheckoutCatalogSelection,
)
from app.services.idempotency_service import IdempotencyConflict
from app.services.payment_service import (
    CheckoutReconciliationPending,
    CheckoutStatusUnknown,
    PaymentError,
    PaymentService,
    checkout_replay_or_raise,
)


class CreditPackCheckoutTest(unittest.TestCase):
    def test_ready_and_confirmed_replay_only_stored_redirect(self) -> None:
        stored = {
            "purchase_id": "00000000-0000-4000-8000-000000000001",
            "provider": "creem",
            "status": "READY",
            "checkout_url": "https://checkout.creem.io/c/abc",
        }
        for state in (PurchaseIntentState.READY, PurchaseIntentState.CONFIRMED):
            purchase = CreditPurchase(intent_state=state, stored_response=stored)
            self.assertEqual(checkout_replay_or_raise(purchase), stored)

    def test_calling_or_unknown_never_causes_an_automatic_provider_retry(self) -> None:
        for state in (PurchaseIntentState.CALLING, PurchaseIntentState.UNKNOWN):
            purchase = CreditPurchase(intent_state=state)
            with self.subTest(state=state), self.assertRaises(CheckoutReconciliationPending):
                checkout_replay_or_raise(purchase)

    def test_provider_request_id_is_stable_and_does_not_expose_raw_key(self) -> None:
        user_id = uuid.uuid4()
        first = PaymentService._provider_request_id(user_id, "browser-visible-key")
        second = PaymentService._provider_request_id(user_id, "browser-visible-key")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("cp_"))
        self.assertNotIn("browser-visible-key", first)


class _CheckoutDb:
    def __init__(self):
        self.purchase = None
        self.commit_count = 0

    async def scalar(self, statement):
        if "credit_purchases" in str(statement):
            return self.purchase
        return None

    def add(self, value):
        if isinstance(value, CreditPurchase):
            self.purchase = value

    async def flush(self):
        return None

    async def commit(self):
        self.commit_count += 1


class _ProviderPaymentService(PaymentService):
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.provider_calls = []

    async def _request(self, method, path, *, json_body=None):
        self.provider_calls.append((method, path, json_body))
        if self.error is not None:
            raise self.error
        return dict(self.response)


def _selection() -> CheckoutCatalogSelection:
    return CheckoutCatalogSelection(
        catalog_version_id=uuid.UUID("00000000-0000-4000-8000-000000000016"),
        catalog_version="2026-07-10",
        release_sha="a" * 40,
        product=BillingProductSnapshot(
            product_code="pack_50",
            product_kind="credit_pack",
            pre_tax_minor_units=1290,
            currency="USD",
            credits=50,
            retention_tier="paid_90d",
            provider_product_id="prod_pack_50",
            metadata={},
        ),
    )


def _provider_response(**overrides):
    response = {
        "id": "ch_1",
        "checkout_url": "https://checkout.creem.io/ch_1",
        "request_id": None,
        "product": {"id": "prod_pack_50", "price": 1290, "currency": "USD"},
        "status": "pending",
    }
    response.update(overrides)
    return response


class CreditPackCheckoutAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def _create(self, service, db, *, idempotency_key="idem-1", response=None):
        selection = _selection()
        provider_request_id = service._provider_request_id(self.user_id, idempotency_key)
        if response is not None:
            response["request_id"] = provider_request_id
            service.response = response
        with (
            patch(
                "app.services.payment_service.require_checkout_catalog_product",
                new=AsyncMock(return_value=selection),
            ),
            patch(
                "app.services.payment_service.begin_idempotent_request",
                new=AsyncMock(return_value=SimpleNamespace(record_id=uuid.uuid4())),
            ),
            patch(
                "app.services.payment_service.complete_idempotent_request",
                new=AsyncMock(),
            ),
        ):
            return await service.create_credit_pack_checkout(
                db,
                user_id=self.user_id,
                product_code="pack_50",
                idempotency_key=idempotency_key,
                return_url="https://example.test/account",
            )

    async def asyncSetUp(self):
        self.user_id = uuid.uuid4()

    async def test_intent_is_durable_before_provider_call_and_ready_replays_without_call(self) -> None:
        db = _CheckoutDb()
        service = _ProviderPaymentService(response={})
        response = _provider_response()
        created = await self._create(service, db, response=response)

        self.assertEqual(created.status, "READY")
        self.assertEqual(db.purchase.intent_state, PurchaseIntentState.READY)
        self.assertEqual(db.commit_count, 2)
        self.assertEqual(len(service.provider_calls), 1)
        self.assertIsNone(db.purchase.grant_transaction_id)
        self.assertEqual(db.purchase.captured_minor_units, 0)
        provider_payload = service.provider_calls[0][2]
        self.assertEqual(provider_payload["request_id"], db.purchase.provider_request_id)
        self.assertEqual(provider_payload["product_id"], "prod_pack_50")
        self.assertNotIn("user_id", provider_payload["metadata"])

        replay = await self._create(service, db, response=response)
        self.assertEqual(replay.checkout_url, created.checkout_url)
        self.assertEqual(len(service.provider_calls), 1)

    async def test_ambiguous_timeout_moves_to_unknown_and_is_never_auto_retried(self) -> None:
        request = httpx.Request("POST", "https://api.creem.io/v1/checkouts")
        service = _ProviderPaymentService(error=httpx.ReadTimeout("timeout", request=request))
        db = _CheckoutDb()
        with self.assertRaises(CheckoutStatusUnknown):
            await self._create(service, db)
        self.assertEqual(db.purchase.intent_state, PurchaseIntentState.UNKNOWN)
        self.assertEqual(len(service.provider_calls), 1)

        with self.assertRaises(CheckoutReconciliationPending):
            await self._create(service, db)
        self.assertEqual(len(service.provider_calls), 1)

    async def test_catalog_or_provider_mismatch_fails_closed(self) -> None:
        db = _CheckoutDb()
        service = _ProviderPaymentService(response={})
        with self.assertRaises(PaymentError) as mismatch:
            await self._create(
                service,
                db,
                response=_provider_response(product={"id": "prod_other", "price": 1290, "currency": "USD"}),
            )
        self.assertEqual(mismatch.exception.code, "payment_checkout_product_mismatch")
        self.assertEqual(db.purchase.intent_state, PurchaseIntentState.UNKNOWN)

        with (
            patch(
                "app.services.payment_service.require_checkout_catalog_product",
                new=AsyncMock(side_effect=BillingCatalogUnavailable("active_catalog_cardinality")),
            ),
            self.assertRaises(PaymentError) as unavailable,
        ):
            await PaymentService().create_credit_pack_checkout(
                _CheckoutDb(),
                user_id=self.user_id,
                product_code="pack_50",
                idempotency_key="idem-catalog",
                return_url=None,
            )
        self.assertEqual(unavailable.exception.code, "credit_catalog_unavailable")

    async def test_same_key_different_payload_is_409(self) -> None:
        with (
            patch(
                "app.services.payment_service.require_checkout_catalog_product",
                new=AsyncMock(return_value=_selection()),
            ),
            patch(
                "app.services.payment_service.begin_idempotent_request",
                new=AsyncMock(side_effect=IdempotencyConflict("idempotency_payload_mismatch")),
            ),
            self.assertRaises(PaymentError) as conflict,
        ):
            await PaymentService().create_credit_pack_checkout(
                _CheckoutDb(),
                user_id=self.user_id,
                product_code="pack_50",
                idempotency_key="reused-key",
                return_url=None,
            )
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(conflict.exception.code, "idempotency_payload_mismatch")


if __name__ == "__main__":
    unittest.main()
