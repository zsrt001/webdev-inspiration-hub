"""Supabase RLS migration contract tests."""

from pathlib import Path
import unittest


ROOT_DIR = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT_DIR / "backend" / "alembic" / "versions" / "20260426_0003_supabase_rls_policies.py"


class SupabaseRlsMigrationTest(unittest.TestCase):
    def test_rls_migration_protects_sensitive_saas_tables(self) -> None:
        self.assertTrue(MIGRATION_PATH.exists())
        sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()

        for table_name in (
            "users",
            "user_credits",
            "credit_transactions",
            "credit_purchases",
            "orders",
            "live_portrait_jobs",
            "leads",
        ):
            self.assertIn(f"public.{table_name}", sql)

        self.assertIn("enable row level security", sql)
        self.assertIn("auth.uid", sql)
        self.assertIn("to authenticated", sql)
        self.assertIn("app_current_user_id", sql)
        self.assertIn("referral", sql)
        self.assertIn("subscription", sql)
        self.assertNotIn("credit_card", sql)


if __name__ == "__main__":
    unittest.main()
