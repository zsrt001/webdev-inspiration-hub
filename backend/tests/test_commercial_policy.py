"""Commercial pricing, retention, and legal policy contract tests."""

from pathlib import Path
import importlib
import sys
import unittest
from datetime import datetime, timezone


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.order import Order  # noqa: E402
from app.services.credit_service import get_generation_cost, get_live_portrait_cost  # noqa: E402


class CommercialPolicyTest(unittest.TestCase):
    def test_generation_pricing_is_differentiated_by_complexity(self) -> None:
        self.assertEqual(get_generation_cost(None, image_count=1), 2)
        self.assertEqual(get_generation_cost(None, image_count=1, director_mode=True), 3)
        self.assertEqual(get_generation_cost(None, image_count=2), 3)
        self.assertEqual(get_generation_cost(None, image_count=2, is_remote_join=True), 4)
        self.assertEqual(get_generation_cost("vintage", image_count=1), 2)
        self.assertEqual(get_generation_cost("vintage", image_count=2), 3)
        self.assertEqual(get_live_portrait_cost(seconds=5), 6)
        self.assertEqual(get_live_portrait_cost(seconds=10), 10)

    def test_order_retention_columns_exist(self) -> None:
        columns = set(Order.__table__.columns.keys())

        self.assertIn("source_images_expires_at", columns)
        self.assertIn("expires_at", columns)
        self.assertIn("deleted_at", columns)
        self.assertIn("storage_cleanup_status", columns)

    def test_retention_policy_contract(self) -> None:
        retention = importlib.import_module("app.services.retention_service")

        self.assertEqual(retention.source_image_retention_days(), 7)
        self.assertEqual(retention.order_retention_days(plan_code=None, has_paid_credits=False), 30)
        self.assertEqual(retention.order_retention_days(plan_code=None, has_paid_credits=True), 90)
        self.assertEqual(retention.order_retention_days(plan_code="starter_monthly", has_paid_credits=True), 180)
        self.assertEqual(retention.order_retention_days(plan_code="studio_monthly", has_paid_credits=True), 365)

    def test_legal_policy_api_is_registered(self) -> None:
        routers_module = importlib.import_module("app.routers")
        routes = {route.path for route in routers_module.api_router.routes}

        self.assertIn("/legal/policies", routes)


class RetentionCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_source_cleanup_status_is_per_order_not_batch_global(self) -> None:
        retention = importlib.import_module("app.services.retention_service")
        now = datetime.now(timezone.utc)
        orders = [
            Order(source_image_urls={"front": "/static/uploads/a.jpg"}, source_images_expires_at=now),
            Order(source_image_urls={"front": "/static/uploads/b.jpg"}, source_images_expires_at=now),
        ]

        class FakeScalars:
            def all(self):
                return orders

        class FakeResult:
            def scalars(self):
                return FakeScalars()

        class FakeDb:
            flushed = False

            async def execute(self, _statement):
                return FakeResult()

            async def flush(self):
                self.flushed = True

        outcomes = iter([
            {"deleted": 0, "failed": 1},
            {"deleted": 1, "failed": 0},
        ])
        original_delete_storage_urls = retention.delete_storage_urls
        retention.delete_storage_urls = lambda _urls: next(outcomes)
        try:
            summary = await retention.cleanup_expired_source_images(FakeDb(), now=now)
        finally:
            retention.delete_storage_urls = original_delete_storage_urls

        self.assertEqual(summary["orders"], 2)
        self.assertEqual(summary["failed_files"], 1)
        self.assertEqual(orders[0].storage_cleanup_status, "cleanup_failed")
        self.assertEqual(orders[1].storage_cleanup_status, "source_deleted")


if __name__ == "__main__":
    unittest.main()
