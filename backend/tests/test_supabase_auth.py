"""Strict Supabase broker-claim and normalized identity contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.supabase_auth import (  # noqa: E402
    SupabaseAuthError,
    parse_supabase_claims,
    supabase_issuer_from_url,
)
from app.core.google_identity import normalize_google_email  # noqa: E402
from app.models.user_identity import UserIdentity  # noqa: E402
from app.schemas.user import UserRead  # noqa: E402


SUBJECT = "2f7b0d43-6e77-4c3d-8a44-cfb9d8ed35c7"
SESSION_ID = "4c57d8e7-94e7-4e89-b1ac-5421af6a5c31"


def valid_claims(now: datetime) -> dict:
    issued_at = int((now - timedelta(seconds=30)).timestamp())
    return {
        "iss": "https://project-ref.supabase.co/auth/v1",
        "aud": "authenticated",
        "exp": issued_at + 3600,
        "iat": issued_at,
        "sub": SUBJECT,
        "role": "authenticated",
        "session_id": SESSION_ID,
        "email": "bride@example.com",
        "is_anonymous": False,
        "app_metadata": {"provider": "google", "providers": ["google"]},
        "user_metadata": {
            "name": "Bride User",
            "avatar_url": "https://example.com/avatar.png",
            "email_verified": True,
        },
        "amr": [{"method": "oauth", "timestamp": issued_at}],
    }


def valid_user_record() -> dict:
    return {
        "id": SUBJECT,
        "email": "bride@example.com",
        "email_confirmed_at": "2026-07-13T00:00:00Z",
        "app_metadata": {"provider": "google", "providers": ["google"]},
        "user_metadata": {
            "name": "Bride User",
            "avatar_url": "https://example.com/avatar.png",
            "email_verified": True,
        },
        "identities": [
            {
                "provider": "google",
                "identity_data": {
                    "email": "bride@example.com",
                    "email_verified": True,
                },
            }
        ],
    }


class SupabaseAuthTest(unittest.TestCase):
    def test_supabase_issuer_is_derived_from_project_url(self) -> None:
        self.assertEqual(
            supabase_issuer_from_url("https://project-ref.supabase.co/"),
            "https://project-ref.supabase.co/auth/v1",
        )

    def test_parse_claims_accepts_only_fresh_verified_google_broker_identity(self) -> None:
        now = datetime.now(timezone.utc)
        claims = parse_supabase_claims(
            valid_claims(now),
            valid_user_record(),
            expected_issuer="https://project-ref.supabase.co/auth/v1",
            expected_audience="authenticated",
            now=now,
        )

        self.assertEqual(claims.subject, SUBJECT)
        self.assertEqual(claims.session_id, SESSION_ID)
        self.assertEqual(claims.email, "bride@example.com")
        self.assertEqual(claims.provider, "google")
        self.assertEqual(claims.identity_provider, "supabase")
        self.assertTrue(claims.broker_verified)
        self.assertEqual(claims.nickname, "Bride User")

    def test_missing_or_stale_broker_session_claims_fail_closed(self) -> None:
        now = datetime.now(timezone.utc)
        cases: list[tuple[str, dict]] = []
        missing_session = valid_claims(now)
        missing_session.pop("session_id")
        cases.append(("session", missing_session))
        stale = valid_claims(now)
        stale["iat"] = int((now - timedelta(minutes=11)).timestamp())
        cases.append(("stale", stale))
        future = valid_claims(now)
        future["iat"] = int((now + timedelta(minutes=2)).timestamp())
        cases.append(("future", future))

        for label, payload in cases:
            with self.subTest(label=label), self.assertRaises(SupabaseAuthError):
                parse_supabase_claims(
                    payload,
                    valid_user_record(),
                    expected_issuer="https://project-ref.supabase.co/auth/v1",
                    expected_audience="authenticated",
                    now=now,
                )

    def test_non_google_anonymous_or_unverified_identity_fails_closed(self) -> None:
        now = datetime.now(timezone.utc)

        wrong_provider = valid_claims(now)
        wrong_provider["app_metadata"] = {"provider": "email", "providers": ["email"]}
        no_oauth_amr = valid_claims(now)
        no_oauth_amr["amr"] = [{"method": "password", "timestamp": no_oauth_amr["iat"]}]
        anonymous = valid_claims(now)
        anonymous["is_anonymous"] = True
        unverified_user = valid_user_record()
        unverified_user["email_confirmed_at"] = None
        unverified_user["user_metadata"] = {"email_verified": False}

        for payload, user_record in (
            (wrong_provider, valid_user_record()),
            (no_oauth_amr, valid_user_record()),
            (anonymous, valid_user_record()),
            (valid_claims(now), unverified_user),
        ):
            with self.assertRaises(SupabaseAuthError):
                parse_supabase_claims(
                    payload,
                    user_record,
                    expected_issuer="https://project-ref.supabase.co/auth/v1",
                    expected_audience="authenticated",
                    now=now,
                )

    def test_broker_user_record_must_match_signed_subject_and_email(self) -> None:
        now = datetime.now(timezone.utc)
        for mismatch in (
            {**valid_user_record(), "id": "f6dc93c5-1b1d-41cf-837c-2d934494b858"},
            {**valid_user_record(), "email": "other@example.com"},
        ):
            with self.assertRaises(SupabaseAuthError):
                parse_supabase_claims(
                    valid_claims(now),
                    mismatch,
                    expected_issuer="https://project-ref.supabase.co/auth/v1",
                    expected_audience="authenticated",
                    now=now,
                )

    def test_selected_google_identity_must_match_and_be_verified(self) -> None:
        now = datetime.now(timezone.utc)
        mismatched = valid_user_record()
        mismatched["identities"][0]["identity_data"]["email"] = "other@example.com"
        unverified = valid_user_record()
        unverified["identities"][0]["identity_data"]["email_verified"] = "true"
        duplicate = valid_user_record()
        duplicate["identities"].append(duplicate["identities"][0].copy())

        for user_record in (mismatched, unverified, duplicate):
            with self.assertRaises(SupabaseAuthError):
                parse_supabase_claims(
                    valid_claims(now),
                    user_record,
                    expected_issuer="https://project-ref.supabase.co/auth/v1",
                    expected_audience="authenticated",
                    now=now,
                )

    def test_google_email_normalization_is_ascii_only_and_preserves_aliases(self) -> None:
        self.assertEqual(normalize_google_email(" Bride+Trial@Example.COM "), "bride+trial@example.com")
        self.assertNotEqual(
            normalize_google_email("bride+trial@example.com"),
            normalize_google_email("bride@example.com"),
        )
        for value in ("bride＠example.com", "Bride <bride@example.com>", "a..b@example.com"):
            with self.assertRaises(ValueError):
                normalize_google_email(value)

    def test_normalized_identity_is_provider_subject_only(self) -> None:
        columns = set(UserIdentity.__table__.columns.keys())
        self.assertTrue({"provider", "subject", "user_id"}.issubset(columns))
        self.assertIn("uq_user_identities_provider_subject", {
            constraint.name for constraint in UserIdentity.__table__.constraints
        })

        active_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "backend/app/core/supabase_auth.py",
                ROOT / "backend/app/core/user_auth.py",
                ROOT / "backend/app/core/admin_auth.py",
                ROOT / "backend/app/routers/auth/google.py",
            )
        )
        self.assertNotIn("build_supabase_openid", active_sources)
        self.assertNotIn("User.openid", active_sources)

    def test_user_read_exposes_canonical_account_fields_only(self) -> None:
        fields = set(UserRead.model_fields)
        self.assertTrue({"id", "email", "role", "status", "last_login_at"}.issubset(fields))
        self.assertTrue({"openid", "unionid", "auth_provider", "auth_subject"}.isdisjoint(fields))

    def test_cookie_auth_routes_are_registered_and_legacy_start_route_is_removed(self) -> None:
        from app.routers import api_router
        from tests.route_contract import effective_paths

        routes = effective_paths(api_router)
        self.assertTrue(
            {
                "/auth/oauth-intents",
                "/auth/supabase/session",
                "/auth/refresh",
                "/auth/logout",
                "/auth/me",
            }.issubset(routes)
        )
        self.assertNotIn("/auth/supabase/google/start", routes)


class SupabaseIdentityLockTest(unittest.IsolatedAsyncioTestCase):
    async def test_identity_advisory_lock_uses_postgresql_safe_text(self) -> None:
        from app.routers.auth.google import _locked_identity_user

        lock_result = MagicMock()
        identity_result = MagicMock()
        identity_result.one_or_none.return_value = None
        database = AsyncMock()
        database.execute = AsyncMock(side_effect=(lock_result, identity_result))

        identity, user = await _locked_identity_user(
            database,
            SimpleNamespace(subject=SUBJECT),
        )

        self.assertIsNone(identity)
        self.assertIsNone(user)
        lock_parameters = database.execute.await_args_list[0].args[1]
        self.assertEqual(lock_parameters["key"], f"vowpic.identity.supabase:{SUBJECT}")
        self.assertNotIn("\x00", lock_parameters["key"])


if __name__ == "__main__":
    unittest.main()
