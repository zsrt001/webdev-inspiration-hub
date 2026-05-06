"""Security hardening contract tests."""

from pathlib import Path
import importlib
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class SecurityHardeningTest(unittest.TestCase):
    def test_frontend_does_not_embed_admin_or_provider_keys(self) -> None:
        frontend_root = BACKEND_DIR.parent / "frontend"
        text_suffixes = {".ts", ".vue", ".js", ".json", ".scss", ".css", ".html", ".md", ".env", ".example", ".local"}
        forbidden = (
            "ADMIN_TOKEN",
            "X-Admin-Token",
            "admin_token",
            "VITE_SUPABASE_ANON_KEY",
            "VITE_SUPABASE_URL",
            "VITE_WENWEN",
            "VITE_CREEM",
            "VITE_COMFY",
            "VITE_OPENAI",
            "VITE_JIEKOU",
        )
        offenders: list[str] = []
        for path in [frontend_root / ".env.example", frontend_root / ".env.local"]:
            candidates = [path] if path.exists() else []
            if path.is_dir():
                candidates = list(path.rglob("*"))
            for candidate in candidates:
                if not candidate.is_file() or candidate.suffix.lower() not in text_suffixes:
                    continue
                text = candidate.read_text(encoding="utf-8", errors="ignore")
                for needle in forbidden:
                    if needle.lower() in text.lower():
                        offenders.append(f"{candidate.relative_to(BACKEND_DIR.parent)} contains {needle}")
        for path in (frontend_root / "src").rglob("*"):
            if not path.is_file() or path.suffix.lower() not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for needle in forbidden:
                if needle.lower() in text.lower():
                    offenders.append(f"{path.relative_to(BACKEND_DIR.parent)} contains {needle}")

        self.assertEqual([], offenders)

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

    def test_commercial_config_allows_admin_identity_without_frontend_token(self) -> None:
        runtime_checks = importlib.import_module("app.core.runtime_checks")
        config_module = importlib.import_module("app.core.config")
        original_settings = runtime_checks.settings
        runtime_checks.settings = config_module.Settings(admin_token="", admin_user_ids="00000000-0000-0000-0000-000000000000")
        try:
            errors = runtime_checks.validate_commercial_config_values()
        finally:
            runtime_checks.settings = original_settings

        self.assertNotIn("ADMIN_TOKEN is required", errors)
        self.assertNotIn("ADMIN_USER_IDS, ADMIN_EMAILS, or backend-only ADMIN_TOKEN is required", errors)

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
