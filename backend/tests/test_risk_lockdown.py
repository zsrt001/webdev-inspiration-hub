"""Fail-closed bootstrap safety contract tests."""

from __future__ import annotations

import importlib
import httpx
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, Response

from app.core.config import Settings


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class RiskLockdownTest(unittest.TestCase):
    def test_high_risk_capabilities_default_off(self) -> None:
        settings = Settings(_env_file=None)

        for field in (
            "google_auth_enabled",
            "authenticated_upload_enabled",
            "generation_enabled",
            "credit_pack_checkout_enabled",
            "subscription_billing_enabled",
            "private_download_enabled",
            "partner_invite_enabled",
        ):
            self.assertTrue(hasattr(settings, field), f"missing setting: {field}")
            self.assertFalse(getattr(settings, field), field)

    def test_disabled_capability_returns_structured_503(self) -> None:
        self.assertTrue(
            (ROOT / "backend" / "app" / "core" / "feature_flags.py").exists(),
            "bootstrap capability contract is missing",
        )
        flags = importlib.import_module("app.core.feature_flags")

        with patch.object(flags.settings, "generation_enabled", False):
            with self.assertRaises(HTTPException) as raised:
                flags.require_bootstrap_capability(flags.Capability.GENERATION)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "capability_disabled")
        self.assertEqual(raised.exception.detail["capability"], "generation")

    def test_vercel_does_not_force_unsafe_flags_on(self) -> None:
        payload = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        env = payload.get("env", {})

        self.assertEqual(env.get("REMOTE_JOIN_ENABLED"), "false")
        self.assertEqual(env.get("LIVE_PORTRAIT_ENABLED"), "false")
        self.assertEqual(env.get("GOOGLE_AUTH_ENABLED"), "false")
        self.assertEqual(env.get("AUTHENTICATED_UPLOAD_ENABLED"), "false")
        self.assertEqual(env.get("GENERATION_ENABLED"), "false")
        self.assertEqual(env.get("CREDIT_PACK_CHECKOUT_ENABLED"), "false")
        self.assertEqual(env.get("SUBSCRIPTION_BILLING_ENABLED"), "false")
        self.assertEqual(env.get("PRIVATE_DOWNLOAD_ENABLED"), "false")
        self.assertEqual(env.get("PARTNER_INVITE_ENABLED"), "false")
        self.assertEqual(env.get("QA_REQUIRE_VISION"), "true")
        self.assertEqual(env.get("QA_FAIL_ON_VISION_ERROR"), "true")
        self.assertNotEqual(env.get("QA_REQUIRE_IDENTITY_EMBEDDING"), "false")

    def test_frontend_fallback_hides_every_high_risk_surface(self) -> None:
        frontend = ROOT / "frontend" / "src"
        ops_source = (frontend / "stores" / "ops.ts").read_text(encoding="utf-8")
        navigation_source = (frontend / "components" / "NavBar.vue").read_text(encoding="utf-8")
        home_source = (frontend / "pages" / "index" / "index.vue").read_text(encoding="utf-8")
        detail_source = (frontend / "pages" / "detail" / "detail.vue").read_text(encoding="utf-8")
        create_source = (frontend / "pages" / "create" / "index.vue").read_text(encoding="utf-8")
        login_source = (frontend / "pages" / "auth" / "login.vue").read_text(encoding="utf-8")
        register_source = (frontend / "pages" / "auth" / "register.vue").read_text(encoding="utf-8")
        account_source = (frontend / "pages" / "account" / "index.vue").read_text(encoding="utf-8")
        orders_source = (frontend / "pages" / "orders" / "orders.vue").read_text(encoding="utf-8")
        preview_source = (frontend / "pages" / "preview" / "preview.vue").read_text(encoding="utf-8")
        supabase_source = (frontend / "utils" / "supabase.ts").read_text(encoding="utf-8")

        for capability in (
            "google_auth: false",
            "authenticated_upload: false",
            "generation: false",
            "credit_pack_checkout: false",
            "subscription_billing: false",
            "private_download: false",
            "partner_invite: false",
        ):
            self.assertIn(capability, ops_source)
        self.assertIn("creationAvailable: (state)", ops_source)
        self.assertIn("googleAuthAvailable: (state)", ops_source)
        self.assertIn("billingAvailable: (state)", ops_source)

        self.assertGreaterEqual(navigation_source.count('v-if="creationAvailable"'), 2)
        self.assertIn('v-if="accountAuthed || googleAuthAvailable"', navigation_source)
        self.assertIn(
            "if (path === '/pages/create/index' && !creationAvailable.value) return;",
            navigation_source,
        )
        self.assertIn("if (!googleAuthAvailable.value) return;", navigation_source)

        self.assertGreaterEqual(home_source.count('v-if="!opsStore.loaded"'), 2)
        self.assertGreaterEqual(home_source.count('v-else-if="creationAvailable" class="btn primary"'), 2)
        self.assertIn('v-else class="availability-notice"', home_source)
        self.assertIn("if (!creationAvailable.value) return;", home_source)

        self.assertIn('v-if="!opsStore.loaded" class="availability-notice"', detail_source)
        self.assertIn('v-else-if="creationAvailable" class="hero-actions"', detail_source)
        self.assertIn('v-else class="availability-notice"', detail_source)
        self.assertIn("if (!creationAvailable.value) return;", detail_source)

        self.assertIn('v-if="!opsStore.loaded" class="capability-unavailable"', create_source)
        self.assertIn('v-else-if="!creationAvailable" class="capability-unavailable"', create_source)
        self.assertIn("<template v-else>", create_source)
        self.assertGreaterEqual(create_source.count("if (!creationAvailable.value) return"), 2)
        self.assertIn("await opsStore.fetchPublicConfig();", create_source)

        for auth_source in (login_source, register_source):
            self.assertIn('v-if="supabaseEnabled"', auth_source)
            self.assertIn("if (!supabaseEnabled.value) return;", auth_source)
            self.assertIn("refreshSupabaseConfig(true)", auth_source)
            self.assertNotIn("opsStore.fetchPublicConfig()", auth_source)
        self.assertIn("if (!isWebRuntime() || !config?.google_oauth_enabled) return false;", supabase_source)
        self.assertIn("if (!config.supabase_url || !config.supabase_publishable_key) return false;", supabase_source)

        self.assertGreaterEqual(account_source.count('v-if="creationAvailable"'), 2)
        self.assertIn("!accountAuthed && googleAuthAvailable && supabaseEnabled", account_source)
        self.assertIn("if (!creationAvailable.value) return;", account_source)
        self.assertIn("if (!googleAuthAvailable.value) return;", account_source)

        self.assertIn('v-if="googleAuthAvailable"', orders_source)
        self.assertIn('v-if="creationAvailable"', orders_source)
        self.assertIn("if (!creationAvailable.value) return;", orders_source)
        self.assertIn("if (!googleAuthAvailable.value) return;", orders_source)

        self.assertIn('v-if="billingAvailable"', preview_source)
        self.assertIn('v-if="creationAvailable"', preview_source)
        self.assertIn("privateDownloadAvailable.value", preview_source)
        self.assertIn("if (!creationAvailable.value) return;", preview_source)
        self.assertIn("if (!billingAvailable.value) return;", preview_source)

    def test_formal_matrix_covers_catalog_cleanup_and_retired_admin_crm(self) -> None:
        from scripts.release import verify_safe_baseline as verify

        probes = {
            (probe.method, probe.path): (
                probe.expected_status,
                probe.expected_code,
                probe.auth_kind,
            )
            for probe in (*verify.GUARDED_ROUTE_PROBES, *verify.RETIRED_ROUTE_PROBES)
        }
        expected = {
            ("GET", "/api/v1/credits/packages"): (
                503,
                "credit_catalog_unavailable",
                None,
            ),
            ("POST", "/api/v1/admin/cleanup_expired_assets"): (
                401,
                "session_missing",
                "retired_admin_header",
            ),
            ("GET", "/api/v1/admin/crm_preview"): (410, "leads_retired", "retired_admin_header"),
            ("POST", "/api/v1/admin/crm_push"): (410, "leads_retired", "retired_admin_header"),
            ("GET", "/api/v1/admin/crm_push_history"): (
                410,
                "leads_retired",
                "retired_admin_header",
            ),
        }
        for route, contract in expected.items():
            with self.subTest(route=route):
                self.assertEqual(probes.get(route), contract)
        self.assertEqual(len(verify.GUARDED_ROUTE_PROBES), 17)
        self.assertEqual(len(verify.RETIRED_ROUTE_PROBES), 33)


