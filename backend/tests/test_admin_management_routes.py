"""Admin management route contract tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class AdminManagementRoutesTest(unittest.TestCase):
    def test_admin_management_routes_are_registered(self) -> None:
        from app.routers import api_router

        routes = {(route.path, ",".join(sorted(route.methods or []))) for route in api_router.routes}
        paths = {path for path, _methods in routes}

        self.assertIn("/admin/dashboard", paths)
        self.assertIn("/admin/payment_config_summary", paths)
        self.assertIn("/admin/users", paths)
        self.assertIn("/admin/users/{user_id}/status", paths)
        self.assertIn("/admin/orders", paths)
        self.assertIn("/admin/orders/{order_id}", paths)
        self.assertIn("/admin/orders/{order_id}/status", paths)

    def test_order_status_options_reuse_existing_enum(self) -> None:
        from app.models.order import OrderStatus
        from app.routers import admin

        self.assertEqual({item.value for item in OrderStatus}, admin.ORDER_STATUS_VALUES)

    def test_payment_config_summary_does_not_expose_secrets(self) -> None:
        import asyncio

        from app.routers.admin import get_payment_config_summary

        summary = asyncio.run(get_payment_config_summary()).model_dump()
        serialized = str(summary).lower()

        self.assertIn(summary["creem_api_key_mode"], {"missing", "test", "live", "unknown"})
        self.assertNotIn("api_key", summary)
        self.assertNotIn("webhook_secret", summary)
        self.assertNotIn("creem_test_", serialized)


if __name__ == "__main__":
    unittest.main()
