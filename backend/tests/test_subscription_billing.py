"""Subscription billing model and migration contract tests."""

from pathlib import Path
import importlib
import sys
import unittest
import uuid


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.payment_event import PaymentEvent  # noqa: E402
from app.models.credit_transaction import CreditTransactionType  # noqa: E402
from app.models.subscription_credit_grant import SubscriptionCreditGrant  # noqa: E402
from app.models.subscription_plan import SubscriptionPlan  # noqa: E402
from app.models.user_subscription import SubscriptionStatus, UserSubscription  # noqa: E402
from app.models.user_credit import UserCredit  # noqa: E402
from app.services.credit_service import DEFAULT_CREDITS  # noqa: E402
from app.services.payment_service import PaymentService, settings as payment_settings  # noqa: E402
from app.services.subscription_service import SubscriptionService, settings as subscription_settings  # noqa: E402


MIGRATION_PATH = BACKEND_DIR / "alembic" / "versions" / "20260426_0004_subscription_billing.py"


class _ScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return [] if self._row is None else [self._row]


class _FakeSubscriptionDb:
    def __init__(self):
        self.credit_row = None
        self.transactions = []
        self.grants = {}

    async def execute(self, statement):
        text = str(statement)
        if "subscription_credit_grants" in text:
            params = getattr(statement, "compile", lambda **_: None)
            _ = params
            return _ScalarResult(next(iter(self.grants.values()), None))
        return _ScalarResult(self.credit_row)

    def add(self, value):
        if isinstance(value, UserCredit):
            self.credit_row = value
        elif isinstance(value, SubscriptionCreditGrant):
            self.grants[(str(value.subscription_id), value.period_key)] = value
        else:
            self.transactions.append(value)

    async def flush(self):
        for item in [*self.transactions, *self.grants.values()]:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()
        return None


class _FakeWebhookDb:
    def __init__(self):
        self.events = {}
        self.credit_row = None
        self.transactions = []
        self.grants = {}
        self.plan = SubscriptionPlan(
            id=uuid.uuid4(),
            code="starter_monthly",
            name="Starter",
            billing_interval="month",
            price_cents=1900,
            currency="USD",
            monthly_credits=80,
            is_active=True,
        )
        self.subscription = UserSubscription(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            plan_id=self.plan.id,
            provider="creem",
            provider_subscription_id="sub_1",
            status=SubscriptionStatus.ACTIVE,
        )
        self.subscription.plan = self.plan

    async def execute(self, statement):
        text = str(statement)
        if "payment_events" in text:
            return _ScalarResult(self.events.get(("creem", "evt_sub_1")))
        if "user_subscriptions" in text:
            return _ScalarResult(self.subscription)
        if "subscription_credit_grants" in text:
            return _ScalarResult(self.grants.get((str(self.subscription.id), "2026-04")))
        return _ScalarResult(self.credit_row)

    def add(self, value):
        if isinstance(value, PaymentEvent):
            self.events[(value.provider, value.event_id)] = value
        elif isinstance(value, UserCredit):
            self.credit_row = value
        elif isinstance(value, SubscriptionCreditGrant):
            self.grants[(str(value.subscription_id), value.period_key)] = value
        else:
            self.transactions.append(value)

    async def flush(self):
        for item in [*self.events.values(), *self.transactions, *self.grants.values()]:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()
        return None


