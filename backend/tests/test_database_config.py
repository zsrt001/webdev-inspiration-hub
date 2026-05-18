"""Database URL compatibility tests."""

from pathlib import Path
import ssl
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core import database as database_config  # noqa: E402
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

    def test_supabase_placeholder_password_keeps_ssl_for_direct_host(self) -> None:
        url, connect_args = normalize_database_url(
            "postgresql://postgres:[YOUR-PASSWORD]@db.example.supabase.co:5432/postgres"
        )

        self.assertEqual(
            url,
            "postgresql+asyncpg://postgres:%5BYOUR-PASSWORD%5D@db.example.supabase.co:5432/postgres",
        )
        self.assertIsInstance(connect_args.get("ssl"), ssl.SSLContext)

    def test_vercel_supabase_direct_url_uses_configured_pooler_host(self) -> None:
        original_vercel = database_config.settings.vercel
        original_host = database_config.settings.supabase_pooler_host
        try:
            database_config.settings.vercel = "1"
            database_config.settings.supabase_pooler_host = "aws-0-us-west-1.pooler.supabase.com"

            url, connect_args = normalize_database_url(
                "postgresql://postgres:secret@db.example.supabase.co:5432/postgres?sslmode=require"
            )

            self.assertIn("@aws-0-us-west-1.pooler.supabase.com:5432/postgres", url)
            self.assertIn("postgres.example", url)
            self.assertEqual(connect_args.get("statement_cache_size"), 0)
        finally:
            database_config.settings.vercel = original_vercel
            database_config.settings.supabase_pooler_host = original_host

    def test_supabase_pooler_hosts_try_both_prefixes_for_preferred_region_first(self) -> None:
        original_host = database_config.settings.supabase_pooler_host
        original_region = database_config.settings.supabase_pooler_region
        try:
            database_config.settings.supabase_pooler_host = ""
            database_config.settings.supabase_pooler_region = "us-east-1"

            hosts = database_config._supabase_pooler_hosts()

            self.assertEqual(hosts[0], "aws-0-us-east-1.pooler.supabase.com")
            self.assertEqual(hosts[1], "aws-1-us-east-1.pooler.supabase.com")
            self.assertEqual(len(hosts), len(set(hosts)))
        finally:
            database_config.settings.supabase_pooler_host = original_host
            database_config.settings.supabase_pooler_region = original_region


if __name__ == "__main__":
    unittest.main()
