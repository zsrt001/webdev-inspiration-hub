"""Fail-closed bootstrap safety contract tests."""

from __future__ import annotations

import importlib
import httpx
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException

from app.core.config import Settings


ROOT = Path(__file__).resolve().parents[2]


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
        source = (ROOT / "frontend" / "src" / "stores" / "ops.ts").read_text(encoding="utf-8")

        self.assertNotIn("remote_join: true", source)
        self.assertNotIn("local_recommendations: true", source)
        self.assertNotIn("director_mode: true", source)

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
                503,
                "cleanup_paused",
                "admin",
            ),
            ("GET", "/api/v1/admin/crm_preview"): (410, "leads_retired", "admin"),
            ("POST", "/api/v1/admin/crm_push"): (410, "leads_retired", "admin"),
            ("GET", "/api/v1/admin/crm_push_history"): (
                410,
                "leads_retired",
                "admin",
            ),
        }
        for route, contract in expected.items():
            with self.subTest(route=route):
                self.assertEqual(probes.get(route), contract)
        self.assertEqual(len(verify.GUARDED_ROUTE_PROBES), 33)
        self.assertEqual(len(verify.RETIRED_ROUTE_PROBES), 17)


class RetiredRouteTest(unittest.IsolatedAsyncioTestCase):
    async def _assert_gone(self, awaitable) -> None:
        with self.assertRaises(HTTPException) as raised:
            await awaitable
        self.assertEqual(raised.exception.status_code, 410)

    async def test_partner_invite_uses_the_authoritative_capability_guard(self) -> None:
        session_router = importlib.import_module("app.routers.session")
        db = AsyncMock()
        request = SimpleNamespace(state=SimpleNamespace())
        disabled = HTTPException(
            status_code=503,
            detail={
                "code": "capability_disabled",
                "capability": "partner_invite",
                "reason": "disabled",
            },
        )
        with patch.object(
            session_router,
            "require_request_capability",
            new=AsyncMock(side_effect=disabled),
        ) as guard:
            with self.assertRaises(HTTPException) as raised:
                await session_router.require_partner_invite_capability(request, db)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["capability"], "partner_invite")
        guard.assert_awaited_once()

    async def test_generic_bearer_auth_cannot_provision_an_unapproved_supabase_user(self) -> None:
        user_auth = importlib.import_module("app.core.user_auth")
        claims = SimpleNamespace(
            subject="unapproved-subject",
            email="unapproved@example.test",
            nickname="Unapproved",
            avatar_url=None,
        )
        result = SimpleNamespace(scalar_one_or_none=lambda: None)
        db = AsyncMock()
        db.execute.return_value = result
        with (
            patch.object(user_auth, "verify_supabase_token", new=AsyncMock(return_value=claims)),
            patch.object(user_auth, "ensure_user_account_columns", new=AsyncMock()),
        ):
            with self.assertRaises(HTTPException) as raised:
                await user_auth.get_request_user(
                    db=db,
                    authorization="Bearer valid-but-not-exchanged-supabase-token",
                )
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "token_user_not_found")
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    async def test_legacy_openid_user_routes_are_gone_before_database_access(self) -> None:
        users = importlib.import_module("app.routers.users")
        db = AsyncMock()
        user_id = uuid.uuid4()

        await self._assert_gone(
            users.create_user(users.UserCreate(openid="legacy-openid"), db)
        )
        await self._assert_gone(users.get_user(user_id, db))
        await self._assert_gone(users.update_user(user_id, users.UserUpdate(nickname="legacy"), db))

        db.execute.assert_not_awaited()

    async def test_local_recommendations_are_gone_before_query(self) -> None:
        recommendations = importlib.import_module("app.routers.recommendations")

        class FakeScalars:
            def all(self):
                return []

        class FakeResult:
            def scalars(self):
                return FakeScalars()

        db = AsyncMock()
        db.execute.return_value = FakeResult()

        await self._assert_gone(
            recommendations.list_local_studios(
                city=None,
                wedding_date=None,
                template_id=None,
                limit=3,
                db=db,
            )
        )

        db.execute.assert_not_awaited()

    async def test_lead_submit_is_gone_before_validation_or_insert(self) -> None:
        leads = importlib.import_module("app.routers.leads")
        request = leads.LeadSubmitRequest(
            name="Legacy lead",
            phone="5551234567",
            city="New York",
            privacy_accepted=False,
        )
        db = AsyncMock()

        await self._assert_gone(leads.submit_lead(request, db))

        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_live_portrait_generate_is_gone_before_credit_or_provider(self) -> None:
        live_portrait = importlib.import_module("app.routers.live_portrait")
        request = live_portrait.LivePortraitRequest(image_url="https://example.com/photo.jpg", seconds=5)
        user = AsyncMock()
        db = AsyncMock()

        with patch.object(live_portrait, "deduct_credits_async", new=AsyncMock()) as debit:
            await self._assert_gone(live_portrait.generate_live_portrait(request, user, db))

        debit.assert_not_awaited()

    async def test_live_portrait_reads_are_gone_before_query_or_url_serialization(self) -> None:
        live_portrait = importlib.import_module("app.routers.live_portrait")
        user = SimpleNamespace(id=uuid.uuid4())
        db = AsyncMock()

        await self._assert_gone(live_portrait.list_live_portrait_jobs(limit=6, current_user=user, db=db))
        await self._assert_gone(
            live_portrait.get_live_portrait_job(job_id="not-a-uuid", current_user=user, db=db)
        )

        db.execute.assert_not_awaited()

    async def test_lead_admin_reads_are_gone_before_query(self) -> None:
        leads = importlib.import_module("app.routers.leads")
        db = AsyncMock()
        kwargs = {
            "limit": 10,
            "city": None,
            "source_page": None,
            "source_slot": None,
            "template_id": None,
            "date_from": None,
            "date_to": None,
            "db": db,
            "_": None,
        }

        await self._assert_gone(leads.list_leads(**kwargs))
        export_kwargs = dict(kwargs)
        export_kwargs.pop("limit")
        await self._assert_gone(leads.export_leads_csv(**export_kwargs))

        db.execute.assert_not_awaited()

    async def test_legacy_credit_mutations_are_gone_before_ledger(self) -> None:
        credits = importlib.import_module("app.routers.credits")
        db = AsyncMock()
        user = SimpleNamespace(id="00000000-0000-0000-0000-000000000001")

        await self._assert_gone(
            credits.deduct_user_credits(
                amount=1,
                current_user=user,
                db=db,
            )
        )

        db.execute.assert_not_awaited()
        db.flush.assert_not_awaited()

    async def test_credit_catalog_and_all_legacy_mutations_fail_before_lookup_or_ledger(self) -> None:
        credits = importlib.import_module("app.routers.credits")
        db = AsyncMock()
        user = SimpleNamespace(id=uuid.uuid4())

        with self.assertRaises(HTTPException) as packages_error:
            await credits.list_packages(region=None, locale=None)
        self.assertEqual(packages_error.exception.status_code, 503)
        self.assertEqual(packages_error.exception.detail["code"], "credit_catalog_unavailable")

        await self._assert_gone(
            credits.purchase_credits(
                credits.PurchaseRequest(package_id="does-not-exist"),
                current_user=user,
                db=db,
            )
        )
        await self._assert_gone(
            credits.add_user_credits(
                credits.AdminAddCreditsRequest(user_id=str(user.id), amount=10),
                _=None,
                db=db,
            )
        )

        db.execute.assert_not_awaited()
        db.flush.assert_not_awaited()


