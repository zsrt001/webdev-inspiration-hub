"""Fact-driven subscription status, paid transaction, and cancellation tests."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
import uuid

from app.core.provider_contracts import (
    CREEM_SUBSCRIPTION_PAID_TRANSACTION,
    CREEM_SUBSCRIPTION_PERIOD_END_CANCELLATION,
    ProviderContractState,
)
from app.models.subscription_cancel_intent import (
    CancelIntentState,
    SubscriptionCancelIntent,
)
from app.models.user_subscription import NormalizedSubscriptionStatus
from app.services.subscription_service import (
    CancellationReconciliationPending,
    SubscriptionFactInvalid,
    cancel_replay_or_raise,
    normalized_status_for_event,
    validate_subscription_paid_fact,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class SubscriptionLifecycleTest(unittest.TestCase):
    def test_status_only_events_never_imply_a_paid_transaction(self) -> None:
        self.assertEqual(
            normalized_status_for_event("subscription.active", "active"),
            NormalizedSubscriptionStatus.ACTIVE,
        )
        self.assertEqual(
            normalized_status_for_event("subscription.past_due", "active"),
            NormalizedSubscriptionStatus.PAST_DUE,
        )
        self.assertEqual(
            normalized_status_for_event("subscription.scheduled_cancel", "active"),
            NormalizedSubscriptionStatus.CANCEL_REQUESTED,
        )
        self.assertEqual(
            normalized_status_for_event("subscription.canceled", "active"),
            NormalizedSubscriptionStatus.CANCELED,
        )

    def test_paid_fact_requires_stable_transaction_exact_period_and_catalog_money(self) -> None:
        validated = validate_subscription_paid_fact(
            provider_transaction_id="tran_1",
            period_start=NOW,
            period_end=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
            event_pre_tax_minor_units=1900,
            event_tax_minor_units=152,
            event_currency="USD",
            catalog_pre_tax_minor_units=1900,
            catalog_currency="USD",
        )
        self.assertEqual(validated.total_minor_units, 2052)
        for missing_transaction in ("", None):
            with self.subTest(missing_transaction=missing_transaction), self.assertRaises(SubscriptionFactInvalid):
                validate_subscription_paid_fact(
                    provider_transaction_id=missing_transaction,
                    period_start=NOW,
                    period_end=datetime(2026, 8, 13, tzinfo=timezone.utc),
                    event_pre_tax_minor_units=1900,
                    event_tax_minor_units=0,
                    event_currency="USD",
                    catalog_pre_tax_minor_units=1900,
                    catalog_currency="USD",
                )

    def test_cancel_confirmed_replays_and_calling_unknown_never_retry(self) -> None:
        response = {
            "subscription_id": "sub_1",
            "state": "CONFIRMED",
            "cancel_at_period_end": True,
        }
        confirmed = SubscriptionCancelIntent(
            id=uuid.uuid4(),
            state=CancelIntentState.CONFIRMED,
            stored_response=response,
        )
        self.assertEqual(cancel_replay_or_raise(confirmed), response)
        for state in (CancelIntentState.CALLING, CancelIntentState.UNKNOWN):
            with self.subTest(state=state), self.assertRaises(CancellationReconciliationPending):
                cancel_replay_or_raise(
                    SubscriptionCancelIntent(id=uuid.uuid4(), state=state)
                )

    def test_external_subscription_contracts_stay_closed_without_test_mode_proof(self) -> None:
        self.assertEqual(
            CREEM_SUBSCRIPTION_PAID_TRANSACTION.state,
            ProviderContractState.UNVERIFIED,
        )
        self.assertIsNotNone(CREEM_SUBSCRIPTION_PAID_TRANSACTION.endpoint_schema_sha256)
        self.assertIsNone(CREEM_SUBSCRIPTION_PAID_TRANSACTION.test_evidence_sha256)
        self.assertEqual(
            CREEM_SUBSCRIPTION_PERIOD_END_CANCELLATION.state,
            ProviderContractState.UNVERIFIED,
        )
        self.assertIsNotNone(
            CREEM_SUBSCRIPTION_PERIOD_END_CANCELLATION.endpoint_schema_sha256
        )


if __name__ == "__main__":
    unittest.main()
