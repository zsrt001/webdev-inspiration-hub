"""Security hardening contract tests."""

from pathlib import Path
import importlib
import json
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class SecurityHardeningTest(unittest.TestCase):
    def test_production_dependency_graph_excludes_unfixed_ecdsa_chain(self) -> None:
        root = BACKEND_DIR.parent
        expected_versions = (
            "fastapi==0.139.0",
            "starlette==1.3.1",
            "pillow==12.3.0",
            "PyJWT==2.13.0",
        )
        for relative_path in ("requirements.in", "backend/requirements.txt"):
            source = (root / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertNotIn("python-jose", source)
                for version in expected_versions:
                    self.assertIn(version, source)

        for relative_path in ("requirements.txt", "backend/requirements.lock.txt", "backend/requirements.windows.lock.txt"):
            lock = (root / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertNotIn("python-jose==", lock)
                self.assertNotIn("ecdsa==", lock)
                for version in expected_versions:
                    self.assertIn(version.lower(), lock.lower())

        for relative_path in (
            "backend/requirements.txt",
            "backend/requirements.lock.txt",
            "backend/requirements.windows.lock.txt",
        ):
            release_runtime = (root / relative_path).read_text(encoding="utf-8")
            with self.subTest(release_runtime=relative_path):
                self.assertIn("cryptography==49.0.0", release_runtime.lower())

        offenders: list[str] = []
        for path in (BACKEND_DIR / "app").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "from jose" in source or "import jose" in source:
                offenders.append(str(path.relative_to(root)))
        self.assertEqual([], offenders)

        deprecated_pillow_calls: list[str] = []
        for path in (BACKEND_DIR / "app" / "services").rglob("*.py"):
            if ".getdata()" in path.read_text(encoding="utf-8"):
                deprecated_pillow_calls.append(str(path.relative_to(root)))
        self.assertEqual([], deprecated_pillow_calls)

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

    def test_frontend_dev_tools_are_loopback_only_and_raw_html_sinks_are_absent(self) -> None:
        frontend_root = BACKEND_DIR.parent / "frontend"
        package = json.loads((frontend_root / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["scripts"]["dev:web"], "uni -p h5 --host 127.0.0.1")
        self.assertEqual(
            package["scripts"]["preview:web"],
            "vite preview --outDir dist/build/h5 --host 127.0.0.1 --port 4173",
        )
        vite_config = (frontend_root / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("host: '127.0.0.1'", vite_config)

        forbidden_sinks = (
            "v-html",
            "innerHTML",
            "outerHTML",
            "document.write(",
            "eval(",
            "new Function(",
        )
        offenders: list[str] = []
        for path in (frontend_root / "src").rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".vue", ".ts", ".js"}:
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            for sink in forbidden_sinks:
                if sink in source:
                    offenders.append(
                        f"{path.relative_to(BACKEND_DIR.parent)} contains {sink}"
                    )
        self.assertEqual([], offenders)

    def test_frontend_build_tool_advisories_have_enforced_boundaries(self) -> None:
        root = BACKEND_DIR.parent
        frontend_root = root / "frontend"
        package = json.loads((frontend_root / "package.json").read_text(encoding="utf-8"))
        vite_config = (frontend_root / "vite.config.ts").read_text(encoding="utf-8")
        ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertNotIn("vite", package["dependencies"])
        self.assertEqual("5.2.8", package["devDependencies"]["vite"])
        self.assertEqual("7.29.6", package["overrides"]["@babel/core"])
        self.assertIn("FORBIDDEN_BROWSER_CODE_PATTERNS", vite_config)
        self.assertIn("'document.currentScript'", vite_config)
        self.assertIn("npm audit --omit=dev --audit-level=low", ci)

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

    def test_commercial_config_rejects_short_access_signing_key(self) -> None:
        runtime_checks = importlib.import_module("app.core.runtime_checks")
        config_module = importlib.import_module("app.core.config")
        original_settings = runtime_checks.settings
        runtime_checks.settings = config_module.Settings(secret_key="too-short")
        try:
            errors = runtime_checks.validate_commercial_config_values()
        finally:
            runtime_checks.settings = original_settings

        self.assertIn("SECRET_KEY must contain at least 32 bytes", errors)

    def test_storage_config_rejects_vercel_s3_loopback_storage_without_blob(self) -> None:
        runtime_checks = importlib.import_module("app.core.runtime_checks")
        config_module = importlib.import_module("app.core.config")
        original_settings = runtime_checks.settings
        runtime_checks.settings = config_module.Settings(
            vercel="1",
            storage_provider="s3",
            aws_s3_endpoint="http://127.0.0.1:9000",
            blob_read_write_token="",
        )
        try:
            with self.assertRaises(RuntimeError) as ctx:
                runtime_checks._check_storage_config()
        finally:
            runtime_checks.settings = original_settings

        self.assertIn("AWS_S3_ENDPOINT points to local storage", str(ctx.exception))

    def test_private_storage_rejects_local_provider_outside_debug(self) -> None:
        storage_module = importlib.import_module("app.services.storage")
        config_module = importlib.import_module("app.core.config")
        original_settings = storage_module.settings
        storage_module.settings = config_module.Settings(debug=False, storage_provider="local")
        try:
            with self.assertRaises(Exception) as ctx:
                storage_module.storage_service.put_private(
                    "security-test/test.txt",
                    b"test",
                    "text/plain",
                )
        finally:
            storage_module.settings = original_settings

        self.assertIn("local private storage is debug-only", str(ctx.exception))

    def test_commercial_config_does_not_require_static_browser_admin_credentials(self) -> None:
        runtime_checks = importlib.import_module("app.core.runtime_checks")
        config_module = importlib.import_module("app.core.config")
        original_settings = runtime_checks.settings
        candidate = config_module.Settings(admin_token="")
        runtime_checks.settings = candidate
        try:
            errors = runtime_checks.validate_commercial_config_values()
        finally:
            runtime_checks.settings = original_settings

        self.assertFalse(hasattr(candidate, "admin_user_ids"))
        self.assertFalse(hasattr(candidate, "admin_emails"))
        self.assertFalse(any("ADMIN_" in error for error in errors), errors)

    def test_admin_audit_model_and_route_exist(self) -> None:
        from app.models.admin_audit_log import AdminAuditLog

        columns = set(AdminAuditLog.__table__.columns.keys())
        self.assertIn("action", columns)
        self.assertIn("actor", columns)
        self.assertIn("request_path", columns)
        self.assertIn("ip_address", columns)
        self.assertIn("details", columns)

        routers_module = importlib.import_module("app.routers")
        from tests.route_contract import effective_paths

        routes = effective_paths(routers_module.api_router)
        self.assertIn("/admin/audit_logs", routes)


if __name__ == "__main__":
    unittest.main()
