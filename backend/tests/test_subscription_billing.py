"""Subscription billing model and migration contract tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.payment_event import PaymentEvent  # noqa: E402
from app.models.subscription_credit_grant import SubscriptionCreditGrant  # noqa: E402
from app.models.subscription_plan import SubscriptionPlan  # noqa: E402
from app.models.user_subscription import SubscriptionStatus, UserSubscription  # noqa: E402


MIGRATION_PATH = BACKEND_DIR / "alembic" / "versions" / "20260426_0004_subscription_billing.py"


class SubscriptionBillingModelTest(unittest.TestCase):
    def test_subscription_status_contract_uses_lowercase_provider_safe_values(self) -> None:
        self.assertEqual(SubscriptionStatus.TRIALING.value, "trialing")
        self.assertEqual(SubscriptionStatus.ACTIVE.value, "active")
        self.assertEqual(SubscriptionStatus.PAST_DUE.value, "past_due")
        self.assertEqual(SubscriptionStatus.CANCELED.value, "canceled")
        self.assertEqual(SubscriptionStatus.EXPIRED.value, "expired")

    def test_payment_event_unique_identity(self) -> None:
        event = PaymentEvent(provider="creem", event_id="evt_1", event_type="subscription.paid")

        self.assertEqual(event.provider, "creem")
        self.assertEqual(event.event_id, "evt_1")
        self.assertEqual(event.event_type, "subscription.paid")

    def test_subscription_tables_declare_idempotency_constraints(self) -> None:
        plan_constraints = {constraint.name for constraint in SubscriptionPlan.__table__.constraints}
        subscription_constraints = {constraint.name for constraint in UserSubscription.__table__.constraints}
        grant_constraints = {constraint.name for constraint in SubscriptionCreditGrant.__table__.constraints}
        event_constraints = {constraint.name for constraint in PaymentEvent.__table__.constraints}

        self.assertIn("uq_subscription_plans_code", plan_constraints)
        self.assertIn("uq_user_subscriptions_provider_subscription_id", subscription_constraints)
        self.assertIn("uq_subscription_credit_grant_period", grant_constraints)
        self.assertIn("uq_payment_events_provider_event_id", event_constraints)

    def test_subscription_migration_seeds_plans_and_applies_rls(self) -> None:
        self.assertTrue(MIGRATION_PATH.exists())
        sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()

        for table_name in (
            "subscription_plans",
            "user_subscriptions",
            "subscription_credit_grants",
            "payment_events",
        ):
            self.assertIn(table_name, sql)

        for plan_code in ("starter_monthly", "creator_monthly", "studio_monthly"):
            self.assertIn(plan_code, sql)

        self.assertIn("enable row level security", sql)
        self.assertIn("app_current_user_id", sql)
        self.assertNotIn("credit_card", sql)
        self.assertNotIn("cvv", sql)


if __name__ == "__main__":
    unittest.main()