class RetiredRouteTest(unittest.IsolatedAsyncioTestCase):
    async def _assert_gone(self, awaitable) -> None:
        with self.assertRaises(HTTPException) as raised:
            await awaitable
        self.assertEqual(raised.exception.status_code, 410)

    async def test_anonymous_partner_session_is_permanently_retired(self) -> None:
        retired = importlib.import_module("app.routers.retired")

        with self.assertRaises(HTTPException) as raised:
            await retired.retired_partner_session_create()

        self.assertEqual(raised.exception.status_code, 410)
        self.assertEqual(raised.exception.detail["code"], "partner_session_retired")

    async def test_generic_bearer_auth_is_ignored_by_cookie_session_dependency(self) -> None:
        session_auth = importlib.import_module("app.core.session_auth")
        request = SimpleNamespace(
            cookies={},
            headers={"authorization": "Bearer valid-but-not-local-cookie"},
            state=SimpleNamespace(),
        )
        db = AsyncMock()
        with self.assertRaises(HTTPException) as raised:
            await session_auth.get_session_user(request=request, db=db)
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, {"code": "session_missing"})
        db.execute.assert_not_awaited()
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    async def test_legacy_openid_user_routes_are_gone_before_database_access(self) -> None:
        retired = importlib.import_module("app.routers.retired")

        await self._assert_gone(retired.retired_user_create())
        await self._assert_gone(retired.retired_user_detail(str(uuid.uuid4())))

    async def test_local_recommendations_are_gone_before_query(self) -> None:
        retired = importlib.import_module("app.routers.retired")
        await self._assert_gone(retired.retired_local_recommendations())

    async def test_lead_submit_is_gone_before_validation_or_insert(self) -> None:
        retired = importlib.import_module("app.routers.retired")
        await self._assert_gone(retired.retired_lead_submit())

    async def test_live_portrait_generate_is_gone_before_credit_or_provider(self) -> None:
        retired = importlib.import_module("app.routers.retired")
        await self._assert_gone(retired.retired_live_portrait_generate())

    async def test_live_portrait_reads_are_gone_before_query_or_url_serialization(self) -> None:
        retired = importlib.import_module("app.routers.retired")
        await self._assert_gone(retired.retired_live_portrait_list())
        await self._assert_gone(retired.retired_live_portrait_detail("not-a-uuid"))

    async def test_lead_admin_reads_are_gone_before_query(self) -> None:
        retired = importlib.import_module("app.routers.retired")
        await self._assert_gone(retired.retired_lead_list())
        await self._assert_gone(retired.retired_lead_export())

    async def test_legacy_credit_mutations_are_gone_before_ledger(self) -> None:
        retired = importlib.import_module("app.routers.retired")
        await self._assert_gone(retired.retired_credit_deduct())

    async def test_credit_catalog_and_all_legacy_mutations_fail_before_lookup_or_ledger(self) -> None:
        credits = importlib.import_module("app.routers.credits")
        retired = importlib.import_module("app.routers.retired")

        with (
            patch.object(
                credits,
                "load_active_catalog",
                side_effect=credits.BillingCatalogUnavailable("active_catalog_cardinality"),
            ),
            self.assertRaises(HTTPException) as packages_error,
        ):
            await credits.list_packages(region=None, locale=None, db=AsyncMock())
        self.assertEqual(packages_error.exception.status_code, 503)
        self.assertEqual(packages_error.exception.detail["code"], "credit_catalog_unavailable")

        await self._assert_gone(retired.retired_credit_purchase())
        await self._assert_gone(retired.retired_credit_add())

    async def test_public_url_upload_routes_are_permanently_retired(self) -> None:
        upload = importlib.import_module("app.routers.upload")
        await self._assert_gone(upload.retired_single_upload())
        await self._assert_gone(upload.retired_multiple_upload())
        await self._assert_gone(upload.retired_url_delete())


