from __future__ import annotations

from pathlib import Path
import unittest

from app.schemas.auth import LoginResponse
from app.schemas.user import UserRead


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class WebOnlyContractTest(unittest.TestCase):
    def test_frontend_package_and_manifest_expose_web_only_targets(self) -> None:
        package_source = _read("frontend/package.json")
        package_lock_source = _read("frontend/package-lock.json")
        manifest_source = _read("frontend/src/manifest.json")

        for forbidden in (
            "dev:mp-weixin",
            "build:mp-weixin",
            "@dcloudio/uni-mp-weixin",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, package_source)
                self.assertNotIn(forbidden, package_lock_source)
        self.assertNotIn('"mp-weixin"', manifest_source)

    def test_frontend_does_not_bootstrap_or_serialize_retired_guest_identity(self) -> None:
        session_source = _read("frontend/src/utils/auth/session.ts")
        api_source = _read("frontend/src/utils/api.ts")
        auth_index_source = _read("frontend/src/utils/auth/index.ts")
        identity_source = _read("frontend/src/utils/auth/identity.ts")
        keys_source = _read("frontend/src/utils/auth/_keys.ts")
        supabase_source = _read("frontend/src/utils/supabase.ts")

        for forbidden in (
            "/auth/login",
            "getGuestUserId",
            "GUEST_ID_KEY",
            "X-Visitor-Id",
            "previous_guest_id",
            "previousGuestId",
            "MP-WEIXIN",
            "provider: 'weixin'",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden,
                    "\n".join(
                        (
                            session_source,
                            api_source,
                            auth_index_source,
                            identity_source,
                            keys_source,
                            supabase_source,
                        )
                    ),
                )

    def test_public_auth_and_account_ui_do_not_promise_a_guest_account(self) -> None:
        active_ui_source = "\n".join(
            (
                _read("frontend/src/pages/auth/login.vue"),
                _read("frontend/src/pages/auth/register.vue"),
                _read("frontend/src/pages/account/index.vue"),
            )
        )

        for forbidden in (
            "Continue as guest",
            "Guest account",
            "Guest user",
            "Guest session",
            "temporarily closed",
            "访客订单",
            "访客账号",
            "访客用户",
            "访客会话",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, active_ui_source)

        account_source = _read("frontend/src/pages/account/index.vue")
        self.assertIn("if (!supabaseAuthed.value)", account_source)
        auth_guard = account_source.index("if (!supabaseAuthed.value)")
        protected_load = account_source.index("await Promise.allSettled")
        self.assertLess(auth_guard, protected_load)

    def test_payment_modal_does_not_load_or_mutate_account_state_anonymously(self) -> None:
        payment_source = _read("frontend/src/components/PaymentModal.vue")
        subscription_source = _read("frontend/src/stores/subscription.ts")

        self.assertIn("isSupabaseLoggedIn", payment_source)
        self.assertIn("function requireVerifiedAccount()", payment_source)
        self.assertGreaterEqual(payment_source.count("if (!requireVerifiedAccount()) return;"), 2)
        self.assertIn("if (!isSupabaseLoggedIn()) return;", payment_source)
        self.assertIn("subscriptionStore.clearCurrentSubscription()", payment_source)
        self.assertIn("function clearCurrentSubscription()", subscription_source)

        fetch_start = payment_source.index("async function fetchData()")
        fetch_end = payment_source.index("async function reconcilePendingPurchase()")
        fetch_source = payment_source[fetch_start:fetch_end]
        auth_guard = fetch_source.index("if (accountAuthed.value)")
        self.assertLess(auth_guard, fetch_source.index("'/credits/balance'"))
        self.assertLess(auth_guard, fetch_source.index("fetchCurrentSubscription(true)"))

    def test_orders_page_requires_verified_account_before_network_loading(self) -> None:
        orders_source = _read("frontend/src/pages/orders/orders.vue")

        self.assertIn("isSupabaseLoggedIn", orders_source)
        fetch_start = orders_source.index("async function fetchOrders()")
        fetch_end = orders_source.index("function getStatusText")
        fetch_source = orders_source[fetch_start:fetch_end]
        auth_guard = fetch_source.index("if (!isSupabaseLoggedIn())")
        self.assertLess(auth_guard, fetch_source.index("templateStore.fetchTemplates()"))
        self.assertLess(auth_guard, fetch_source.index("get<OrdersResponse>('/orders'"))
        self.assertIn("authRequired.value = true;", fetch_source[: fetch_source.index("return;")])

    def test_backend_google_exchange_has_no_guest_merge_contract(self) -> None:
        from app.schemas.auth import SupabaseSessionRequest

        self.assertNotIn("previous_guest_id", SupabaseSessionRequest.model_fields)

        guest_source = _read("backend/app/routers/auth/guest.py")
        google_source = _read("backend/app/routers/auth/google.py")
        self.assertNotIn("LoginRequest", guest_source)
        self.assertNotIn("LoginResponse", guest_source)
        self.assertNotIn("_merge_guest_account", google_source)
        self.assertNotIn("previous_guest_id", google_source)
        self.assertFalse((ROOT / "backend/app/routers/auth/merge.py").exists())

    def test_retired_openid_routes_and_frontend_dto_expose_no_openid_contract(self) -> None:
        from app.main import app

        specification = app.openapi()
        forbidden = {"openid", "unionid", "auth_provider", "auth_subject"}
        account_source = _read("frontend/src/pages/account/index.vue")

        self.assertNotIn("/api/v1/users/", specification["paths"])
        self.assertNotIn("/api/v1/users/{user_id}", specification["paths"])
        self.assertTrue(forbidden.isdisjoint(set(UserRead.model_fields)))
        self.assertTrue(forbidden.isdisjoint(set(LoginResponse.model_fields)))
        for field in forbidden:
            with self.subTest(field=field):
                self.assertNotIn(f"{field}:", account_source)
                self.assertNotIn(f"{field}?:", account_source)

    def test_active_models_do_not_describe_a_wechat_product(self) -> None:
        active_model_source = "\n".join(
            (
                _read("backend/app/models/user.py"),
                _read("backend/app/models/order.py"),
            )
        ).lower()

        self.assertNotIn("wechat", active_model_source)
        self.assertNotIn("mini program", active_model_source)


if __name__ == "__main__":
    unittest.main()
