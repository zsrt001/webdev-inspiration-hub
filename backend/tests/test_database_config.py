"""Database URL compatibility tests."""

import hashlib
import os
from pathlib import Path
import ssl
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch


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
        self.assertTrue(connect_args["ssl"].check_hostname)
        self.assertEqual(connect_args["ssl"].verify_mode, ssl.CERT_REQUIRED)

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

    def test_asyncpg_context_loads_the_scoped_postgres_root_certificate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_cert = Path(temp_dir) / "database-root.crt"
            root_cert.write_text("test certificate placeholder", encoding="utf-8")
            context = Mock()
            context.verify_flags = int(getattr(ssl, "VERIFY_X509_STRICT", 0))
            with (
                patch.dict(os.environ, {"PGSSLROOTCERT": str(root_cert)}),
                patch.object(database_config.ssl, "create_default_context", return_value=context),
            ):
                _url, connect_args = normalize_database_url(
                    "postgresql://postgres.example:secret@aws-1-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
                )

        self.assertIs(connect_args["ssl"], context)
        context.load_verify_locations.assert_called_once_with(cafile=str(root_cert))
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            self.assertTrue(context.verify_flags & ssl.VERIFY_X509_STRICT)

    def test_supabase_context_loads_the_bundled_root_when_runtime_path_is_unset(self) -> None:
        root_cert = database_config._BUNDLED_SUPABASE_ROOT_CERT
        context = Mock()
        context.verify_flags = int(getattr(ssl, "VERIFY_X509_STRICT", 0))
        with (
            patch.dict(os.environ, {"PGSSLROOTCERT": ""}),
            patch.object(database_config.ssl, "create_default_context", return_value=context),
        ):
            _url, connect_args = normalize_database_url(
                "postgresql://postgres.example:secret@aws-1-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
            )

        self.assertIs(connect_args["ssl"], context)
        context.load_verify_locations.assert_called_once_with(cafile=str(root_cert))
        self.assertEqual(
            hashlib.sha256(root_cert.read_bytes()).hexdigest(),
            "700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7",
        )
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            self.assertFalse(context.verify_flags & ssl.VERIFY_X509_STRICT)

    def test_non_supabase_context_does_not_trust_the_bundled_supabase_root(self) -> None:
        context = Mock()
        with (
            patch.dict(os.environ, {"PGSSLROOTCERT": ""}),
            patch.object(database_config.ssl, "create_default_context", return_value=context),
        ):
            _url, connect_args = normalize_database_url(
                "postgresql://app:secret@postgres.example.com:5432/app?sslmode=require"
            )

        self.assertIs(connect_args["ssl"], context)
        context.load_verify_locations.assert_not_called()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_missing_scoped_postgres_root_certificate_fails_closed(self) -> None:
        with (
            patch.dict(os.environ, {"PGSSLROOTCERT": "missing-database-root.crt"}),
            self.assertRaisesRegex(RuntimeError, "existing certificate file"),
        ):
            normalize_database_url(
                "postgresql://postgres.example:secret@aws-1-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
            )

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
