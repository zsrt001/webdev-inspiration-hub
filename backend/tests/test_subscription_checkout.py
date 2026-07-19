"""Durable subscription checkout and first-webhook correlation tests."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

import httpx

from app.core.provider_contracts import ProviderContract, ProviderContractState
from app.models.payment_event import PaymentEvent, PaymentEventProcessingState
from app.models.subscription_checkout_intent import (
    SubscriptionCheckoutIntent,
    SubscriptionCheckoutIntentState,
)
from app.models.user_subscription import NormalizedSubscriptionStatus
from app.services.billing_catalog_service import (
    BillingProductSnapshot,
    CheckoutCatalogSelection,
)
from app.services.creem_event_service import normalize_creem_event
from app.services.subscription_service import (
    SubscriptionCheckoutReconciliationPending,
    SubscriptionError,
    SubscriptionService,
)


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000101")
PLAN_ID = uuid.UUID("00000000-0000-4000-8000-000000000102")
CATALOG_ID = uuid.UUID("00000000-0000-4000-8000-000000000103")


def _verified_contract() -> ProviderContract:
    return ProviderContract(
        provider="creem",
        capability="subscription_paid_transaction",
        state=ProviderContractState.VERIFIED,
        endpoint_schema_sha256="a" * 64,
        test_evidence_sha256="b" * 64,
    )


def _selection(code: str = "starter_monthly") -> CheckoutCatalogSelection:
    return CheckoutCatalogSelection(
        catalog_version_id=CATALOG_ID,
        catalog_version="2026-07-10",
        release_sha="c" * 40,
        product=BillingProductSnapshot(
            product_code=code,
            product_kind="subscription",
            pre_tax_minor_units=1900 if code == "starter_monthly" else 4900,
            currency="USD",
            credits=80 if code == "starter_monthly" else 300,
            retention_tier="subscription_180d",
            provider_product_id=f"prod_{code}",
            metadata={},
        ),
    )


class _Rows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _CheckoutDb:
    def __init__(self):
        self.plan = SimpleNamespace(
            id=PLAN_ID,
            catalog_product_code="starter_monthly",
            is_active=True,
        )
        self.intents: list[SubscriptionCheckoutIntent] = []
        self.subscriptions = []
        self.added = []
        self.commit_count = 0

    async def execute(self, _statement, _params=None):
        return None

    async def scalars(self, statement):
        sql = str(statement)
        if "subscription_plans" in sql:
            return _Rows([self.plan])
        if "subscription_checkout_intents" in sql:
            return _Rows(self.intents)
        if "user_subscriptions" in sql:
            return _Rows(self.subscriptions)
        return _Rows([])

    async def scalar(self, statement):
        sql = str(statement)
        if "subscription_checkout_intents" in sql:
            return self.intents[0] if self.intents else None
        if "user_subscriptions" in sql:
            return self.subscriptions[0] if self.subscriptions else None
        return None

    def add(self, value):
        self.added.append(value)
        if isinstance(value, SubscriptionCheckoutIntent):
            self.intents.append(value)
        elif hasattr(value, "provider_subscription_id"):
            self.subscriptions.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.commit_count += 1


class _CheckoutService(SubscriptionService):
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls = []

    def _headers(self):
        return {"x-api-key": "test-key", "Content-Type": "application/json"}

    async def _request(self, method, path, *, json_body=None):
        self.calls.append((method, path, dict(json_body or {})))
        if self.error is not None:
            raise self.error
        return {
            "id": "ch_subscription_1",
            "request_id": json_body["request_id"],
            "product_id": json_body["product_id"],
            "status": "pending",
            "checkout_url": "https://checkout.creem.io/ch_subscription_1",
        }


class SubscriptionCheckoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_ready_checkout_is_durable_and_same_key_replays_one_call(self) -> None:
        db = _CheckoutDb()
        service = _CheckoutService()
        with (
            patch(
                "app.services.subscription_service.CREEM_SUBSCRIPTION_PAID_TRANSACTION",
                _verified_contract(),
            ),
            patch(
                "app.services.subscription_service.require_subscription_checkout_catalog_product",
                return_value=_selection(),
            ),
        ):
            first = await service.create_checkout(
                db,
                user_id=USER_ID,
                plan_code="starter_monthly",
                return_url="https://www.vowpic.com/account",
                idempotency_key="subscription-checkout-1",
            )
            second = await service.create_checkout(
                db,
                user_id=USER_ID,
                plan_code="starter_monthly",
                return_url="https://www.vowpic.com/account",
                idempotency_key="subscription-checkout-1",
            )

        self.assertEqual(first, second)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(db.intents[0].state, SubscriptionCheckoutIntentState.READY)
        self.assertEqual(db.intents[0].provider_checkout_id, "ch_subscription_1")
        self.assertEqual(db.commit_count, 2)

    async def test_ambiguous_response_stays_unknown_and_is_never_recalled(self) -> None:
        request = httpx.Request("POST", "https://api.creem.io/v1/checkouts")
        service = _CheckoutService(
            error=httpx.ReadTimeout("timeout", request=request)
        )
        db = _CheckoutDb()
        with (
            patch(
                "app.services.subscription_service.CREEM_SUBSCRIPTION_PAID_TRANSACTION",
                _verified_contract(),
            ),
            patch(
                "app.services.subscription_service.require_subscription_checkout_catalog_product",
                return_value=_selection(),
            ),
        ):
            with self.assertRaises(SubscriptionCheckoutReconciliationPending):
                await service.create_checkout(
                    db,
                    user_id=USER_ID,
                    plan_code="starter_monthly",
                    return_url=None,
                    idempotency_key="subscription-checkout-timeout",
                )
            self.assertEqual(
                db.intents[0].state,
                SubscriptionCheckoutIntentState.UNKNOWN,
            )
            with self.assertRaises(SubscriptionCheckoutReconciliationPending):
                await service.create_checkout(
                    db,
                    user_id=USER_ID,
                    plan_code="starter_monthly",
                    return_url=None,
                    idempotency_key="subscription-checkout-timeout",
                )
        self.assertEqual(len(service.calls), 1)

    async def test_same_key_with_different_catalog_request_is_rejected(self) -> None:
        db = _CheckoutDb()
        service = _CheckoutService()
        with (
            patch(
                "app.services.subscription_service.CREEM_SUBSCRIPTION_PAID_TRANSACTION",
                _verified_contract(),
            ),
            patch(
                "app.services.subscription_service.require_subscription_checkout_catalog_product",
                side_effect=[_selection(), _selection("creator_monthly")],
            ),
        ):
            await service.create_checkout(
                db,
                user_id=USER_ID,
                plan_code="starter_monthly",
                return_url=None,
                idempotency_key="subscription-checkout-conflict",
            )
            with self.assertRaises(SubscriptionError) as raised:
                await service.create_checkout(
                    db,
                    user_id=USER_ID,
                    plan_code="creator_monthly",
                    return_url=None,
                    idempotency_key="subscription-checkout-conflict",
                )
        self.assertEqual(raised.exception.code, "idempotency_payload_mismatch")
        self.assertEqual(len(service.calls), 1)

    async def test_unverified_contract_creates_no_intent_and_calls_no_provider(self) -> None:
        db = _CheckoutDb()
        service = _CheckoutService()
        with self.assertRaises(SubscriptionError) as raised:
            await service.create_checkout(
                db,
                user_id=USER_ID,
                plan_code="starter_monthly",
                return_url=None,
                idempotency_key="subscription-checkout-closed",
            )
        self.assertEqual(
            raised.exception.code,
            "subscription_paid_transaction_unverified",
        )
        self.assertEqual(db.intents, [])
        self.assertEqual(service.calls, [])

    async def test_signed_paid_event_correlates_intent_and_creates_projection(self) -> None:
        db = _CheckoutDb()
        selection = _selection()
        intent = SubscriptionCheckoutIntent(
            id=uuid.uuid4(),
            user_id=USER_ID,
            plan_id=PLAN_ID,
            catalog_version_id=CATALOG_ID,
            product_code=selection.product.product_code,
            idempotency_key="subscription-checkout-webhook",
            request_hash="d" * 64,
            provider_request_id="subco_request_1",
            internal_metadata_id=uuid.uuid4(),
            state=SubscriptionCheckoutIntentState.READY,
            catalog_snapshot=selection.as_snapshot(),
            stored_response={
                "provider": "creem",
                "status": "READY",
                "checkout_url": "https://checkout.creem.io/ch_subscription_1",
            },
            provider_checkout_id="ch_subscription_1",
            checkout_url="https://checkout.creem.io/ch_subscription_1",
            ready_at=NOW,
        )
        db.intents.append(intent)
        event = PaymentEvent(
            id=uuid.uuid4(),
            provider="creem",
            event_id="evt_subscription_paid_1",
            event_type="subscription.paid",
            object_id="sub_1",
            request_id=intent.provider_request_id,
            customer_id="cust_1",
            pre_tax_minor_units=1900,
            tax_minor_units=152,
            currency="USD",
            normalized_status="active",
            business_metadata={
                "provider_product_id": "prod_starter_monthly",
                "provider_checkout_id": "ch_subscription_1",
                "vowpic_subscription_checkout_ref": str(
                    intent.internal_metadata_id
                ),
            },
            raw_payload_sha256="e" * 64,
            occurred_at=NOW,
            processing_state=PaymentEventProcessingState.RECEIVED,
        )

        subscription = await SubscriptionService()._find_subscription_for_event(
            db,
            event,
        )

        self.assertIsNotNone(subscription)
        self.assertEqual(subscription.user_id, USER_ID)
        self.assertEqual(subscription.provider_subscription_id, "sub_1")
        self.assertEqual(
            subscription.normalized_status,
            NormalizedSubscriptionStatus.PENDING,
        )
        self.assertEqual(
            intent.state,
            SubscriptionCheckoutIntentState.CONFIRMED,
        )
        self.assertEqual(intent.provider_subscription_id, "sub_1")

    def test_official_envelope_shape_keeps_checkout_correlation_and_money(self) -> None:
        payload = {
            "id": "evt_subscription_paid_2",
            "eventType": "subscription.paid",
            "created_at": "2026-07-19T12:00:00Z",
            "subscription": {
                "id": "sub_2",
                "status": "active",
                "product": "prod_starter_monthly",
                "customer": "cust_2",
                "last_transaction_id": "tran_2",
                "current_period_start_date": "2026-07-19T12:00:00Z",
                "current_period_end_date": "2026-08-19T12:00:00Z",
            },
            "checkout": {
                "id": "ch_subscription_2",
                "request_id": "subco_request_2",
                "metadata": {
                    "vowpic_subscription_checkout_ref": str(uuid.uuid4())
                },
            },
            "order": {
                "id": "ord_2",
                "product": "prod_starter_monthly",
                "customer": "cust_2",
                "transaction": "tran_2",
                "sub_total": 1900,
                "tax_amount": 152,
                "currency": "USD",
            },
        }

        normalized = normalize_creem_event(payload, "f" * 64)

        self.assertEqual(normalized.object_id, "sub_2")
        self.assertEqual(normalized.request_id, "subco_request_2")
        self.assertEqual(normalized.pre_tax_minor_units, 1900)
        self.assertEqual(normalized.tax_minor_units, 152)
        self.assertEqual(normalized.currency, "USD")
        self.assertEqual(
            normalized.business_metadata["provider_checkout_id"],
            "ch_subscription_2",
        )
        self.assertEqual(
            normalized.business_metadata["provider_product_id"],
            "prod_starter_monthly",
        )


if __name__ == "__main__":
    unittest.main()
