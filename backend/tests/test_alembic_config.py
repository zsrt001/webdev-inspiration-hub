"""Alembic migration configuration tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class AlembicConfigTest(unittest.TestCase):
    def test_alembic_files_exist(self) -> None:
        self.assertTrue((BACKEND_DIR / "alembic.ini").is_file())
        self.assertTrue((BACKEND_DIR / "alembic" / "env.py").is_file())
        versions = list((BACKEND_DIR / "alembic" / "versions").glob("*.py"))
        self.assertTrue(versions)

    def test_alembic_ini_does_not_embed_database_secret(self) -> None:
        contents = (BACKEND_DIR / "alembic.ini").read_text(encoding="utf-8")

        self.assertNotIn("your-password@", contents)
        self.assertNotIn("supabase.co", contents)
        self.assertIn("script_location = alembic", contents)
        self.assertIn("path_separator = os", contents)

    def test_migration_runner_exists(self) -> None:
        runner = BACKEND_DIR / "scripts" / "migrate_db.py"

        self.assertTrue(runner.is_file())

    def test_task6_identity_migration_follows_current_head(self) -> None:
        plan = (
            REPO_ROOT
            / "docs"
            / "superpowers"
            / "plans"
            / "2026-07-10-vowpic-commercial-closure-implementation.md"
        ).read_text(encoding="utf-8")

        self.assertIn('down_revision = "20260712_0014"', plan)


if __name__ == "__main__":
    unittest.main()
