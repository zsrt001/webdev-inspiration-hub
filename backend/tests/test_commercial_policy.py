"""Commercial pricing, retention, and legal policy contract tests."""

from pathlib import Path
import importlib
import sys
import unittest
from datetime import datetime, timezone


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.order import Order  # noqa: E402
from app.services.credit_service import get_generation_cost  # noqa: E402


class CommercialPolicyTest(unittest.TestCase):
    def test_stage_one_ui_and_privacy_copy_do_not_claim_active_deletion(self) -> None:
        account = (ROOT / "frontend" / "src" / "pages" / "account" / "index.vue").read_text(
            encoding="utf-8"
        )
        privacy = (ROOT / "frontend" / "src" / "pages" / "legal" / "privacy.vue").read_text(
            encoding="utf-8"
        )
        consent = (ROOT / "frontend" / "src" / "components" / "LegalConsentInline.vue").read_text(
            encoding="utf-8"
        )
        legal_service = (
            ROOT / "backend" / "app" / "services" / "legal_policy_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('@tap.stop="deleteOrder(order.id)"', account)
        self.assertNotIn("async function deleteOrder", account)
        self.assertNotIn("Image files will be removed", account)
        self.assertIn("Deletion temporarily paused", account)

        self.assertNotIn("are deleted after 7 days by default", privacy)
        self.assertNotIn("Users can delete images from the account center", privacy)
        self.assertIn("Automated and in-account deletion are temporarily paused", privacy)
        self.assertIn("自动删除和账户中心删除目前均已暂停", privacy)
        self.assertNotIn("source images are periodically deleted", consent)
        self.assertIn("source-image deletion is temporarily paused", consent)
        self.assertNotIn("removed automatically after expiry", legal_service)
        self.assertIn("Automated deletion is temporarily paused", legal_service)

    def test_generation_pricing_follows_subject_count_not_director_mode(self) -> None:
        self.assertEqual(get_generation_cost(None, image_count=1), 2)
        self.assertEqual(get_generation_cost(None, image_count=1, director_mode=True), 2)
        self.assertEqual(get_generation_cost(None, image_count=2), 3)
        self.assertEqual(get_generation_cost(None, image_count=2, is_remote_join=True), 3)
        self.assertEqual(get_generation_cost("vintage", image_count=1), 2)
        self.assertEqual(get_generation_cost("vintage", image_count=2), 3)

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
        self.assertEqual(orders[0].source_image_urls, {"front": "/static/uploads/a.jpg"})
        self.assertEqual(orders[1].storage_cleanup_status, "source_deleted")
        self.assertIsNone(orders[1].source_image_urls)

    async def test_order_cleanup_preserves_all_references_when_any_delete_fails(self) -> None:
        retention = importlib.import_module("app.services.retention_service")
        now = datetime.now(timezone.utc)
        order = Order(
            source_image_urls={"front": "/static/uploads/source.jpg"},
            preview_image_urls={"main": "/static/uploads/preview.jpg"},
            final_image_urls={"main": "/static/uploads/final.jpg"},
            expires_at=now,
        )

        class FakeScalars:
            def all(self):
                return [order]

        class FakeResult:
            def scalars(self):
                return FakeScalars()

        class FakeDb:
            async def execute(self, _statement):
                return FakeResult()

            async def flush(self):
                return None

        original_delete_storage_urls = retention.delete_storage_urls
        retention.delete_storage_urls = lambda _urls: {"deleted": 2, "failed": 1}
        try:
            summary = await retention.cleanup_expired_orders(FakeDb(), now=now)
        finally:
            retention.delete_storage_urls = original_delete_storage_urls

        self.assertEqual(summary["failed_files"], 1)
        self.assertEqual(order.source_image_urls, {"front": "/static/uploads/source.jpg"})
        self.assertEqual(order.preview_image_urls, {"main": "/static/uploads/preview.jpg"})
        self.assertEqual(order.final_image_urls, {"main": "/static/uploads/final.jpg"})
        self.assertIsNone(order.deleted_at)
        self.assertEqual(order.storage_cleanup_status, "cleanup_failed")

    async def test_transient_generated_cleanup_targets_generated_prefix_only(self) -> None:
        retention = importlib.import_module("app.services.retention_service")
        now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

        class FakeStorage:
            called_with = None

            def cleanup_generated_files_older_than(self, *, cutoff, limit):
                self.called_with = {"cutoff": cutoff, "limit": limit}
                return {
                    "provider": "vercel",
                    "prefix": "generated/",
                    "checked": 3,
                    "matched": 2,
                    "deleted_files": 2,
                    "failed_files": 0,
                    "freed_bytes_estimate": 1024,
                    "freed_mb_estimate": 0.0,
                    "skipped": False,
                }

        fake_storage = FakeStorage()
        original_storage = retention.storage_service
        retention.storage_service = fake_storage
        try:
            summary = retention.cleanup_transient_generated_assets(
                now=now,
                older_than_hours=6,
                limit=250,
            )
        finally:
            retention.storage_service = original_storage

        self.assertEqual(summary["prefix"], "generated/")
        self.assertEqual(summary["deleted_files"], 2)
        self.assertEqual(fake_storage.called_with["limit"], 250)
        self.assertEqual(fake_storage.called_with["cutoff"].isoformat(), "2026-05-20T06:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
