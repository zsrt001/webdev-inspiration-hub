"""Route and worker adoption contracts for PostgreSQL feature flags."""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"


class FeatureFlagRouteSourceTest(unittest.TestCase):
    def test_no_active_bootstrap_guard_call_remains(self) -> None:
        offenders: list[str] = []
        for path in (APP / "routers").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"require_bootstrap_capability", "bootstrap_capability_enabled"}
                for node in ast.walk(tree)
            ):
                offenders.append(str(path.relative_to(ROOT)))
        worker = APP / "worker_tasks.py"
        worker_tree = ast.parse(worker.read_text(encoding="utf-8"), filename=str(worker))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"require_bootstrap_capability", "bootstrap_capability_enabled"}
            for node in ast.walk(worker_tree)
        ):
            offenders.append(str(worker.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_mutating_routes_use_authoritative_request_dependency(self) -> None:
        expected = {
            "routers/auth/google.py",
            "routers/media.py",
            "routers/gatekeeper.py",
            "routers/orders.py",
            "routers/payments.py",
            "routers/subscriptions.py",
            "routers/admin.py",
            "routers/ops.py",
        }
        missing = []
        for relative in expected:
            source = (APP / relative).read_text(encoding="utf-8")
            if "require_request_capability" not in source:
                missing.append(relative)
        self.assertEqual(missing, [])

    def test_anonymous_partner_session_is_centralized_and_permanently_retired(self) -> None:
        self.assertFalse((APP / "routers" / "session.py").exists())
        source = (APP / "routers" / "retired.py").read_text(encoding="utf-8")
        self.assertIn('"/session/create"', source)
        self.assertIn('"/session/{session_id}/bind_order"', source)
        self.assertIn("partner_session_retired", source)

    def test_admin_mutations_use_the_separate_control_plane_connection(self) -> None:
        source = (APP / "routers" / "ops_admin.py").read_text(encoding="utf-8")
        self.assertIn("get_control_plane_db", source)
        for function_name in ("mutate_feature_flag", "emergency_disable_feature_flag"):
            tree = ast.parse(source)
            function = next(
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name
            )
            segment = ast.get_source_segment(source, function) or ""
            self.assertIn("Depends(get_control_plane_db)", segment)

    def test_formal_verifier_matrix_covers_every_stage1_blocked_and_retired_route(self) -> None:
        script = ROOT / "scripts" / "release" / "verify_safe_baseline.py"
        spec = importlib.util.spec_from_file_location("verify_safe_baseline_route_matrix", script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        identifier = "00000000-0000-0000-0000-000000000000"
        expected_blocked = {
            ("POST", "/api/v1/auth/oauth-intents"),
            ("POST", "/api/v1/auth/supabase/session"),
            ("POST", "/api/v1/media/uploads"),
            ("POST", "/api/v1/gatekeeper/check"),
            ("POST", "/api/v1/orders/create"),
            ("DELETE", f"/api/v1/orders/{identifier}"),
            ("GET", "/api/v1/credits/packages"),
            ("POST", "/api/v1/payments/checkout"),
            ("POST", "/api/v1/subscriptions/checkout"),
            ("POST", "/api/v1/subscriptions/cancel"),
            ("GET", "/api/v1/admin/creem_product_check"),
            ("POST", "/api/v1/admin/creem_checkout_probe"),
            ("POST", "/api/v1/admin/generation_probe"),
            ("POST", "/api/v1/admin/grant_credits"),
            ("POST", f"/api/v1/admin/orders/{identifier}/regenerate"),
            ("POST", "/api/v1/admin/cleanup_expired_assets"),
            ("POST", "/api/v1/ops/poll_pending_orders"),
        }
        observed_blocked = {
            (probe.method, probe.path) for probe in module.GUARDED_ROUTE_PROBES
        }
        self.assertEqual(observed_blocked, expected_blocked)

        expected_retired = {
            ("GET", "/api/v1/payments/manual/checkout"),
            ("POST", "/api/v1/payments/manual/submit"),
            ("POST", "/api/v1/payments/manual/admin/complete"),
            ("POST", "/api/v1/payments/manual/admin/fail"),
            ("POST", "/api/v1/auth/login"),
            ("POST", "/api/v1/upload"),
            ("POST", "/api/v1/upload/multiple"),
            ("POST", "/api/v1/upload/delete"),
            ("POST", "/api/v1/session/create"),
            ("GET", "/api/v1/session/safe-baseline-probe/status"),
            ("POST", "/api/v1/session/safe-baseline-probe/upload/host"),
            ("POST", "/api/v1/session/safe-baseline-probe/upload/guest"),
            ("GET", "/api/v1/session/safe-baseline-probe/images"),
            ("GET", "/api/v1/session/safe-baseline-probe/share_meta"),
            ("POST", "/api/v1/session/safe-baseline-probe/processing"),
            ("POST", "/api/v1/session/safe-baseline-probe/complete"),
            ("POST", "/api/v1/session/safe-baseline-probe/bind_order"),
            ("POST", "/api/v1/users/"),
            ("GET", f"/api/v1/users/{identifier}"),
            ("PATCH", f"/api/v1/users/{identifier}"),
            ("POST", "/api/v1/credits/purchase"),
            ("POST", "/api/v1/credits/deduct"),
            ("POST", "/api/v1/credits/add"),
            ("POST", "/api/v1/live_portrait/generate"),
            ("GET", "/api/v1/live_portrait/list"),
            ("GET", f"/api/v1/live_portrait/{identifier}"),
            ("POST", "/api/v1/leads/submit"),
            ("GET", "/api/v1/leads/list"),
            ("GET", "/api/v1/leads/export.csv"),
            ("GET", "/api/v1/recommendations/local_studios"),
            ("GET", "/api/v1/admin/crm_preview"),
            ("POST", "/api/v1/admin/crm_push"),
            ("GET", "/api/v1/admin/crm_push_history"),
        }
        observed_retired = {
            (probe.method, probe.path) for probe in module.RETIRED_ROUTE_PROBES
        }
        self.assertEqual(observed_retired, expected_retired)

    def test_signed_webhook_logout_and_retired_reference_routes_are_not_flagged(self) -> None:
        payments = (APP / "routers" / "payments.py").read_text(encoding="utf-8")
        google = (APP / "routers" / "auth" / "google.py").read_text(encoding="utf-8")
        users = (APP / "routers" / "users.py").read_text(encoding="utf-8")
        self.assertNotIn("require_request_capability", ast.get_source_segment(payments, next(
            node for node in ast.walk(ast.parse(payments))
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "creem_webhook"
        )) or "")
        self.assertNotIn("require_request_capability", google[google.find("logout"):])
        self.assertNotIn("require_request_capability", users)


class FeatureFlagDependencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_google_exchange_fails_closed_before_identity_schema_access(self) -> None:
        google = importlib.import_module("app.routers.auth.google")
        payload = importlib.import_module("app.schemas.auth").SupabaseSessionRequest(
            access_token="x" * 16,
            intent_token="i" * 32,
        )
        request = SimpleNamespace(cookies={})
        response = SimpleNamespace()
        database = AsyncMock()
        disabled = SimpleNamespace(allowed=False, reason="global_off")
        with (
            patch.object(google, "require_request_origin", new=AsyncMock()),
            patch.object(
                google,
                "resolve_request_capability",
                new=AsyncMock(return_value=disabled),
            ) as resolve,
            patch.object(
                google,
                "consume_oauth_intent",
                new=AsyncMock(),
            ) as consume,
            self.assertRaises(HTTPException) as denied,
        ):
            await google.exchange_supabase_session(
                payload,
                request,
                response,
                database,
            )

        self.assertEqual(denied.exception.status_code, 503)
        self.assertEqual(denied.exception.detail["code"], "capability_disabled")
        resolve.assert_awaited_once_with(database, google.Capability.GOOGLE_AUTH)
        consume.assert_not_awaited()

    async def test_browser_admin_requires_active_database_role_on_cookie_identity(self) -> None:
        admin_auth = importlib.import_module("app.core.admin_auth")
        request = SimpleNamespace(state=SimpleNamespace())
        active_admin = SimpleNamespace(id=__import__("uuid").uuid4(), role="admin", status="active")
        resolved = await admin_auth.require_admin_user(request, active_admin)
        self.assertIs(resolved, active_admin)
        self.assertEqual(request.state.admin_actor, f"admin-user:{active_admin.id}")

        for user in (
            SimpleNamespace(id=__import__("uuid").uuid4(), role="user", status="active"),
            SimpleNamespace(id=__import__("uuid").uuid4(), role="admin", status="suspended"),
        ):
            with self.subTest(role=user.role, status=user.status), self.assertRaises(HTTPException) as denied:
                await admin_auth.require_admin_user(SimpleNamespace(state=SimpleNamespace()), user)
            self.assertEqual(denied.exception.status_code, 403)

    async def test_request_guard_returns_structured_503_on_database_failure(self) -> None:
        flags = importlib.import_module("app.core.feature_flags")
        service = importlib.import_module("app.services.feature_flag_service")
        request = SimpleNamespace(headers={}, state=SimpleNamespace())
        with patch.object(
            service,
            "resolve_capability",
            new=AsyncMock(side_effect=RuntimeError("database unavailable")),
        ):
            with self.assertRaises(HTTPException) as raised:
                await service.require_request_capability(
                    request,
                    AsyncMock(),
                    flags.Capability.GENERATION,
                    verified_user_id=None,
                )
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "capability_disabled")

    async def test_worker_rejects_missing_server_stamps_before_task16(self) -> None:
        flags = importlib.import_module("app.core.feature_flags")
        service = importlib.import_module("app.services.feature_flag_service")
        with self.assertRaises(service.CapabilityDisabled):
            await service.require_worker_capability(
                AsyncMock(),
                flags.Capability.GENERATION,
                deployment_id=None,
                runtime_bundle_id=None,
                worker_image_digest=None,
                user_id=None,
            )

    async def test_worker_rejects_spoofed_coordinates_without_database_access(self) -> None:
        flags = importlib.import_module("app.core.feature_flags")
        service = importlib.import_module("app.services.feature_flag_service")
        db = AsyncMock()
        with patch.multiple(
            service.settings,
            runtime_environment="production",
            vercel_deployment_id="dpl_system",
            runtime_bundle_id="rtb_" + "a" * 64,
            worker_image_digest="sha256:" + "b" * 64,
            acceptance_identity_hmac_key="k" * 32,
        ):
            with self.assertRaises(service.CapabilityDisabled):
                await service.require_worker_capability(
                    db,
                    flags.Capability.GENERATION,
                    deployment_id="dpl_spoofed",
                    runtime_bundle_id="rtb_" + "a" * 64,
                    worker_image_digest="sha256:" + "b" * 64,
                    user_id=__import__("uuid").uuid4(),
                )
        db.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
