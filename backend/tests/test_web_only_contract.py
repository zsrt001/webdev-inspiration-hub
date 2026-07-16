"""Web-only product contract and retired-route compatibility tests."""

from __future__ import annotations

import json
from pathlib import Path
import unittest
import uuid

import httpx


ROOT = Path(__file__).resolve().parents[2]

RETIRED_ROUTES = (
    ("POST", "/api/v1/auth/login", "/api/v1/auth/login", "auth_method_retired", {"code": "retired"}),
    ("POST", "/api/v1/users/", "/api/v1/users/", "legacy_user_route_retired", {"openid": "retired"}),
    (
        "GET",
        "/api/v1/users/{user_id}",
        f"/api/v1/users/{uuid.UUID(int=0)}",
        "legacy_user_route_retired",
        None,
    ),
    (
        "PATCH",
        "/api/v1/users/{user_id}",
        f"/api/v1/users/{uuid.UUID(int=0)}",
        "legacy_user_route_retired",
        {"nickname": "retired"},
    ),
    (
        "POST",
        "/api/v1/credits/purchase",
        "/api/v1/credits/purchase",
        "legacy_credit_mutation_retired",
        {"package_id": "retired"},
    ),
    ("POST", "/api/v1/credits/deduct", "/api/v1/credits/deduct", "legacy_credit_mutation_retired", None),
    (
        "POST",
        "/api/v1/credits/add",
        "/api/v1/credits/add",
        "legacy_credit_mutation_retired",
        {"user_id": str(uuid.UUID(int=0)), "amount": 1},
    ),
    (
        "POST",
        "/api/v1/live_portrait/generate",
        "/api/v1/live_portrait/generate",
        "live_portrait_retired",
        {"image_url": "https://example.invalid/source.jpg", "seconds": 5},
    ),
    ("GET", "/api/v1/live_portrait/list", "/api/v1/live_portrait/list", "live_portrait_retired", None),
    (
        "GET",
        "/api/v1/live_portrait/{job_id}",
        f"/api/v1/live_portrait/{uuid.UUID(int=0)}",
        "live_portrait_retired",
        None,
    ),
    (
        "GET",
        "/api/v1/recommendations/local_studios",
        "/api/v1/recommendations/local_studios",
        "local_recommendations_retired",
        None,
    ),
    (
        "POST",
        "/api/v1/leads/submit",
        "/api/v1/leads/submit",
        "leads_retired",
        {"name": "Retired", "phone": "5551234567", "city": "New York", "privacy_accepted": True},
    ),
    ("GET", "/api/v1/leads/list", "/api/v1/leads/list", "leads_retired", None),
    ("GET", "/api/v1/leads/export.csv", "/api/v1/leads/export.csv", "leads_retired", None),
    ("GET", "/api/v1/admin/crm_preview", "/api/v1/admin/crm_preview", "leads_retired", None),
    ("POST", "/api/v1/admin/crm_push", "/api/v1/admin/crm_push", "leads_retired", None),
    (
        "GET",
        "/api/v1/admin/crm_push_history",
        "/api/v1/admin/crm_push_history",
        "leads_retired",
        None,
    ),
    (
        "POST",
        "/api/v1/session/create",
        "/api/v1/session/create",
        "partner_session_retired",
        {"template_id": "retired"},
    ),
    (
        "GET",
        "/api/v1/session/{session_id}/status",
        "/api/v1/session/retired/status",
        "partner_session_retired",
        None,
    ),
    (
        "POST",
        "/api/v1/session/{session_id}/upload/host",
        "/api/v1/session/retired/upload/host?image_url=https://example.invalid/host.jpg",
        "partner_session_retired",
        None,
    ),
    (
        "POST",
        "/api/v1/session/{session_id}/upload/guest",
        "/api/v1/session/retired/upload/guest?image_url=https://example.invalid/partner.jpg",
        "partner_session_retired",
        None,
    ),
    (
        "GET",
        "/api/v1/session/{session_id}/images",
        "/api/v1/session/retired/images",
        "partner_session_retired",
        None,
    ),
    (
        "GET",
        "/api/v1/session/{session_id}/share_meta",
        "/api/v1/session/retired/share_meta",
        "partner_session_retired",
        None,
    ),
    (
        "POST",
        "/api/v1/session/{session_id}/processing",
        "/api/v1/session/retired/processing",
        "partner_session_retired",
        None,
    ),
    (
        "POST",
        "/api/v1/session/{session_id}/complete",
        "/api/v1/session/retired/complete",
        "partner_session_retired",
        None,
    ),
    (
        "POST",
        "/api/v1/session/{session_id}/bind_order",
        "/api/v1/session/retired/bind_order",
        "partner_session_retired",
        {"order_id": str(uuid.UUID(int=0))},
    ),
)