class CapabilityRouteGuardTest(unittest.IsolatedAsyncioTestCase):
    async def _assert_disabled(self, awaitable, capability: str) -> None:
        with self.assertRaises(HTTPException) as raised:
            await awaitable
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "capability_disabled")
        self.assertEqual(raised.exception.detail["capability"], capability)

    async def test_google_start_is_blocked_before_redirect(self) -> None:
        google = importlib.import_module("app.routers.auth.google")

        await self._assert_disabled(google.start_supabase_google_login(), "google_auth")

        db = AsyncMock()
        request = google.SupabaseSessionRequest(access_token="x" * 16)
        await self._assert_disabled(
            google.exchange_supabase_session(request, SimpleNamespace(), db),
            "google_auth",
        )
        db.execute.assert_not_awaited()

    async def test_signed_webhook_and_read_only_credit_surfaces_remain_available(self) -> None:
        payments = importlib.import_module("app.routers.payments")
        credits = importlib.import_module("app.routers.credits")
        db = AsyncMock()
        user = SimpleNamespace(id=uuid.uuid4())

        request = SimpleNamespace(
            headers={"creem-signature": "signed"},
            body=AsyncMock(return_value=b"{}"),
            json=AsyncMock(return_value={"eventType": "checkout.completed"}),
        )
        with patch.object(payments.payment_service, "process_webhook_event", new=AsyncMock(return_value=None)) as webhook:
            result = await payments.creem_webhook(request, db)
        self.assertEqual(result, {"success": True, "purchase_id": None})
        webhook.assert_awaited_once()

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
        upload = importlib.import_module("app.routers.upload")
        gatekeeper = importlib.import_module("app.routers.gatekeeper")
        file = AsyncMock()

        await self._assert_disabled(upload.upload_file(file), "authenticated_upload")
        file.read.assert_not_awaited()
        await self._assert_disabled(upload.upload_multiple_files([file]), "authenticated_upload")

        with patch.object(upload.storage_service, "delete_file") as delete:
            await self._assert_disabled(
                upload.delete_file(upload.DeleteRequest(url="https://example.com/a.jpg"), _=None),
                "authenticated_upload",
            )
        delete.assert_not_called()

        with patch.object(gatekeeper.gatekeeper_service, "check_image_quality", new=AsyncMock()) as check:
            await self._assert_disabled(
                gatekeeper.check_image_quality(gatekeeper.GatekeeperRequest(image_url="https://example.com/a.jpg")),
                "authenticated_upload",
            )
        check.assert_not_awaited()

    async def test_order_creation_is_blocked_before_reservation_or_queue(self) -> None:
        orders = importlib.import_module("app.routers.orders")
        user = SimpleNamespace(id=uuid.uuid4())
        db = AsyncMock()
        request = orders.OrderCreate(template_id="classic", user_images=["https://example.com/a.jpg"])

        with patch.object(orders, "create_order_for_user", new=AsyncMock()) as create:
            await self._assert_disabled(
                orders.create_order(request, BackgroundTasks(), current_user=user, db=db),
                "generation",
            )

        create.assert_not_awaited()
        db.execute.assert_not_awaited()

    async def test_payment_and_subscription_mutations_are_blocked_before_services(self) -> None:
        payments = importlib.import_module("app.routers.payments")
        subscriptions = importlib.import_module("app.routers.subscriptions")
        user = SimpleNamespace(id=uuid.uuid4())
        db = AsyncMock()

        with patch.object(payments.payment_service, "create_checkout", new=AsyncMock()) as checkout:
            await self._assert_disabled(
                payments.create_checkout(
                    payments.CheckoutRequest(package_id="pack_50"),
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
                    current_user=user,
                    db=db,
                ),
                "subscription_billing",
            )
        subscribe.assert_not_awaited()

        with patch.object(subscriptions.subscription_service, "cancel_current_subscription", new=AsyncMock()) as cancel:
            await self._assert_disabled(
                subscriptions.cancel_my_subscription(current_user=user, db=db),
                "subscription_billing",
            )
        cancel.assert_not_awaited()

        manual_calls = (
            payments.manual_checkout_page(purchase_id="purchase", token="token", db=db),
            payments.submit_manual_checkout(purchase_id="purchase", token="token", db=db),
            payments.complete_manual_checkout(
                payments.ManualReviewRequest(purchase_id="purchase"),
                _=None,
                db=db,
            ),
            payments.fail_manual_checkout(
                payments.ManualReviewRequest(purchase_id="purchase", reason="failed"),
                _=None,
                db=db,
            ),
        )
        for call in manual_calls:
            await self._assert_disabled(call, "credit_pack_checkout")

    async def test_worker_is_blocked_before_order_lookup(self) -> None:
        worker = importlib.import_module("app.worker_tasks")

        await self._assert_disabled(worker.run_order_generation("not-a-uuid"), "generation")
        with self.assertRaisesRegex(RuntimeError, "live_portrait_retired"):
            await worker.run_live_portrait_generation("not-a-uuid")

    async def test_admin_generation_and_credit_probes_are_blocked_before_external_calls(self) -> None:
        admin = importlib.import_module("app.routers.admin")

        with patch.object(admin.httpx, "AsyncClient") as client:
            await self._assert_disabled(admin.check_creem_products(), "credit_pack_checkout")
        client.assert_not_called()

        db = AsyncMock()
        http_request = SimpleNamespace(state=SimpleNamespace(admin_actor="test-admin"))
        await self._assert_disabled(
            admin.probe_generation(
                admin.GenerationProbeRequest(image_url="https://example.com/a.jpg"),
                http_request,
                db,
            ),
            "generation",
        )
        await self._assert_disabled(
            admin.grant_credits(
                http_request,
                admin.GrantCreditsRequest(user_id=str(uuid.uuid4()), amount=10),
                db,
            ),
            "credit_pack_checkout",
        )
        await self._assert_disabled(
            admin.regenerate_admin_order(
                str(uuid.uuid4()),
                admin.RegenerateOrderRequest(reason="test"),
                http_request,
                db,
            ),
            "generation",
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

        self.assertIsNone(payload.source_image_urls)
        self.assertIsNone(payload.preview_image_urls)
        self.assertIsNone(payload.final_image_urls)
        self.assertIsNone(payload.preview_master_image_url)
        self.assertIsNone(payload.final_master_image_url)
        self.assertEqual(payload.download_variants, [])
        self.assertFalse(payload.can_download)
        self.assertTrue(payload.download_locked)
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
    async def test_cleanup_is_post_only_authenticated_and_paused_before_deletion(self) -> None:
        ops = importlib.import_module("app.routers.ops")
        cleanup_routes = [route for route in ops.router.routes if route.path == "/ops/cleanup_expired_assets"]
        self.assertEqual(len(cleanup_routes), 1)
        self.assertEqual(cleanup_routes[0].methods, {"POST"})
        db = AsyncMock()

        with patch.object(ops.settings, "cleanup_cron_token", "cleanup-secret"):
            with self.assertRaises(HTTPException) as raised:
                await ops.cleanup_expired_assets(authorization="Bearer cleanup-secret", db=db)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "cleanup_paused")
        db.execute.assert_not_awaited()


