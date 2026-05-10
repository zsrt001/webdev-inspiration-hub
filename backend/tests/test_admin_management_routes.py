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


if __name__ == "__main__":
    unittest.main()
