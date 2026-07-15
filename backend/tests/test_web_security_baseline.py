"""Exact-origin, transport, browser Admin, and zero-bearer Web security contracts."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
import ssl
import sys
from types import SimpleNamespace
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent
APP = BACKEND_DIR / "app"
FRONTEND = ROOT / "frontend" / "src"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core import database as database_config  # noqa: E402
from app.core.security_headers import (  # noqa: E402
    CONTENT_SECURITY_POLICY,
    CORS_HEADERS,
    CORS_METHODS,
    SECURITY_HEADERS,
    apply_web_security_headers,
    is_allowed_web_origin,
    normalize_origin,
    validate_preflight_request,
)
from fastapi.responses import Response  # noqa: E402


class WebSecurityBaselineTest(unittest.TestCase):
    def test_supabase_database_tls_verifies_ca_and_hostname(self) -> None:
        _url, connect_args = database_config.normalize_database_url(
            "postgresql://postgres.example:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
        )
        context = connect_args["ssl"]
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_origin_normalization_is_exact_and_rejects_confusable_values(self) -> None:
        self.assertEqual(normalize_origin("https://www.vowpic.com"), "https://www.vowpic.com")
        self.assertEqual(normalize_origin("https://www.vowpic.com:443"), "https://www.vowpic.com")
        confusable = normalize_origin("https://www.vowpic.com.evil.example")
        self.assertEqual(confusable, "https://www.vowpic.com.evil.example")
        settings = SimpleNamespace(
            effective_frontend_base_url="https://www.vowpic.com",
            runtime_environment="production",
            cors_origins=[],
            is_vercel_runtime=True,
            deployment_id="dpl_test",
            runtime_bundle_id="rtb_" + "a" * 64,
            vercel_url="www.vowpic.com.evil.example",
        )
        self.assertFalse(
            asyncio.run(is_allowed_web_origin(confusable, settings_obj=settings))
        )
        for origin in (
            "https://user@www.vowpic.com",
            "https://www.vowpic.com/path",
            "//www.vowpic.com",
            "https://*.vercel.app",
            "null",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                normalize_origin(origin)

    def test_security_headers_meet_minimum_browser_contract(self) -> None:
        self.assertEqual(SECURITY_HEADERS["Strict-Transport-Security"], "max-age=31536000; includeSubDomains")
        self.assertEqual(SECURITY_HEADERS["X-Content-Type-Options"], "nosniff")
        self.assertEqual(SECURITY_HEADERS["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("camera=()", SECURITY_HEADERS["Permissions-Policy"])
        for directive in (
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "connect-src 'self' https://*.supabase.co",
            "upgrade-insecure-requests",
        ):
            self.assertIn(directive, CONTENT_SECURITY_POLICY)
        response = Response()
        apply_web_security_headers(response)
        for name, value in SECURITY_HEADERS.items():
            self.assertEqual(response.headers[name], value)
        vercel_config = (ROOT / "vercel.json").read_text(encoding="utf-8")
        edge_middleware = (ROOT / "middleware.ts").read_text(encoding="utf-8")
        for value in SECURITY_HEADERS.values():
            self.assertIn(value, vercel_config)
            self.assertIn(value, edge_middleware)
        self.assertNotIn("Access-Control-Allow-Origin", vercel_config)
        self.assertNotIn("Content-Type, Authorization", vercel_config)

    def test_cors_preflight_allows_only_explicit_methods_and_headers(self) -> None:
        validate_preflight_request(
            "POST",
            "Content-Type, X-CSRF-Token, X-Request-ID, Idempotency-Key",
        )
        self.assertIn("Idempotency-Key", CORS_HEADERS)
        self.assertNotIn("*", CORS_METHODS)
        self.assertNotIn("*", CORS_HEADERS)
        with self.assertRaises(ValueError):
            validate_preflight_request("TRACE", "Content-Type")
        with self.assertRaises(ValueError):
            validate_preflight_request("POST", "Authorization")
        main_source = (APP / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("CORSMiddleware", main_source)
        self.assertNotIn('allow_methods=["*"]', main_source)

    def test_browser_admin_uses_cookie_session_and_database_role_only(self) -> None:
        source = (APP / "core" / "admin_auth.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        browser_guard = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "require_admin_user"
        )
        segment = ast.get_source_segment(source, browser_guard) or ""
        self.assertIn("get_session_user", segment)
        self.assertNotIn("X-Admin-Token", segment)
        self.assertNotIn("admin_email", segment)
        self.assertNotIn("admin_user_id", segment)

        admin_router = (APP / "routers" / "admin.py").read_text(encoding="utf-8")
        self.assertIn("Depends(require_admin_user)", admin_router)
        self.assertNotIn("Depends(require_admin_token)", admin_router)
        audit_source = (APP / "services" / "admin_audit_service.py").read_text(encoding="utf-8")
        self.assertNotIn('request.headers.get("x-admin-token")', audit_source)
        self.assertNotIn('request.headers.get("authorization")', audit_source)
        self.assertNotIn("get_unverified_claims", audit_source)
        for relative in ("pages/admin/index.vue", "pages/admin/users.vue"):
            frontend_source = (ROOT / "frontend" / "src" / relative).read_text(encoding="utf-8")
            self.assertNotIn("ADMIN_EMAILS", frontend_source, relative)
            self.assertNotIn("ADMIN_USER_IDS", frontend_source, relative)

        admin_overview = (FRONTEND / "pages" / "admin" / "index.vue").read_text(encoding="utf-8")
        self.assertIn("await loadAdminMe();", admin_overview)
        self.assertNotIn("Promise.all([loadAdminMe()", admin_overview)

    def test_runtime_request_paths_contain_no_ddl_writer(self) -> None:
        needles = (
            "Base.metadata.create_all",
            "CREATE TABLE",
            "ALTER TABLE",
            "CREATE INDEX",
            "ensure_credit_guardrails(",
            "ensure_generation_refund_guardrails(",
        )
        offenders: list[str] = []
        for folder in (APP / "routers", APP / "services"):
            for path in folder.rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                for needle in needles:
                    if needle in source:
                        offenders.append(f"{path.relative_to(ROOT)}:{needle}")
        self.assertEqual(offenders, [])

    def test_frontend_has_pkce_and_no_persistent_bearer_path(self) -> None:
        auth_source = (FRONTEND / "services" / "auth.ts").read_text(encoding="utf-8")
        supabase_source = (FRONTEND / "utils" / "supabase.ts").read_text(encoding="utf-8")
        callback_source = (FRONTEND / "pages" / "auth" / "callback.vue").read_text(encoding="utf-8")
        combined = "\n".join([auth_source, supabase_source, callback_source])
        self.assertIn("flowType: 'pkce'", supabase_source)
        self.assertIn("persistSession: false", supabase_source)
        self.assertIn("autoRefreshToken: false", supabase_source)
        self.assertIn("detectSessionInUrl: false", supabase_source)
        self.assertIn("SUPABASE_PKCE_STORAGE_KEY", supabase_source)
        self.assertIn("storageKey: SUPABASE_PKCE_STORAGE_KEY", supabase_source)
        self.assertIn("export function clearSupabaseTransientStorage", supabase_source)
        for suffix in ("", "-code-verifier", "-user"):
            self.assertIn(
                f"window.sessionStorage.removeItem(`${{SUPABASE_PKCE_STORAGE_KEY}}{suffix}`)",
                supabase_source,
            )
        self.assertIn("exchangeCodeForSession", combined)
        self.assertIn("sessionStorage", combined)
        self.assertIn("new URLSearchParams(window.location.hash.slice(1))", auth_source)
        self.assertIn("fragment.has('access_token')", auth_source)
        self.assertIn("function clearOAuthAttemptState", auth_source)
        self.assertRegex(
            auth_source,
            r"catch \(error\) \{\s*clearOAuthAttemptState\(\);\s*discardSupabaseClient\(\);",
        )
        self.assertRegex(
            auth_source,
            r"finally \{\s*discardSupabaseClient\(\);\s*clearCallbackState\(\);",
        )

        forbidden: list[str] = []
        for path in FRONTEND.rglob("*"):
            if not path.is_file() or path.suffix not in {".ts", ".vue", ".js"}:
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            for needle in (
                "ai_wedding_token",
                "Authorization = `Bearer",
                "headers.Authorization",
                "localStorage.setItem('access_token",
                'localStorage.setItem("access_token',
            ):
                if needle in source:
                    forbidden.append(f"{path.relative_to(ROOT)}:{needle}")
        self.assertEqual(forbidden, [])

    def test_mutating_ops_are_post_only_and_edge_errors_use_the_public_shape(self) -> None:
        ops_source = (APP / "routers" / "ops.py").read_text(encoding="utf-8")
        self.assertNotIn('@router.get("/check_alerts")', ops_source)
        self.assertNotIn('@router.get("/poll_pending_orders")', ops_source)
        self.assertIn('@router.post("/check_alerts")', ops_source)
        self.assertIn('@router.post("/poll_pending_orders")', ops_source)
        vercel_config = (ROOT / "vercel.json").read_text(encoding="utf-8")
        self.assertNotIn('"crons"', vercel_config)

        edge_middleware = (ROOT / "middleware.ts").read_text(encoding="utf-8")
        for field in ("code", "message", "request_id", "retryable", "field_errors"):
            self.assertIn(f"{field}:", edge_middleware)
        self.assertNotIn('JSON.stringify({ error: "rate_limit_exceeded" })', edge_middleware)

    def test_api_transport_uses_credentials_and_csrf_without_authorization(self) -> None:
        source = (FRONTEND / "services" / "http.ts").read_text(encoding="utf-8")
        self.assertIn("credentials: 'include'", source)
        self.assertIn("X-CSRF-Token", source)
        self.assertIn("Authorization headers are forbidden", source)
        self.assertNotIn("Bearer ${", source)

    def test_frontend_csp_has_no_runtime_remote_font_model_or_placeholder_dependency(self) -> None:
        forbidden = ("fonts.googleapis.com", "cdn.jsdelivr.net", "github.io/face-api", "placehold.co")
        offenders = []
        for path in FRONTEND.rglob("*"):
            if not path.is_file() or path.suffix not in {".ts", ".vue", ".scss", ".css"}:
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            for needle in forbidden:
                if needle in source:
                    offenders.append(f"{path.relative_to(ROOT)}:{needle}")
        self.assertEqual(offenders, [])

    def test_web_build_removes_uni_remote_preload_and_rejects_remote_assets(self) -> None:
        vite_config = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
        app_styles = (FRONTEND / "App.vue").read_text(encoding="utf-8")
        package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
        clean_script_path = ROOT / "frontend" / "scripts" / "clean-web-output.mjs"
        self.assertIn("vowpic-web-asset-policy", vite_config)
        self.assertIn("style/framework/shadow.css", vite_config)
        self.assertIn("cdn.dcloud.net.cn", vite_config)
        self.assertIn("fonts.googleapis.com", vite_config)
        self.assertIn("fonts.gstatic.com", vite_config)
        self.assertEqual(
            package["scripts"]["build:web"],
            "node scripts/clean-web-output.mjs && uni build -p h5",
        )
        clean_script = clean_script_path.read_text(encoding="utf-8")
        self.assertIn("readdirSync", clean_script)
        self.assertIn("unlinkSync", clean_script)
        self.assertIn("rmdirSync", clean_script)
        self.assertIn("existsSync", clean_script)
        self.assertIn("'dist', 'build', 'h5'", clean_script)
        self.assertIn("body::after", app_styles)
        self.assertIn("animation: none !important", app_styles)
        for relative in (
            "frontend/src/static/hero_wedding_luxury_bg.jpg",
            "frontend/src/static/legacy_promo_banner.jpg",
            "frontend/src/static/README.md",
            "frontend/src/static/icons/home.svg",
            "frontend/src/static/icons/home-active.svg",
            "frontend/src/static/icons/order.svg",
            "frontend/src/static/icons/order-active.svg",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_web_metadata_defaults_to_overseas_vowpic_brand(self) -> None:
        uni_manifest = json.loads((FRONTEND / "manifest.json").read_text(encoding="utf-8"))
        pwa_manifest = json.loads(
            (ROOT / "frontend/public/manifest.webmanifest").read_text(encoding="utf-8")
        )
        index_html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        offline_html = (ROOT / "frontend/public/offline.html").read_text(encoding="utf-8")
        icon = (ROOT / "frontend/public/icons/pwa-icon.svg").read_text(encoding="utf-8")
        service_worker = (ROOT / "frontend/public/sw.js").read_text(encoding="utf-8")

        self.assertEqual(uni_manifest["name"], "VowPic")
        self.assertIn("Web SaaS", uni_manifest["description"])
        self.assertEqual(uni_manifest["h5"]["title"], "VowPic")
        self.assertEqual(pwa_manifest["name"], "VowPic")
        self.assertEqual(pwa_manifest["short_name"], "VowPic")
        self.assertEqual(pwa_manifest["lang"], "en")
        for html in (index_html, offline_html):
            self.assertIn('<html lang="en">', html)
            self.assertIn("VowPic", html)
        self.assertIn('aria-label="VowPic"', icon)
        self.assertIn("const CACHE_VERSION = 'vowpic-pwa-v2';", service_worker)
        self.assertNotIn("ai-wedding-pwa-v1", service_worker)


if __name__ == "__main__":
    unittest.main()
