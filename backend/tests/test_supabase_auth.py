"""Supabase Auth identity mapping tests."""

from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
import uuid

from jose import jwt


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.supabase_auth import (  # noqa: E402
    build_supabase_openid,
    parse_supabase_claims,
    supabase_issuer_from_url,
)
from app.schemas.user import UserRead  # noqa: E402
from app.schemas.auth import LoginResponse  # noqa: E402
from app.routers.auth import _helpers as auth_helpers  # noqa: E402


class SupabaseAuthTest(unittest.TestCase):
    def test_supabase_issuer_is_derived_from_project_url(self) -> None:
        self.assertEqual(
            supabase_issuer_from_url("https://ucqdgdjituqzzsnfprqd.supabase.co/"),
            "https://ucqdgdjituqzzsnfprqd.supabase.co/auth/v1",
        )

    def test_parse_claims_extracts_business_profile(self) -> None:
        claims = parse_supabase_claims(
            {
                "sub": "2f7b0d43-6e77-4c3d-8a44-cfb9d8ed35c7",
                "email": "bride@example.com",
                "app_metadata": {"provider": "google"},
                "user_metadata": {
                    "name": "Bride User",
                    "avatar_url": "https://example.com/avatar.png",
                },
            }
        )

        self.assertEqual(claims.subject, "2f7b0d43-6e77-4c3d-8a44-cfb9d8ed35c7")
        self.assertEqual(claims.email, "bride@example.com")
        self.assertEqual(claims.provider, "google")
        self.assertEqual(claims.nickname, "Bride User")
        self.assertEqual(claims.avatar_url, "https://example.com/avatar.png")

    def test_supabase_openid_is_stable_and_fits_existing_column(self) -> None:
        openid = build_supabase_openid("2f7b0d43-6e77-4c3d-8a44-cfb9d8ed35c7")

        self.assertEqual(openid, "supabase:2f7b0d43-6e77-4c3d-8a44-cfb9d8ed35c7")
        self.assertLessEqual(len(openid), 64)

    def test_long_subject_uses_stable_hash(self) -> None:
        openid = build_supabase_openid("subject-" + ("x" * 200))

        self.assertTrue(openid.startswith("supabase:"))
        self.assertLessEqual(len(openid), 64)

    def test_public_account_contract_exposes_only_web_profile_fields(self) -> None:
        fields = set(UserRead.model_fields)

        self.assertTrue(
            {
                "id",
                "email",
                "role",
                "status",
                "last_login_at",
            }.issubset(fields)
        )
        self.assertTrue(
            {"openid", "unionid", "auth_provider", "auth_subject"}.isdisjoint(fields)
        )

    def test_login_response_and_application_jwt_do_not_expose_internal_identity(self) -> None:
        user = SimpleNamespace(
            id=uuid.uuid4(),
            openid="internal-openid",
            auth_provider="supabase",
            username="bride",
            email="bride@example.com",
        )

        response = auth_helpers._build_login_response(user)
        claims = jwt.decode(
            response.access_token,
            auth_helpers.settings.secret_key,
            algorithms=[auth_helpers.ALGORITHM],
        )

        forbidden = {"openid", "unionid", "auth_provider", "auth_subject"}
        self.assertTrue(forbidden.isdisjoint(LoginResponse.model_fields))
        self.assertTrue(forbidden.isdisjoint(response.model_dump()))
        self.assertTrue(forbidden.isdisjoint(claims))

    def test_supabase_oauth_routes_are_registered(self) -> None:
        from app.routers import api_router

        routes = {route.path for route in api_router.routes}

        self.assertIn("/auth/supabase/google/start", routes)
        self.assertIn("/auth/supabase/session", routes)


if __name__ == "__main__":
    unittest.main()