class _SignedWebhookPaymentService(PaymentService):
    def verify_webhook_signature(self, body, signature_header):
        return True


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

    def test_subscription_api_schema_contract(self) -> None:
        schema_module = importlib.import_module("app.schemas.subscription")

        plan = schema_module.SubscriptionPlanRead(
            code="starter_monthly",
            name="Starter",
            billing_interval="month",
            price_cents=1900,
            currency="USD",
            monthly_credits=80,
            feature_flags={"remote_join": True},
        )
        current = schema_module.CurrentSubscriptionRead(
            status="active",
            plan_code="starter_monthly",
            current_period_end=None,
            cancel_at_period_end=False,
        )

        self.assertEqual(plan.code, "starter_monthly")
        self.assertEqual(plan.monthly_credits, 80)
        self.assertEqual(current.status, "active")
        self.assertEqual(current.plan_code, "starter_monthly")

    def test_creem_subscription_product_ids_map_to_plan_codes(self) -> None:
        service = SubscriptionService()
        old_creator = subscription_settings.creem_subscription_creator_product_id
        subscription_settings.creem_subscription_creator_product_id = "prod_creator"
        try:
            self.assertEqual(service._plan_code_for_product_id("prod_creator"), "creator_monthly")
            self.assertEqual(service._product_id({"product": {"id": "prod_creator"}}), "prod_creator")
            self.assertEqual(service._provider_customer_id({"customer": {"id": "cust_1"}}), "cust_1")
        finally:
            subscription_settings.creem_subscription_creator_product_id = old_creator

    def test_creem_return_urls_upgrade_local_http_to_https_frontend(self) -> None:
        old_frontend = payment_settings.frontend_base_url
        old_provider = payment_settings.payment_provider
        payment_settings.frontend_base_url = "https://frontend.example.test"
        payment_settings.payment_provider = "creem"
        try:
            local_return_url = "http://127.0.0.1:3000/pages/create/index?from=test"

            self.assertEqual(
                PaymentService()._safe_return_url(local_return_url),
                "https://frontend.example.test/pages/create/index?from=test",
            )
            self.assertEqual(
                SubscriptionService()._safe_return_url(local_return_url),
                "https://frontend.example.test/pages/create/index?from=test",
            )
        finally:
            payment_settings.frontend_base_url = old_frontend
            payment_settings.payment_provider = old_provider

    def test_creem_success_urls_keep_root_path_before_query(self) -> None:
        self.assertEqual(
            PaymentService()._append_query("https://frontend.example.test", payment="success"),
            "https://frontend.example.test/?payment=success",
        )
        self.assertEqual(
            SubscriptionService()._append_query("https://frontend.example.test", subscription="success"),
            "https://frontend.example.test/?subscription=success",
        )

    def test_subscription_router_is_registered(self) -> None:
        routers_module = importlib.import_module("app.routers")

        routes = {route.path for route in routers_module.api_router.routes}

        self.assertIn("/subscriptions/plans", routes)
        self.assertIn("/subscriptions/me", routes)
        self.assertIn("/subscriptions/checkout", routes)
        self.assertIn("/subscriptions/cancel", routes)


class SubscriptionBillingServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_subscription_period_grant_is_idempotent(self) -> None:
        db = _FakeSubscriptionDb()
        user_id = uuid.uuid4()
        subscription = UserSubscription(
            id=uuid.uuid4(),
            user_id=user_id,
            plan_id=uuid.uuid4(),
            provider="creem",
            provider_subscription_id="sub_1",
            status=SubscriptionStatus.ACTIVE,
        )
        db.credit_row = UserCredit(user_id=user_id, balance=DEFAULT_CREDITS)
        service = SubscriptionService()

        first = await service.grant_period_credits(
            db,
            subscription,
            period_key="2026-04",
            credits=80,
        )
        second = await service.grant_period_credits(
            db,
            subscription,
            period_key="2026-04",
            credits=80,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.credit_transaction_id, second.credit_transaction_id)
        self.assertEqual(len(db.grants), 1)
        subscription_grants = [
            tx for tx in db.transactions if tx.transaction_type == CreditTransactionType.SUBSCRIPTION_GRANT
        ]
        self.assertEqual(len(subscription_grants), 1)
        self.assertEqual(subscription_grants[0].amount, 80)
        self.assertEqual(subscription_grants[0].balance_after, DEFAULT_CREDITS + 80)

    async def test_repeated_subscription_webhook_is_recorded_and_processed_once(self) -> None:
        db = _FakeWebhookDb()
        service = _SignedWebhookPaymentService()
        event = {
            "id": "evt_sub_1",
            "type": "subscription.paid",
            "data": {
                "subscription_id": "sub_1",
                "current_period_start": "2026-04-01T00:00:00Z",
                "current_period_end": "2026-05-01T00:00:00Z",
            },
        }

        await service.process_webhook_event(db, payload=event, body=b"{}", signature_header="ok")
        await service.process_webhook_event(db, payload=event, body=b"{}", signature_header="ok")

        self.assertEqual(len(db.events), 1)
        stored_event = db.events[("creem", "evt_sub_1")]
        self.assertIsNotNone(stored_event.processed_at)
        self.assertIsNone(stored_event.error)
        self.assertEqual(stored_event.event_type, "subscription.paid")
        self.assertEqual(stored_event.object_id, "sub_1")
        self.assertEqual(len(db.grants), 1)
        subscription_grants = [
            tx for tx in db.transactions if tx.transaction_type == CreditTransactionType.SUBSCRIPTION_GRANT
        ]
        self.assertEqual(len(subscription_grants), 1)
        self.assertEqual(subscription_grants[0].amount, 80)


if __name__ == "__main__":
    unittest.main()
