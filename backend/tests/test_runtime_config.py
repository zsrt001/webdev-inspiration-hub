"""Runtime configuration behavior tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402


class RuntimeConfigTest(unittest.TestCase):
    def test_development_allows_schema_auto_create_by_default(self) -> None:
        settings = Settings(debug=True, auto_create_tables=None)

        self.assertTrue(settings.should_auto_create_tables)

    def test_production_disables_schema_auto_create_by_default(self) -> None:
        settings = Settings(debug=False, auto_create_tables=None)

        self.assertFalse(settings.should_auto_create_tables)

    def test_explicit_auto_create_tables_override_is_respected(self) -> None:
        settings = Settings(debug=False, auto_create_tables=True)

        self.assertTrue(settings.should_auto_create_tables)

    def test_vercel_uses_inline_generation_by_default(self) -> None:
        settings = Settings(vercel="1", task_execution_mode="auto")

        self.assertEqual(settings.generation_execution_mode, "inline")

    def test_cleanup_cron_token_accepts_vercel_cron_secret_alias(self) -> None:
        settings = Settings(cleanup_cron_token="", cron_secret="vercel-cron-secret")

        self.assertEqual(settings.effective_cleanup_cron_token, "vercel-cron-secret")


if __name__ == "__main__":
    unittest.main()