class CapabilityRouteGuardTest(unittest.IsolatedAsyncioTestCase):
    async def _assert_disabled(self, awaitable, capability: str) -> None:
        with self.assertRaises(HTTPException) as raised:
            await awaitable
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "capability_disabled")
        self.assertEqual(raised.exception.detail["capability"], capability)

    async def test_google_oauth_intent_is_blocked_before_row_or_redirect(self) -> None:
        google = importlib.import_module("app.routers.auth.google")
        db = AsyncMock()
        request = SimpleNamespace(headers={}, client=None)
        decision = SimpleNamespace(allowed=False, reason="state_off")
        with (
            patch.object(google, "_enforce_intent_rate_limit"),
            patch.object(google, "require_request_origin", new=AsyncMock(return_value="https://example.test")),
            patch.object(google, "resolve_request_capability", new=AsyncMock(return_value=decision)),
            patch.object(google, "create_oauth_intent", new=AsyncMock()) as create_intent,
        ):
            await self._assert_disabled(
                google.create_google_oauth_intent(
                    google.OAuthIntentRequest(next_path="/pages/account/index"),
                    request,
                    Response(),
                    db,
                ),
                "google_auth",
            )
        create_intent.assert_not_awaited()
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    async def test_signed_webhook_and_read_only_credit_surfaces_remain_available(self) -> None:
        payments = importlib.import_module("app.routers.payments")
        credits = importlib.import_module("app.routers.credits")
        db = AsyncMock()
        user = SimpleNamespace(id=uuid.uuid4())

        request = SimpleNamespace(
            headers={"creem-signature": "signed"},
            body=AsyncMock(return_value=b"{}"),
        )
        accepted = {
            "event_id": "evt_signed",
            "created": True,
            "processing_state": "RECEIVED",
        }
        with patch.object(
            payments.payment_service,
            "process_webhook_event",
            new=AsyncMock(return_value=accepted),
        ) as webhook:
            result = await payments.creem_webhook(request, db)
        self.assertEqual(result, accepted)
        webhook.assert_awaited_once()
        self.assertEqual(webhook.await_args.kwargs["body"], b"{}")
        self.assertEqual(webhook.await_args.kwargs["signature_header"], "signed")

        with patch.object(credits, "get_balance_async", new=AsyncMock(return_value=7)) as balance:
            payload = await credits.get_user_balance(current_user=user, db=db)
        self.assertEqual(payload.balance, 7)
        self.assertFalse(payload.can_generate)
        balance.assert_awaited_once()

        with patch.object(credits, "list_credit_transactions_async", new=AsyncMock(return_value=[])) as history:
            payload = await credits.list_credit_transactions(current_user=user, db=db, limit=20)
        self.assertEqual(payload.transactions, [])
        history.assert_awaited_once()

    async def test_upload_and_gatekeeper_are_blocked_before_input_or_provider_access(self) -> None:
        media = importlib.import_module("app.routers.media")
        gatekeeper = importlib.import_module("app.routers.gatekeeper")
        request = SimpleNamespace(state=SimpleNamespace(), headers={})
        user = SimpleNamespace(id=uuid.uuid4())
        db = AsyncMock()

        with patch.object(media, "stream_authenticated_multipart_upload", new=AsyncMock()) as stream:
            await self._assert_disabled(
                media.upload_media(request, user, db),
                "authenticated_upload",
            )
        stream.assert_not_awaited()

        with patch.object(gatekeeper.gatekeeper_service, "check_image_quality", new=AsyncMock()) as check:
            await self._assert_disabled(
                gatekeeper.check_image_quality(
                    gatekeeper.GatekeeperRequest(asset_id=uuid.uuid4()),
                    request,
                    user,
                    db,
                ),
                "authenticated_upload",
            )
        check.assert_not_awaited()

    async def test_order_creation_is_blocked_before_reservation_or_queue(self) -> None:
        orders = importlib.import_module("app.routers.orders")
        user = SimpleNamespace(id=uuid.uuid4())
        db = AsyncMock()
        request = orders.OrderCreate(
            template_id="solo_royal_castle",
            asset_ids=[uuid.uuid4()],
            legal_accepted=True,
        )

        with patch.object(orders, "create_order_for_user", new=AsyncMock()) as create:
            await self._assert_disabled(
                orders.create_order(
                    request,
                    "generation-idempotency",
                    current_user=user,
                    db=db,
                ),
                "generation",
            )

        create.assert_not_awaited()
        db.execute.assert_not_awaited()

    async def test_payment_and_subscription_mutations_are_blocked_before_services(self) -> None:
        payments = importlib.import_module("app.routers.payments")
        subscriptions = importlib.import_module("app.routers.subscriptions")
        user = SimpleNamespace(id=uuid.uuid4())
        db = AsyncMock()
        http_request = SimpleNamespace(state=SimpleNamespace())

        with patch.object(payments.payment_service, "create_checkout", new=AsyncMock()) as checkout:
            await self._assert_disabled(
                payments.create_checkout(
                    payments.CreditPackCheckoutRequest(
                        product_code="pack_50",
                        return_url="https://example.test/return",
                    ),
                    http_request,
                    "credit-pack-idempotency",
                    current_user=user,
                    db=db,
                ),
                "credit_pack_checkout",
            )
        checkout.assert_not_awaited()

        with patch.object(subscriptions.subscription_service, "create_checkout", new=AsyncMock()) as subscribe:
            await self._assert_disabled(
                subscriptions.create_subscription_checkout(
                    subscriptions.SubscriptionCheckoutRequest(plan_code="starter_monthly"),
                    http_request,
                    "subscription-idempotency",
                    current_user=user,
                    db=db,
                ),
                "subscription_billing",
            )
        subscribe.assert_not_awaited()

        with patch.object(subscriptions.subscription_service, "cancel_current_subscription", new=AsyncMock()) as cancel:
            await self._assert_disabled(
                subscriptions.cancel_my_subscription(
                    http_request,
                    "subscription-cancel-idempotency",
                    current_user=user,
                    db=db,
                ),
                "subscription_billing",
            )
        cancel.assert_not_awaited()
        for retired_name in (
            "manual_checkout_page",
            "submit_manual_checkout",
            "complete_manual_checkout",
            "fail_manual_checkout",
            "ManualReviewRequest",
        ):
            self.assertFalse(hasattr(payments, retired_name), retired_name)

    async def test_backend_executor_rejects_legacy_payload_before_database_or_provider_access(self) -> None:
        executor = importlib.import_module("app.services.generation_executor_service")

        with self.assertRaisesRegex(ValueError, "payload_version_unsupported"):
            await executor.generate_order_v1({}, "not-a-uuid", "legacy-order.v0")

        executor_source = (
            ROOT / "backend/app/services/generation_executor_service.py"
        ).read_text(encoding="utf-8")
        self.assertFalse((ROOT / "backend/app/worker_tasks.py").exists())
        self.assertFalse((ROOT / "backend/app/core/task_queue.py").exists())
        for forbidden in (
            "LivePortraitJob",
            "generate_live_portrait",
            "run_order_generation",
            "session_service",
            "source_image_urls",
        ):
            self.assertNotIn(forbidden, executor_source)

    async def test_admin_generation_and_credit_probes_are_blocked_before_external_calls(self) -> None:
        admin = importlib.import_module("app.routers.admin")
        retired = importlib.import_module("app.routers.retired")
        db = AsyncMock()
        http_request = SimpleNamespace(state=SimpleNamespace(admin_actor="test-admin"))
        admin_user = SimpleNamespace(id=uuid.uuid4(), role="admin", status="active")

        with patch.object(admin.httpx, "AsyncClient") as client:
            await self._assert_disabled(
                admin.check_creem_products(http_request, db),
                "credit_pack_checkout",
            )
        client.assert_not_called()

        for call in (
            retired.retired_admin_generation_probe(),
            retired.retired_admin_order_regenerate(str(uuid.uuid4())),
        ):
            with self.assertRaises(HTTPException) as raised:
                await call
            self.assertEqual(raised.exception.status_code, 410)
            self.assertEqual(
                raised.exception.detail["code"],
                "admin_generation_execution_retired",
            )
        await self._assert_disabled(
            admin.grant_credits(
                http_request,
                admin.GrantCreditsRequest(
                    user_id=str(uuid.uuid4()),
                    amount=10,
                    idempotency_key="risk-lockdown",
                    approval_id="approval-test",
                    reason="risk lockdown contract",
                    positive_grant_policy=admin.PositiveGrantPolicy(
                        policy_code="service_recovery",
                        source_reference="risk-lockdown",
                        retention_days=90,
                    ),
                ),
                admin_user,
                db,
            ),
            "credit_pack_checkout",
        )
        db.execute.assert_not_awaited()


