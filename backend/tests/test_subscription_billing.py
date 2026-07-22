"""Normalized subscription invoice and grant behavior."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx

from app.models.credit_grant_lot import CreditGrantLot
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.payment_event import PaymentEvent, PaymentEventProcessingState
from app.models.subscription_cancel_intent import CancelIntentState, SubscriptionCancelIntent
from app.models.subscription_credit_grant import SubscriptionCreditGrant
from app.models.subscription_invoice import (
    SubscriptionInvoice,
    SubscriptionInvoiceAdjustmentFact,
)
from app.models.user_credit import UserCredit
from app.models.user_subscription import (
    NormalizedSubscriptionStatus,
    SubscriptionStatus,
    UserSubscription,
)
from app.services.billing_catalog_service import (
    BillingProductSnapshot,
    CheckoutCatalogSelection,
)
from app.services.subscription_service import (
    CancellationReconciliationPending,
    SubscriptionError,
    SubscriptionService,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def _selection(code: str, credits: int, price: int, retention_tier: str) -> CheckoutCatalogSelection:
    return CheckoutCatalogSelection(
        catalog_version_id=uuid.UUID("00000000-0000-4000-8000-000000000018"),
        catalog_version="2026-07-10",
        release_sha="c" * 40,
        product=BillingProductSnapshot(
            product_code=code,
            product_kind="subscription",
            pre_tax_minor_units=price,
            currency="USD",
            credits=credits,
            retention_tier=retention_tier,
            provider_product_id=f"prod_{code}",
            metadata={},
        ),
    )


def _subscription() -> UserSubscription:
    return UserSubscription(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        provider="creem",
        provider_subscription_id="sub_1",
        status=SubscriptionStatus.ACTIVE,
        normalized_status=NormalizedSubscriptionStatus.ACTIVE,
        cancel_at_period_end=False,
    )


def _paid_event(selection: CheckoutCatalogSelection, *, event_id="evt_paid_1", transaction_id="tran_1") -> PaymentEvent:
    return PaymentEvent(
        id=uuid.uuid4(),
        provider="creem",
        event_id=event_id,
        event_type="subscription.paid",
        object_id="sub_1",
        raw_payload_sha256="d" * 64,
        occurred_at=NOW,
        pre_tax_minor_units=selection.product.pre_tax_minor_units,
        tax_minor_units=152,
        currency="USD",
        normalized_status="active",
        business_metadata={
            "provider_product_id": selection.product.provider_product_id,
            "last_transaction_id": transaction_id,
            "current_period_start_date": NOW.isoformat(),
            "current_period_end_date": PERIOD_END.isoformat(),
        },
        processing_state=PaymentEventProcessingState.RECEIVED,
    )


class _Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)


class _GrantDb:
    def __init__(self, subscription, credit):
        self.subscription = subscription
        self.credit = credit
        self.invoices = []
        self.grants = {}
        self.added = []

    async def scalars(self, statement):
        sql = str(statement)
        if "subscription_invoices" in sql:
            return _Rows(self.invoices)
        if "user_subscriptions" in sql:
            return _Rows([self.subscription])
        return _Rows([])

    async def scalar(self, statement):
        sql = str(statement)
        if "user_credits" in sql:
            return self.credit
        if "user_subscriptions" in sql:
            return self.subscription
        if "subscription_credit_grants" in sql:
            return next(iter(self.grants.values()), None)
        if "subscription_cancel_intents" in sql:
            return None
        if "payment_reconciliation_cases" in sql:
            return None
        return None

    def add(self, value):
        self.added.append(value)
        if isinstance(value, SubscriptionInvoice):
            self.invoices.append(value)
        if isinstance(value, SubscriptionCreditGrant):
            self.grants[value.id] = value

    async def flush(self):
        return None


class SubscriptionBillingServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_exact_catalog_credits_and_double_uniqueness_grant_once(self) -> None:
        cases = (
            ("starter_monthly", 80, 1900, "subscription_180d"),
            ("creator_monthly", 300, 4900, "subscription_180d"),
            ("studio_monthly", 900, 12900, "studio_365d"),
        )
        for code, credits, price, retention in cases:
            with self.subTest(code=code):
                selection = _selection(code, credits, price, retention)
                subscription = _subscription()
                credit = UserCredit(
                    user_id=subscription.user_id,
                    balance=2,
                    reserved_balance=0,
                )
                db = _GrantDb(subscription, credit)
                service = SubscriptionService()
                event = _paid_event(selection)
                with (
                    patch(
                        "app.services.subscription_service.require_subscription_catalog_product",
                        new=AsyncMock(return_value=selection),
                    ),
                ):
                    first = await service.apply_subscription_paid_transaction(
                        db,
                        event=event,
                        subscription=subscription,
                    )
                    second = await service.apply_subscription_paid_transaction(
                        db,
                        event=event,
                        subscription=subscription,
                    )

                self.assertFalse(first.replayed)
                self.assertTrue(second.replayed)
                self.assertEqual(first.grant.id, second.grant.id)
                roots = [item for item in db.added if isinstance(item, CreditTransaction)]
                lots = [item for item in db.added if isinstance(item, CreditGrantLot)]
                self.assertEqual(len(roots), 1)
                self.assertEqual(roots[0].transaction_type, CreditTransactionType.SUBSCRIPTION_GRANT)
                self.assertEqual(roots[0].amount, credits)
                self.assertEqual(len(lots), 1)
                self.assertEqual(first.invoice.provider_transaction_id, "tran_1")
                self.assertEqual(first.invoice.period_start, NOW)
                self.assertEqual(first.invoice.period_end, PERIOD_END)
                self.assertEqual(credit.balance, 2 + credits)
                self.assertEqual(subscription.paid_through_at, PERIOD_END)

    async def test_negative_balance_is_permanently_recorded_as_debt_offset(self) -> None:
        selection = _selection("starter_monthly", 80, 1900, "subscription_180d")
        subscription = _subscription()
        credit = UserCredit(user_id=subscription.user_id, balance=-50, reserved_balance=0)
        db = _GrantDb(subscription, credit)
        with (
            patch(
                "app.services.subscription_service.require_subscription_catalog_product",
                new=AsyncMock(return_value=selection),
            ),
        ):
            await SubscriptionService().apply_subscription_paid_transaction(
                db,
                event=_paid_event(selection),
                subscription=subscription,
            )
        lot = next(item for item in db.added if isinstance(item, CreditGrantLot))
        self.assertEqual(lot.debt_offset_amount, 50)
        self.assertEqual(lot.spendable_amount, 30)
        self.assertEqual(credit.balance, 30)

    async def test_active_and_past_due_status_events_grant_zero_and_do_not_extend_paid_through(self) -> None:
        subscription = _subscription()
        subscription.paid_through_at = PERIOD_END
        credit = UserCredit(user_id=subscription.user_id, balance=2, reserved_balance=0)
        db = _GrantDb(subscription, credit)
        service = SubscriptionService()
        for event_type, expected in (
            ("subscription.active", NormalizedSubscriptionStatus.ACTIVE),
            ("subscription.past_due", NormalizedSubscriptionStatus.PAST_DUE),
        ):
            event = PaymentEvent(
                id=uuid.uuid4(),
                provider="creem",
                event_id=f"evt_{event_type}",
                event_type=event_type,
                object_id="sub_1",
                raw_payload_sha256="e" * 64,
                occurred_at=NOW,
                normalized_status="active",
                business_metadata={},
                processing_state=PaymentEventProcessingState.RECEIVED,
            )
            await service.apply_normalized_payment_event(db, event=event)
            self.assertEqual(subscription.normalized_status, expected)
            self.assertEqual(event.processing_state, PaymentEventProcessingState.APPLIED)
        self.assertEqual(
            [item for item in db.added if isinstance(item, CreditTransaction)],
            [],
        )
        self.assertEqual(subscription.paid_through_at, PERIOD_END)
        self.assertEqual(credit.balance, 2)


class _AdjustmentDb:
    def __init__(self, invoice, grant, lot):
        self.invoice = invoice
        self.grant = grant
        self.lot = lot
        self.adjustments = []

    async def scalar(self, statement):
        sql = str(statement)
        if "subscription_invoices" in sql:
            return self.invoice
        if "subscription_invoice_adjustment_facts" in sql:
            return next(
                (
                    item
                    for item in self.adjustments
                    if str(item.payment_event_id) in sql
                ),
                None,
            )
        if "subscription_credit_grants" in sql:
            return self.grant
        if "credit_grant_lots" in sql:
            return self.lot
        if "payment_reconciliation_cases" in sql:
            return None
        return None

    def add(self, value):
        if isinstance(value, SubscriptionInvoiceAdjustmentFact):
            self.adjustments.append(value)

    async def flush(self):
        return None


def _subscription_invoice_fixture():
    user_id = uuid.uuid4()
    invoice = SubscriptionInvoice(
        id=uuid.uuid4(),
        subscription_id=uuid.uuid4(),
        user_id=user_id,
        payment_event_id=uuid.uuid4(),
        provider="creem",
        provider_transaction_id="tran_subscription_1",
        period_start=NOW,
        period_end=PERIOD_END,
        pre_tax_minor_units=1900,
        tax_minor_units=152,
        currency="USD",
        provider_status="paid",
        occurred_at=NOW,
        raw_payload_sha256="a" * 64,
        catalog_version_id=uuid.uuid4(),
        catalog_snapshot={"credits": 80},
        credit_grant_id=uuid.uuid4(),
        refunded_minor_units=0,
        disputed_minor_units=0,
        dispute_state="NONE",
    )
    grant = SubscriptionCreditGrant(
        id=invoice.credit_grant_id,
        subscription_id=invoice.subscription_id,
        user_id=user_id,
        period_key=NOW.isoformat(),
        period_start=NOW,
        period_end=PERIOD_END,
        credits=80,
        invoice_id=invoice.id,
        credit_transaction_id=uuid.uuid4(),
        grant_lot_id=uuid.uuid4(),
    )
    lot = CreditGrantLot(
        id=grant.grant_lot_id,
        user_id=user_id,
        root_transaction_id=grant.credit_transaction_id,
        source_type="SUBSCRIPTION",
        source_id=str(invoice.id),
        original_amount=80,
        debt_offset_amount=0,
        reversed_amount=0,
        frozen_amount=0,
        consumed_amount=20,
        retention_tier="subscription_180d",
    )
    return invoice, grant, lot


def _subscription_adjustment_event(*, event_type, object_id, amount, status):
    return PaymentEvent(
        id=uuid.uuid4(),
        provider="creem",
        event_id=f"evt_{object_id}_{status}",
        event_type=event_type,
        object_id=object_id,
        raw_payload_sha256="b" * 64,
        occurred_at=PERIOD_END,
        currency="USD",
        normalized_status=status,
        business_metadata={
            "provider_payment_id": "tran_subscription_1",
            "event_minor_units": str(amount),
            (
                "provider_refund_id"
                if event_type == "refund.created"
                else "provider_dispute_id"
            ): object_id,
        },
        processing_state=PaymentEventProcessingState.RECEIVED,
    )


class SubscriptionAdjustmentTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_invoice_refund_reverses_exact_subscription_root(self) -> None:
        invoice, grant, lot = _subscription_invoice_fixture()
        db = _AdjustmentDb(invoice, grant, lot)
        event = _subscription_adjustment_event(
            event_type="refund.created",
            object_id="refund_subscription_1",
            amount=2052,
            status="succeeded",
        )
        reversal_transaction = CreditTransaction(
            id=uuid.uuid4(),
            user_id=invoice.user_id,
            transaction_type=CreditTransactionType.SUBSCRIPTION_REVERSAL,
            amount=-80,
            balance_after=-20,
        )
        reversal = AsyncMock(
            return_value=SimpleNamespace(
                transaction=reversal_transaction,
                debt=20,
                replayed=False,
            )
        )
        with patch(
            "app.services.subscription_service.reverse_root_grant",
            new=reversal,
        ):
            applied = await SubscriptionService().apply_subscription_adjustment_event(
                db,
                event=event,
            )

        self.assertTrue(applied)
        reversal.assert_awaited_once()
        self.assertEqual(
            reversal.await_args.kwargs["transaction_type"],
            CreditTransactionType.SUBSCRIPTION_REVERSAL,
        )
        self.assertEqual(invoice.refunded_minor_units, 2052)
        fact = db.adjustments[0]
        self.assertEqual(fact.adjustment_kind, "REFUND")
        self.assertEqual(fact.outcome, "FULL")
        self.assertEqual(fact.reversal_transaction_id, reversal_transaction.id)
        self.assertEqual(event.processing_state, PaymentEventProcessingState.APPLIED)

    async def test_partial_invoice_refund_freezes_lineage_and_requires_reconciliation(self) -> None:
        invoice, grant, lot = _subscription_invoice_fixture()
        db = _AdjustmentDb(invoice, grant, lot)
        event = _subscription_adjustment_event(
            event_type="refund.created",
            object_id="refund_subscription_partial",
            amount=500,
            status="succeeded",
        )
        with patch(
            "app.services.subscription_service.open_payment_reconciliation_case",
            new=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
        ) as open_case:
            applied = await SubscriptionService().apply_subscription_adjustment_event(
                db,
                event=event,
            )

        self.assertTrue(applied)
        self.assertEqual(invoice.refunded_minor_units, 500)
        self.assertEqual(lot.frozen_amount, 60)
        self.assertEqual(db.adjustments[0].outcome, "PARTIAL_RECONCILIATION_REQUIRED")
        self.assertEqual(
            event.processing_state,
            PaymentEventProcessingState.RECONCILIATION_REQUIRED,
        )
        open_case.assert_awaited_once()

    async def test_invoice_dispute_open_win_and_full_loss_are_root_specific(self) -> None:
        invoice, grant, lot = _subscription_invoice_fixture()
        db = _AdjustmentDb(invoice, grant, lot)
        service = SubscriptionService()
        with patch(
            "app.services.subscription_service.open_payment_reconciliation_case",
            new=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
        ):
            opened = _subscription_adjustment_event(
                event_type="dispute.created",
                object_id="dispute_subscription_1",
                amount=2052,
                status="open",
            )
            self.assertTrue(
                await service.apply_subscription_adjustment_event(db, event=opened)
            )
        self.assertEqual(lot.frozen_amount, 60)
        self.assertEqual(invoice.dispute_state, "OPEN")

        won = _subscription_adjustment_event(
            event_type="dispute.closed",
            object_id="dispute_subscription_1",
            amount=2052,
            status="won",
        )
        self.assertTrue(await service.apply_subscription_adjustment_event(db, event=won))
        self.assertEqual(lot.frozen_amount, 0)
        self.assertEqual(invoice.dispute_state, "WON")

        lost = _subscription_adjustment_event(
            event_type="dispute.closed",
            object_id="dispute_subscription_2",
            amount=2052,
            status="lost",
        )
        reversal_transaction = CreditTransaction(
            id=uuid.uuid4(),
            user_id=invoice.user_id,
            transaction_type=CreditTransactionType.SUBSCRIPTION_REVERSAL,
            amount=-80,
            balance_after=-20,
        )
        reversal = AsyncMock(
            return_value=SimpleNamespace(
                transaction=reversal_transaction,
                debt=20,
                replayed=False,
            )
        )
        with patch(
            "app.services.subscription_service.reverse_root_grant",
            new=reversal,
        ):
            self.assertTrue(
                await service.apply_subscription_adjustment_event(db, event=lost)
            )
        self.assertEqual(invoice.dispute_state, "LOST")
        self.assertEqual(invoice.disputed_minor_units, 2052)
        self.assertEqual(db.adjustments[-1].reversal_transaction_id, reversal_transaction.id)


class _CancelDb:
    def __init__(self, subscription):
        self.subscription = subscription
        self.intent = None
        self.commit_count = 0

    async def execute(self, _statement, _params=None):
        return None

    async def scalar(self, statement):
        sql = str(statement)
        if "subscription_cancel_intents" in sql:
            return self.intent
        if "user_subscriptions" in sql:
            return self.subscription
        return None

    def add(self, value):
        if isinstance(value, SubscriptionCancelIntent):
            self.intent = value

    async def flush(self):
        return None

    async def commit(self):
        self.commit_count += 1


class _CancelService(SubscriptionService):
    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def _request(self, method, path, *, json_body=None):
        self.calls.append((method, path, json_body))
        if self.error is not None:
            raise self.error
        return dict(self.response)


class SubscriptionCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_cancel_replays_one_provider_call(self) -> None:
        subscription = _subscription()
        db = _CancelDb(subscription)
        service = _CancelService(
            response={"id": "sub_1", "status": "scheduled_cancel"}
        )
        first = await service.request_period_end_cancellation(
            db,
            user_id=subscription.user_id,
            subscription_id=subscription.id,
            idempotency_key="cancel-1",
        )
        second = await service.request_period_end_cancellation(
            db,
            user_id=subscription.user_id,
            subscription_id=subscription.id,
            idempotency_key="cancel-1",
        )
        self.assertEqual(first, second)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(db.intent.state, CancelIntentState.CONFIRMED)
        self.assertEqual(subscription.normalized_status, NormalizedSubscriptionStatus.CANCEL_REQUESTED)
        self.assertTrue(subscription.cancel_at_period_end)

    async def test_ambiguous_cancel_stays_unknown_and_never_retries(self) -> None:
        subscription = _subscription()
        request = httpx.Request("POST", "https://api.creem.io/v1/subscriptions/sub_1/cancel")
        service = _CancelService(error=httpx.ReadTimeout("timeout", request=request))
        db = _CancelDb(subscription)
        with self.assertRaises(CancellationReconciliationPending):
            await service.request_period_end_cancellation(
                db,
                user_id=subscription.user_id,
                subscription_id=subscription.id,
                idempotency_key="cancel-timeout",
            )
        self.assertEqual(db.intent.state, CancelIntentState.UNKNOWN)
        with self.assertRaises(CancellationReconciliationPending):
            await service.request_period_end_cancellation(
                db,
                user_id=subscription.user_id,
                subscription_id=subscription.id,
                idempotency_key="cancel-timeout",
            )
        self.assertEqual(len(service.calls), 1)


class SubscriptionRouteContractTest(unittest.TestCase):
    def test_unsupported_mutation_routes_do_not_exist(self) -> None:
        from app.routers import api_router
        from tests.route_contract import effective_paths

        paths = effective_paths(api_router)
        for suffix in ("trial", "pause", "resume", "upgrade", "downgrade", "proration"):
            self.assertNotIn(f"/subscriptions/{suffix}", paths)


if __name__ == "__main__":
    unittest.main()