class HttpLockdownContractTest(unittest.IsolatedAsyncioTestCase):
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
                        "template_id": "classic",
                        "user_images": ["https://example.invalid/source.jpg"],
                    },
                )
                self.assertEqual(create_response.status_code, 503, create_response.text)
                self.assertEqual(
                    create_response.json()["detail"]["code"],
                    "capability_disabled",
                )
                self.assertEqual(database.statements, [])

                database.statements.clear()
                delete_response = await client.delete(
                    "/api/v1/orders/00000000-0000-0000-0000-000000000000",
                    headers={"X-User-OpenID": "attacker-selected-openid"},
                )
                self.assertEqual(delete_response.status_code, 503, delete_response.text)
                self.assertEqual(delete_response.json()["detail"]["code"], "cleanup_paused")
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
            self.assertEqual(packages.json()["detail"]["error"], "credit_catalog_unavailable")

    async def test_partner_invite_http_routes_are_capability_disabled_not_retired(self) -> None:
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
                self.assertEqual(response.status_code, 503, f"{method} {path}: {response.text}")
                self.assertEqual(response.json()["detail"]["code"], "capability_disabled")
                self.assertEqual(response.json()["detail"]["capability"], "partner_invite")

    async def test_formal_route_matrix_reaches_every_expected_application_guard(self) -> None:
        from app.core import admin_auth
        from app.core.database import get_db
        from app.core.user_auth import get_request_user
        from app.main import app
        from app.routers import ops
        from scripts.release import verify_safe_baseline as verify

        class UnavailableAuthorityDb:
            async def execute(self, _statement):
                raise RuntimeError("authority unavailable in route-matrix test")

        async def unavailable_db():
            yield UnavailableAuthorityDb()

        async def probe_user():
            return SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"))

        original_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = unavailable_db
        app.dependency_overrides[get_request_user] = probe_user
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        try:
            with (
                patch.object(admin_auth.settings, "admin_token", "admin-probe-token"),
                patch.object(ops.settings, "cleanup_cron_token", "cleanup-probe-token"),
            ):
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    for probe in (*verify.GUARDED_ROUTE_PROBES, *verify.RETIRED_ROUTE_PROBES):
                        headers: dict[str, str] = {}
                        if probe.auth_kind == "user":
                            headers["Authorization"] = "Bearer user-probe-token"
                        elif probe.auth_kind == "admin":
                            headers["X-Admin-Token"] = "admin-probe-token"
                        elif probe.auth_kind == "cleanup":
                            headers["Authorization"] = "Bearer cleanup-probe-token"
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
                                response.json()["detail"]["code"],
                                probe.expected_code,
                                probe.name,
                            )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original_overrides)

    async def test_public_high_risk_http_routes_return_structured_503(self) -> None:
        from app.main import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        requests = (
            ("GET", "/api/v1/auth/supabase/google/start", {}),
            ("POST", "/api/v1/auth/supabase/session", {"json": {"access_token": "x" * 16}}),
            (
                "POST",
                "/api/v1/gatekeeper/check",
                {"json": {"image_url": "https://example.com/a.jpg"}},
            ),
            (
                "POST",
                "/api/v1/upload",
                {"files": {"file": ("a.jpg", b"not-read", "image/jpeg")}},
            ),
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for method, path, kwargs in requests:
                response = await client.request(method, path, **kwargs)
                self.assertEqual(response.status_code, 503, f"{method} {path}: {response.text}")
                self.assertEqual(response.json()["detail"]["error"], "capability_disabled")


if __name__ == "__main__":
    unittest.main()