class DisabledOrderReadTest(unittest.IsolatedAsyncioTestCase):
    async def test_order_serialization_omits_all_image_and_download_urls(self) -> None:
        from datetime import datetime, timezone

        orders = importlib.import_module("app.routers.orders")
        order_model = importlib.import_module("app.models.order")
        user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        order = order_model.Order(
            id=uuid.uuid4(),
            user_id=user_id,
            status=order_model.OrderStatus.COMPLETED,
            template_id="classic",
            source_image_urls={"images": ["https://example.com/source.jpg"]},
            preview_image_urls={"main": "https://example.com/preview.jpg"},
            final_image_urls={"main": "https://example.com/final.jpg"},
            generation_params={"access_tier": "paid"},
            price_cents=100,
            created_at=now,
            updated_at=now,
        )
        db = AsyncMock()

        payload = await orders._serialize_order_for_user(db, order, user_id)

        dumped = payload.model_dump(mode="json")
        for forbidden in (
            "source_image_urls",
            "preview_image_urls",
            "final_image_urls",
            "preview_master_image_url",
            "final_master_image_url",
            "download_variants",
            "download_locked",
        ):
            self.assertNotIn(forbidden, dumped)
        self.assertNotIn("https://", str(dumped))
        self.assertEqual(payload.assets, [])
        self.assertFalse(payload.can_download)
        db.execute.assert_not_awaited()

    async def test_user_delete_is_paused_before_storage_or_database_access(self) -> None:
        from datetime import datetime, timezone

        orders = importlib.import_module("app.routers.orders")
        order_model = importlib.import_module("app.models.order")
        user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        order = order_model.Order(
            id=uuid.uuid4(),
            user_id=user_id,
            status=order_model.OrderStatus.COMPLETED,
            source_image_urls={"images": ["https://example.com/source.jpg"]},
            preview_image_urls={"main": "https://example.com/preview.jpg"},
            final_image_urls={"main": "https://example.com/final.jpg"},
            generation_params={},
            price_cents=0,
            created_at=now,
            updated_at=now,
        )
        db = AsyncMock()
        with self.assertRaises(HTTPException) as raised:
            await orders.delete_my_order(str(order.id), SimpleNamespace(id=user_id), db)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "cleanup_paused")
        db.execute.assert_not_awaited()
        self.assertEqual(order.source_image_urls, {"images": ["https://example.com/source.jpg"]})
        self.assertEqual(order.preview_image_urls, {"main": "https://example.com/preview.jpg"})
        self.assertEqual(order.final_image_urls, {"main": "https://example.com/final.jpg"})
        self.assertIsNone(order.deleted_at)
        self.assertIsNone(order.storage_cleanup_status)


