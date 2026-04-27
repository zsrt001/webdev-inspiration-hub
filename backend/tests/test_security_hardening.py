"""Security hardening contract tests."""

from pathlib import Path
import importlib
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class SecurityHardeningTest(unittest.TestCase):
    def test_rate_limiter_blocks_after_configured_limit(self) -> None:
        from app.core.rate_limit import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(limit=2, window_seconds=60)

        self.assertFalse(limiter.is_limited("client-a", now=100.0))
        self.assertFalse(limiter.is_limited("client-a", now=101.0))
        self.assertTrue(limiter.is_limited("client-a", now=102.0))
        self.assertFalse(limiter.is_limited("client-a", now=161.0))

    def test_commercial_config_requires_rate_limiting(self) -> None:
        runtime_checks = importlib.import_module("app.core.runtime_checks")
        config_module = importlib.import_module("app.core.config")
        original_settings = runtime_checks.settings
        runtime_checks.settings = config_module.Settings(rate_limit_enabled=False)
        try:
            errors = runtime_checks.validate_commercial_config_values()
        finally:
            runtime_checks.settings = original_settings

        self.assertIn("RATE_LIMIT_ENABLED must be true", errors)

    def test_admin_audit_model_and_route_exist(self) -> None:
        from app.models.admin_audit_log import AdminAuditLog

        columns = set(AdminAuditLog.__table__.columns.keys())
        self.assertIn("action", columns)
        self.assertIn("actor", columns)
        self.assertIn("request_path", columns)
        self.assertIn("ip_address", columns)
        self.assertIn("details", columns)

        routers_module = importlib.import_module("app.routers")
        routes = {route.path for route in routers_module.api_router.routes}
        self.assertIn("/admin/audit_logs", routes)


if __name__ == "__main__":
    unittest.main()