class WebOnlyStaticContractTest(unittest.TestCase):
    def test_frontend_has_no_mini_program_or_qr_build_surface(self) -> None:
        package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
        self.assertNotIn("dev:mp-weixin", package["scripts"])
        self.assertNotIn("build:mp-weixin", package["scripts"])
        self.assertNotIn("@dcloudio/uni-mp-weixin", package["dependencies"])
        self.assertNotIn("qrcode", package["dependencies"])
        self.assertNotIn("@types/qrcode", package["devDependencies"])

        self.assertEqual(package["scripts"]["test:unit"], "vitest run")
        self.assertIn("vitest", package["devDependencies"])
        self.assertTrue(any((ROOT / "frontend/tests").rglob("*.spec.ts")))

        manifest = json.loads((ROOT / "frontend/src/manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("mp-weixin", manifest)

        lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
        locked_packages = lock.get("packages", {})
        for package_path in (
            "node_modules/@dcloudio/uni-mp-weixin",
            "node_modules/qrcode",
            "node_modules/@types/qrcode",
        ):
            with self.subTest(package_path=package_path):
                self.assertNotIn(package_path, locked_packages)

        for relative_path in ("scripts/start_frontend_dev.ps1", "scripts/start_frontend_dev.cmd"):
            launcher = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("dev:web", launcher)
            self.assertNotIn("dev:h5", launcher)

    def test_frontend_has_no_native_tabbar_or_anonymous_join_route(self) -> None:
        pages = json.loads((ROOT / "frontend/src/pages.json").read_text(encoding="utf-8"))
        self.assertNotIn("tabBar", pages)
        self.assertNotIn("pages/join/landing", {page["path"] for page in pages["pages"]})
        self.assertFalse((ROOT / "frontend/src/pages/join/landing.vue").exists())

        app_source = (ROOT / "frontend/src/App.vue").read_text(encoding="utf-8")
        locale_source = (ROOT / "frontend/src/stores/i18n.ts").read_text(encoding="utf-8")
        self.assertNotIn("applyTabBarLocale", app_source)
        self.assertNotIn("uni-tabbar", app_source)
        self.assertNotIn("console.log", app_source)
        self.assertNotIn("setTabBarItem", locale_source)
        self.assertNotIn("applyTabBarLocale", locale_source)
        self.assertNotIn("'tab.home'", locale_source)
        self.assertNotIn("'tab.orders'", locale_source)
        self.assertNotIn(
            "uni.switchTab",
            (ROOT / "frontend/src/pages/result/download.vue").read_text(encoding="utf-8"),
        )

    def test_active_frontend_has_no_retired_remote_join_or_qr_surface(self) -> None:
        home_source = (ROOT / "frontend/src/pages/index/index.vue").read_text(encoding="utf-8")
        create_source = (ROOT / "frontend/src/pages/create/index.vue").read_text(encoding="utf-8")
        detail_source = (ROOT / "frontend/src/pages/detail/detail.vue").read_text(encoding="utf-8")
        preview_source = (ROOT / "frontend/src/pages/preview/preview.vue").read_text(encoding="utf-8")
        payment_source = (ROOT / "frontend/src/components/PaymentModal.vue").read_text(encoding="utf-8")
        navigation_source = (ROOT / "frontend/src/components/NavBar.vue").read_text(encoding="utf-8")

        for forbidden in ("couple_remote", "/session/", "remote_join"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, create_source)
                self.assertNotIn(forbidden, detail_source)
        self.assertNotIn("pages/join/landing", navigation_source)
        self.assertNotIn("flags.remote_join", payment_source)
        self.assertNotIn("flags.live_portrait", payment_source)
        self.assertNotIn("Solo, couple, or remote", home_source)
        self.assertNotIn("invite a partner from another device", home_source)
        self.assertNotIn("QRCode", preview_source)
        self.assertNotIn("qrCodeUrl", preview_source)
        for retired_path in ("/live_portrait/", "/recommendations/", "/leads/"):
            with self.subTest(retired_path=retired_path):
                self.assertNotIn(retired_path, preview_source)

        admin_source = (ROOT / "frontend/src/pages/admin/index.vue").read_text(encoding="utf-8")
        order_store_source = (ROOT / "frontend/src/stores/order.ts").read_text(encoding="utf-8")
        self.assertNotIn("remote_join", admin_source)
        self.assertNotIn("remote_join", order_store_source)
        self.assertIn('v-if="isGoldenTemplate"', detail_source)
        self.assertIn("goCreate('golden_anniversary')", detail_source)
        self.assertIn("Golden anniversary workflow", detail_source)

        legacy_session_service = (ROOT / "backend/app/services/session_service.py").read_text(encoding="utf-8")
        self.assertNotIn("mp_path", legacy_session_service)
        self.assertNotIn("pages/join/landing", legacy_session_service)

    def test_active_frontend_has_no_guest_or_wechat_authentication(self) -> None:
        active_paths = (
            ROOT / "frontend/src/utils/api.ts",
            ROOT / "frontend/src/utils/supabase.ts",
            ROOT / "frontend/src/services/auth.ts",
            ROOT / "frontend/src/utils/auth/identity.ts",
            ROOT / "frontend/src/utils/auth/index.ts",
            ROOT / "frontend/src/pages/auth/callback.vue",
            ROOT / "frontend/src/pages/auth/login.vue",
            ROOT / "frontend/src/pages/auth/register.vue",
            ROOT / "frontend/src/pages/account/index.vue",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in active_paths)
        for forbidden in (
            "MP-WEIXIN",
            "provider: 'weixin'",
            "previous_guest_id",
            "localhost:3000/pages/account/index",
            "getSupabaseRedirectUrl",
            "isSupabaseConfigured",
            "signOutFromSupabase",
            "ai_wedding_guest_id",
            "GUEST_ID_KEY",
            "getGuestUserId",
            "isGuestSession",
            "X-Visitor-Id",
            "Continue as guest",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)

    def test_signed_out_account_does_not_default_to_success_states(self) -> None:
        account_source = (ROOT / "frontend/src/pages/account/index.vue").read_text(encoding="utf-8")
        self.assertNotIn("profile?.status || 'active'", account_source)
        self.assertIn("const renewalStatus = computed", account_source)
        self.assertIn("if (!current?.product_code)", account_source)
        self.assertIn("Not applicable", account_source)
        self.assertIn("const accountCreditValue = computed", account_source)
        self.assertIn("const creditStatusLabel = computed", account_source)
        self.assertIn("Sign in required", account_source)
        self.assertIn('v-if="accountAuthed && adminAccess"', account_source)

    def test_billing_surfaces_fail_closed_without_capability_or_catalog(self) -> None:
        ops_source = (ROOT / "frontend/src/stores/ops.ts").read_text(encoding="utf-8")
        home_source = (ROOT / "frontend/src/pages/index/index.vue").read_text(encoding="utf-8")
        modal_source = (ROOT / "frontend/src/components/PaymentModal.vue").read_text(encoding="utf-8")
        subscription_source = (ROOT / "frontend/src/stores/subscription.ts").read_text(encoding="utf-8")

        self.assertIn("capabilities: ActiveCapabilities", ops_source)
        self.assertIn("credit_pack_checkout: false", ops_source)
        self.assertIn("subscription_billing: false", ops_source)
        self.assertIn('v-if="billingAvailable"', home_source)
        self.assertNotIn("const pricingPlans", home_source)
        self.assertNotIn("DEFAULT_CREDIT_PACKAGES", modal_source)
        self.assertNotIn("defaultCreditPackages", modal_source)
        self.assertIn("useOpsStore", modal_source)
        self.assertIn("if (!creditCheckoutAvailable.value)", modal_source)
        self.assertIn("if (!subscriptionBillingAvailable.value)", modal_source)
        self.assertNotIn("DEFAULT_SUBSCRIPTION_PLANS", subscription_source)
        self.assertNotIn("defaultSubscriptionPlans", subscription_source)

    def test_public_auth_and_user_schemas_do_not_expose_legacy_identity(self) -> None:
        from app.schemas.auth import SupabaseSessionRequest
        from app.schemas.user import UserRead

        self.assertNotIn("previous_guest_id", SupabaseSessionRequest.model_fields)
        self.assertEqual(set(SupabaseSessionRequest.model_fields), {"access_token", "intent_token"})
        for field in ("openid", "unionid", "auth_provider", "auth_subject"):
            with self.subTest(field=field):
                self.assertNotIn(field, UserRead.model_fields)

    def test_new_orders_reject_retired_remote_join_input(self) -> None:
        from pydantic import ValidationError

        from app.schemas.order import OrderCreate

        self.assertNotIn("remote_join", OrderCreate.model_fields)
        with self.assertRaises(ValidationError):
            OrderCreate.model_validate(
                {
                    "template_id": "chn_xiuhe",
                    "user_images": ["asset:one", "asset:two"],
                    "legal_accepted": True,
                    "remote_join": True,
                }
            )

    def test_active_model_docs_label_legacy_identity_as_internal_only(self) -> None:
        user_model = (ROOT / "backend/app/models/user.py").read_text(encoding="utf-8")
        order_model = (ROOT / "backend/app/models/order.py").read_text(encoding="utf-8")
        for stale_product_claim in (
            "WeChat Mini Program users",
            "password-based sign-in",
            "WeChat UnionID",
            "WeChat payment transaction ID",
        ):
            with self.subTest(stale_product_claim=stale_product_claim):
                self.assertNotIn(stale_product_claim, user_model + order_model)

    def test_dead_runtime_router_modules_are_deleted(self) -> None:
        for relative_path in (
            "backend/app/routers/auth/guest.py",
            "backend/app/routers/auth/merge.py",
            "backend/app/routers/live_portrait.py",
            "backend/app/routers/recommendations.py",
            "backend/app/routers/leads.py",
            "backend/app/routers/session.py",
            "backend/app/services/lead_crm_service.py",
            "backend/app/services/local_reco_service.py",
            "backend/scripts/run_e2e_commercial_regression.py",
            "scripts/run_prod_generation_acceptance.mjs",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertFalse((ROOT / relative_path).exists())

    def test_identity_docs_do_not_restore_retired_bearer_or_admin_auth(self) -> None:
        supabase = (ROOT / "docs/SUPABASE_SETUP.md").read_text(encoding="utf-8")
        self.assertNotIn("请求业务 API 时带 `Authorization: Bearer", supabase)
        self.assertNotIn("https://your-domain.com/**", supabase)
        self.assertIn("/pages/auth/callback", supabase)

        production = (ROOT / "docs/PRODUCTION_ACCEPTANCE.md").read_text(encoding="utf-8")
        self.assertNotIn("--admin-token", production)
        self.assertNotIn("--public-bearer-token", production)
        self.assertIn("尚未达到 Production accepted", production)
        preflight = (ROOT / "backend/scripts/preflight_production.py").read_text(encoding="utf-8")
        self.assertNotIn("run_e2e_commercial_regression", preflight)
        self.assertIn("不代表真实商业主链路或生产验收通过", preflight)

    def test_credit_service_has_no_anonymous_json_fallback(self) -> None:
        source = (ROOT / "backend/app/services/credit_service.py").read_text(encoding="utf-8")
        for forbidden in ("CREDITS_FILE", "anonymous_user", "_read_credits", "_write_credits"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_runtime_ops_config_has_no_retired_product_sections(self) -> None:
        source = (ROOT / "backend/app/services/ops_config_service.py").read_text(encoding="utf-8")
        frontend_source = (ROOT / "frontend/src/stores/ops.ts").read_text(encoding="utf-8")
        admin_source = (ROOT / "backend/app/routers/admin.py").read_text(encoding="utf-8")

        for forbidden in (
            "legacy_enabled",
            "portal_enabled",
            '"recommendations"',
            '"crm"',
            "get_recommendation_config",
            "get_crm_config",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source + frontend_source)
        self.assertNotIn("recommendations: dict[str, Any]", admin_source)
        self.assertNotIn("crm: dict[str, Any]", admin_source)

    def test_legacy_identity_cannot_authorize_or_drive_admin_surfaces(self) -> None:
        from app.core.config import Settings
        from app.models.user import User
        from app.routers.admin import _user_item

        admin_auth_source = (ROOT / "backend/app/core/admin_auth.py").read_text(encoding="utf-8")
        admin_service_source = (ROOT / "backend/app/services/admin_service.py").read_text(encoding="utf-8")
        admin_users_source = (ROOT / "frontend/src/pages/admin/users.vue").read_text(encoding="utf-8")
        admin_home_source = (ROOT / "frontend/src/pages/admin/index.vue").read_text(encoding="utf-8")
        legal_consent_source = (ROOT / "frontend/src/components/LegalConsentInline.vue").read_text(encoding="utf-8")

        self.assertNotIn("admin_openids", Settings.model_fields)
        self.assertNotIn("admin_openid_list", admin_auth_source)
        self.assertNotIn("OWNER_EMAILS", admin_auth_source)
        self.assertNotIn("User.openid", admin_service_source)
        self.assertNotIn("User(openid", admin_service_source)
        self.assertNotIn("openid", admin_users_source.lower())
        self.assertNotIn("and leads", admin_home_source)
        self.assertNotIn("'lead'", legal_consent_source)

        user_id = uuid.uuid4()
        item = _user_item(User(id=user_id, openid="internal-only-alias"), 0)
        self.assertEqual(item.user_id, str(user_id))
        self.assertEqual(item.name, str(user_id))

        for relative_path in (".env.example", "backend/.env.example", "docs/VERCEL_DEPLOYMENT.md"):
            with self.subTest(relative_path=relative_path):
                source = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertNotIn("ADMIN_OPENIDS", source)

    def test_retired_lead_crypto_and_visitor_header_are_not_runtime_requirements(self) -> None:
        from app.core.config import Settings

        runtime_checks = (ROOT / "backend/app/core/runtime_checks.py").read_text(encoding="utf-8")
        risk_service = (ROOT / "backend/app/services/account_risk_service.py").read_text(encoding="utf-8")

        self.assertFalse((ROOT / "backend/app/core/phone_crypto.py").exists())
        self.assertNotIn("phone_crypto_key", Settings.model_fields)
        self.assertNotIn("PHONE_CRYPTO_KEY", runtime_checks)
        self.assertNotIn("x-visitor-id", risk_service.lower())
        for relative_path in (".env.example", "backend/.env.example", "docs/VERCEL_DEPLOYMENT.md"):
            with self.subTest(relative_path=relative_path):
                source = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertNotIn("PHONE_CRYPTO_KEY", source)

    def test_public_legal_pricing_does_not_advertise_retired_features(self) -> None:
        from app.services.legal_policy_service import get_legal_policies

        pricing = get_legal_policies()["pricing"]
        for retired_key in ("couple_remote", "premium_scene", "live_portrait_5s", "live_portrait_extra_5s"):
            with self.subTest(retired_key=retired_key):
                self.assertNotIn(retired_key, pricing)

    def test_current_product_docs_describe_web_saas_only(self) -> None:
        for relative_path in ("README.md", "docs/PRD.md"):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            with self.subTest(relative_path=relative_path):
                self.assertNotIn("小程序", source)
                self.assertIn("Web", source)

        deployment = (ROOT / "docs/VERCEL_DEPLOYMENT.md").read_text(encoding="utf-8")
        self.assertNotIn("guest session", deployment.lower())
        self.assertIn("compiler output directory", deployment)


class RetiredRouteContractTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app.core.database import get_db
        from app.main import app

        self.app = app
        self._original_dependency_overrides = dict(app.dependency_overrides)
        self.database_dependency_calls = 0

        async def forbidden_database_dependency():
            self.database_dependency_calls += 1
            raise AssertionError("retired routes must not resolve a database dependency")
            yield  # pragma: no cover

        app.dependency_overrides[get_db] = forbidden_database_dependency
        self._had_runtime_blocker = hasattr(app.state, "runtime_config_blocked")
        self._runtime_blocker = getattr(app.state, "runtime_config_blocked", False)
        app.state.runtime_config_blocked = False

    async def asyncTearDown(self) -> None:
        self.app.dependency_overrides.clear()
        self.app.dependency_overrides.update(self._original_dependency_overrides)
        if self._had_runtime_blocker:
            self.app.state.runtime_config_blocked = self._runtime_blocker
        elif hasattr(self.app.state, "runtime_config_blocked"):
            delattr(self.app.state, "runtime_config_blocked")

    async def test_central_router_has_no_database_model_or_service_imports(self) -> None:
        source = (ROOT / "backend/app/routers/retired.py").read_text(encoding="utf-8")
        for forbidden in ("app.core.database", "app.models", "app.services"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    async def test_one_central_router_owns_every_retired_path(self) -> None:
        from tests.route_contract import effective_routes

        for method, route_path, _, _, _ in RETIRED_ROUTES:
            matches = [
                route
                for route in effective_routes(self.app)
                if route.path == route_path and method in getattr(route, "methods", set())
            ]
            with self.subTest(method=method, route_path=route_path):
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0].endpoint.__module__, "app.routers.retired")

    async def test_retired_paths_return_stable_410_without_authentication(self) -> None:
        def has_url_key(value: object) -> bool:
            if isinstance(value, dict):
                return any(
                    "url" in str(key).lower() or has_url_key(nested)
                    for key, nested in value.items()
                )
            if isinstance(value, list):
                return any(has_url_key(item) for item in value)
            return False

        transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for method, _, request_path, expected_code, body in RETIRED_ROUTES:
                kwargs = {"json": body} if body is not None else {}
                response = await client.request(method, request_path, **kwargs)
                with self.subTest(method=method, request_path=request_path):
                    self.assertEqual(response.status_code, 410, response.text)
                    payload = response.json()
                    self.assertEqual(payload["code"], expected_code)
                    self.assertEqual(
                        set(payload),
                        {"code", "message", "request_id", "retryable", "field_errors"},
                    )
                    self.assertFalse(has_url_key(payload), payload)
        self.assertEqual(self.database_dependency_calls, 0)


if __name__ == "__main__":
    unittest.main()