class CleanupLockdownTest(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_remains_paused_for_safe_baseline_role(self) -> None:
        ops = importlib.import_module("app.routers.ops")
        db = AsyncMock()
        source_cleanup = AsyncMock()
        order_cleanup = AsyncMock()
        deletion_cleanup = AsyncMock()

        with (
            patch.object(ops.settings, "runtime_environment", "production"),
            patch.object(ops.settings, "release_role", "SAFE_BASELINE"),
            patch.object(ops.settings, "cleanup_cron_token", "cleanup-secret"),
            patch.object(ops, "cleanup_expired_source_images", source_cleanup),
            patch.object(ops, "cleanup_expired_orders", order_cleanup),
            patch.object(ops, "run_deletion_cleanup", deletion_cleanup),
        ):
            with self.assertRaises(HTTPException) as raised:
                await ops.cleanup_expired_assets(
                    authorization="Bearer cleanup-secret",
                    db=db,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "cleanup_paused")
        source_cleanup.assert_not_awaited()
        order_cleanup.assert_not_awaited()
        deletion_cleanup.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_cleanup_is_post_only_authenticated_and_runs_durable_state_machine(self) -> None:
        ops = importlib.import_module("app.routers.ops")
        cleanup_routes = [route for route in ops.router.routes if route.path == "/ops/cleanup_expired_assets"]
        self.assertEqual(len(cleanup_routes), 1)
        self.assertEqual(cleanup_routes[0].methods, {"POST"})
        db = AsyncMock()

        source_cleanup = AsyncMock(return_value={"orders": 1, "pending_assets": 1})
        order_cleanup = AsyncMock(return_value={"orders": 0, "pending_assets": 0})
        deletion_cleanup = AsyncMock(return_value={"claimed": 1, "deleted": 1})
        with (
            patch.object(ops.settings, "runtime_environment", "production"),
            patch.object(ops.settings, "release_role", "COMMERCIAL_7A"),
            patch.object(ops.settings, "cleanup_cron_token", "cleanup-secret"),
            patch.object(ops, "cleanup_expired_source_images", source_cleanup),
            patch.object(ops, "cleanup_expired_orders", order_cleanup),
            patch.object(ops, "run_deletion_cleanup", deletion_cleanup),
        ):
            result = await ops.cleanup_expired_assets(
                authorization="Bearer cleanup-secret",
                db=db,
            )

        source_cleanup.assert_awaited_once()
        order_cleanup.assert_awaited_once()
        deletion_cleanup.assert_awaited_once()
        self.assertEqual(result["deletion"]["deleted"], 1)


class HttpLockdownContractTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app.main import app

        self._had_runtime_blocker = hasattr(app.state, "runtime_config_blocked")
        self._runtime_blocker = getattr(app.state, "runtime_config_blocked", False)
        app.state.runtime_config_blocked = False

    async def asyncTearDown(self) -> None:
        from app.main import app

        if self._had_runtime_blocker:
            app.state.runtime_config_blocked = self._runtime_blocker
        elif hasattr(app.state, "runtime_config_blocked"):
            delattr(app.state, "runtime_config_blocked")

    async def test_safe_baseline_cleanup_http_contract_pauses_before_mutation(self) -> None:
        from app.core.database import get_db
        from app.main import app

        ops = importlib.import_module("app.routers.ops")
        db = AsyncMock()
        source_cleanup = AsyncMock()
        order_cleanup = AsyncMock()
        deletion_cleanup = AsyncMock()

        async def database_override():
            yield db

        original_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = database_override
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        try:
            with (
                patch.object(ops.settings, "runtime_environment", "production"),
                patch.object(ops.settings, "release_role", "SAFE_BASELINE"),
                patch.object(ops.settings, "cleanup_cron_token", "cleanup-secret"),
                patch.object(ops, "cleanup_expired_source_images", source_cleanup),
                patch.object(ops, "cleanup_expired_orders", order_cleanup),
                patch.object(ops, "run_deletion_cleanup", deletion_cleanup),
            ):
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    response = await client.post(
                        "/api/v1/ops/cleanup_expired_assets",
                        headers={"Authorization": "Bearer cleanup-secret"},
                    )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original_overrides)

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["code"], "cleanup_paused")
        source_cleanup.assert_not_awaited()
        order_cleanup.assert_not_awaited()
        deletion_cleanup.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_retired_identity_headers_cannot_touch_users_before_lockdown_guards(self) -> None:
        from app.core.database import get_db
        from app.main import app

        class TrackingUnavailableDb:
            def __init__(self) -> None:
                self.statements: list[str] = []

            async def execute(self, statement):
                self.statements.append(str(statement))
                raise RuntimeError("authority unavailable in retired-identity test")

        database = TrackingUnavailableDb()

        async def unavailable_db():
            yield database

        original_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = unavailable_db
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        try:
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                list_response = await client.get(
                    "/api/v1/orders",
                    headers={"X-User-OpenID": "attacker-selected-openid"},
                )
                self.assertEqual(list_response.status_code, 401, list_response.text)
                self.assertEqual(database.statements, [])

                create_response = await client.post(
                    "/api/v1/orders/create",
                    headers={"X-Visitor-Id": "attacker-selected-visitor"},
                    json={
                        "template_id": "solo_royal_castle",
                        "asset_ids": ["00000000-0000-4000-8000-000000000001"],
                        "legal_accepted": True,
                    },
                )
                self.assertEqual(create_response.status_code, 401, create_response.text)
                self.assertEqual(create_response.json()["code"], "session_missing")
                self.assertEqual(database.statements, [])

                database.statements.clear()
                delete_response = await client.delete(
                    "/api/v1/orders/00000000-0000-0000-0000-000000000000",
                    headers={"X-User-OpenID": "attacker-selected-openid"},
                )
                self.assertEqual(delete_response.status_code, 503, delete_response.text)
                self.assertEqual(delete_response.json()["code"], "cleanup_paused")
                self.assertEqual(database.statements, [])
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original_overrides)

    async def test_retired_http_routes_short_circuit_before_auth_and_database_dependencies(self) -> None:
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        user_id = str(uuid.uuid4())
        retired_requests = (
            ("POST", "/api/v1/auth/login", {"json": {"code": "legacy-guest"}}),
            ("GET", "/api/v1/live_portrait/list", {}),
            ("GET", "/api/v1/live_portrait/not-a-uuid", {}),
            (
                "POST",
                "/api/v1/live_portrait/generate",
                {"json": {"image_url": "https://example.com/photo.jpg", "seconds": 5}},
            ),
            ("GET", "/api/v1/recommendations/local_studios", {}),
            (
                "POST",
                "/api/v1/leads/submit",
                {
                    "json": {
                        "name": "Legacy lead",
                        "phone": "5551234567",
                        "city": "New York",
                        "privacy_accepted": True,
                    }
                },
            ),
            ("GET", "/api/v1/leads/list", {}),
            ("GET", "/api/v1/leads/export.csv", {}),
            ("POST", "/api/v1/credits/purchase", {"json": {"package_id": "pack_50"}}),
            ("POST", "/api/v1/credits/deduct", {}),
            ("POST", "/api/v1/credits/add", {"json": {"user_id": user_id, "amount": 10}}),
            ("POST", "/api/v1/users/", {"json": {"openid": "legacy-openid"}}),
            ("GET", f"/api/v1/users/{user_id}", {}),
            ("PATCH", f"/api/v1/users/{user_id}", {"json": {"nickname": "legacy"}}),
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for method, path, kwargs in retired_requests:
                response = await client.request(method, path, **kwargs)
                self.assertEqual(response.status_code, 410, f"{method} {path}: {response.text}")
                if path.startswith("/api/v1/live_portrait"):
                    self.assertNotIn("video_url", response.json())

            packages = await client.get("/api/v1/credits/packages")
            self.assertEqual(packages.status_code, 503)
            self.assertEqual(packages.json()["code"], "credit_catalog_unavailable")

    async def test_anonymous_partner_session_http_routes_are_retired(self) -> None:
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        identifier = str(uuid.uuid4())
        requests = (
            ("POST", "/api/v1/session/create", {"json": {"template_id": "classic"}}),
            ("GET", "/api/v1/session/probe/status", {}),
            ("POST", "/api/v1/session/probe/upload/host", {"params": {"image_url": "https://example.invalid/a.jpg"}}),
            ("POST", "/api/v1/session/probe/upload/guest", {"params": {"image_url": "https://example.invalid/b.jpg"}}),
            ("GET", "/api/v1/session/probe/images", {}),
            ("GET", "/api/v1/session/probe/share_meta", {}),
            ("POST", "/api/v1/session/probe/processing", {}),
            ("POST", "/api/v1/session/probe/complete", {}),
            ("POST", "/api/v1/session/probe/bind_order", {"json": {"order_id": identifier}}),
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for method, path, kwargs in requests:
                response = await client.request(method, path, **kwargs)
                self.assertEqual(response.status_code, 410, f"{method} {path}: {response.text}")
                self.assertEqual(response.json()["code"], "partner_session_retired")

    async def test_formal_route_matrix_reaches_every_expected_application_guard(self) -> None:
        from app.core.database import get_db
        from app.main import app
        from app.routers import ops
        from scripts.release import verify_safe_baseline as verify

        class EmptyResult:
            @staticmethod
            def scalar_one_or_none():
                return None

        class EmptyAuthorityDb:
            async def execute(self, _statement):
                return EmptyResult()

        async def empty_db():
            yield EmptyAuthorityDb()

        original_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = empty_db
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        try:
            with patch.object(ops.settings, "cleanup_cron_token", "cleanup-probe-token"):
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    for probe in (*verify.GUARDED_ROUTE_PROBES, *verify.RETIRED_ROUTE_PROBES):
                        headers: dict[str, str] = {}
                        if probe.auth_kind == "retired_bearer":
                            headers["Authorization"] = "Bearer retired-probe-token"
                        elif probe.auth_kind == "retired_admin_header":
                            headers["X-Admin-Token"] = "retired-probe-token"
                        elif probe.auth_kind == "cleanup":
                            headers["Authorization"] = "Bearer cleanup-probe-token"
                        if probe.include_origin:
                            headers["Origin"] = "http://localhost:3000"
                        kwargs: dict[str, object] = {"headers": headers}
                        if probe.params is not None:
                            kwargs["params"] = probe.params
                        if probe.json_body is not None:
                            kwargs["json"] = probe.json_body
                        if probe.form_body is not None:
                            kwargs["data"] = probe.form_body
                        if probe.multipart:
                            kwargs["files"] = {
                                probe.multipart_field: (
                                    "probe.png",
                                    b"safe-baseline",
                                    "image/png",
                                )
                            }
                        response = await client.request(probe.method, probe.path, **kwargs)
                        self.assertEqual(
                            response.status_code,
                            probe.expected_status,
                            f"{probe.name}: {response.text}",
                        )
                        if probe.expected_code is not None:
                            self.assertEqual(
                                response.json()["code"],
                                probe.expected_code,
                                probe.name,
                            )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original_overrides)

    async def test_public_and_session_protected_high_risk_routes_fail_closed(self) -> None:
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        requests = (
            (
                "POST",
                "/api/v1/auth/oauth-intents",
                {
                    "headers": {"Origin": "http://localhost:3000"},
                    "json": {"next_path": "/pages/account/index"},
                },
                503,
                "capability_disabled",
            ),
            (
                "POST",
                "/api/v1/gatekeeper/check",
                {"json": {"asset_id": "00000000-0000-0000-0000-000000000000"}},
                401,
                "session_missing",
            ),
            (
                "POST",
                "/api/v1/media/uploads",
                {"files": {"file": ("a.jpg", b"not-read", "image/jpeg")}},
                401,
                "session_missing",
            ),
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            retired_start = await client.get("/api/v1/auth/supabase/google/start")
            self.assertEqual(retired_start.status_code, 404)
            for method, path, kwargs, expected_status, expected_code in requests:
                response = await client.request(method, path, **kwargs)
                self.assertEqual(response.status_code, expected_status, f"{method} {path}: {response.text}")
                self.assertEqual(response.json()["code"], expected_code)


if __name__ == "__main__":
    unittest.main()
