"""Database URL compatibility tests."""

from pathlib import Path
import ssl
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import normalize_database_url  # noqa: E402


class DatabaseConfigTest(unittest.TestCase):
    def test_supabase_url_is_normalized_for_asyncpg(self) -> None:
        url, connect_args = normalize_database_url(
            "postgresql://postgres.example:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
        )

        self.assertTrue(url.startswith("postgresql+asyncpg://"))
        self.assertNotIn("sslmode=", url)
        self.assertIsInstance(connect_args.get("ssl"), ssl.SSLContext)
        self.assertFalse(connect_args["ssl"].check_hostname)
        self.assertEqual(connect_args["ssl"].verify_mode, ssl.CERT_NONE)

    def test_asyncpg_url_without_sslmode_is_preserved(self) -> None:
        url, connect_args = normalize_database_url(
            "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_wedding"
        )

        self.assertEqual(url, "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_wedding")
        self.assertEqual(connect_args, {})

    def test_supabase_placeholder_password_does_not_break_url_parsing(self) -> None:
        url, connect_args = normalize_database_url(
            "postgresql://postgres:[YOUR-PASSWORD]@db.example.supabase.co:5432/postgres"
        )

        self.assertEqual(
            url,
            "postgresql+asyncpg://postgres:%5BYOUR-PASSWORD%5D@db.example.supabase.co:5432/postgres",
        )
        self.assertEqual(connect_args, {})


if __name__ == "__main__":
    unittest.main()
