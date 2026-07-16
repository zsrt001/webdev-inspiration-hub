"""Cookie-only local-session, refresh rotation, and OAuth intent contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ast
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import Response


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.session_auth import (  # noqa: E402
    decode_access_token,
    validate_access_claim_shape,
    validate_csrf_secret,
)
from app.models.auth_refresh_token import AuthRefreshToken, RefreshTokenStatus  # noqa: E402
from app.models.auth_session import AuthSession  # noqa: E402
from app.services.auth_session_service import (  # noqa: E402
    ACCESS_COOKIE,
    ACCESS_TTL,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    REFRESH_TTL,
    apply_refresh_rotation,
    encode_access_token,
    revoke_session_family,
    set_session_cookies,
)
from app.services.oauth_intent_service import (  # noqa: E402
    OAuthIntentError,
    validate_intent_row,
    validate_redirect_path,
)


class CookieSessionContractTest(unittest.TestCase):
    def test_session_ttls_match_contract(self) -> None:
        self.assertEqual(ACCESS_TTL, timedelta(minutes=15))
        self.assertEqual(REFRESH_TTL, timedelta(days=30))

    def test_access_claims_require_all_revocation_coordinates(self) -> None:
        with self.assertRaises(ValueError):
            validate_access_claim_shape({"sub": "user-only"})

        claims = validate_access_claim_shape(
            {
                "sub": "00000000-0000-4000-8000-000000000001",
                "sid": "00000000-0000-4000-8000-000000000002",
                "jti": "00000000-0000-4000-8000-000000000003",
                "token_version": 1,
                "iat": 1,
                "exp": 2,
            },
            verify_time=False,
        )
        self.assertEqual(claims.token_version, 1)

        invalid = {
            "sub": "not-a-uuid",
            "sid": str(uuid4()),
            "jti": str(uuid4()),
            "token_version": True,
            "iat": 1,
            "exp": 2,
        }
        with self.assertRaises(ValueError):
            validate_access_claim_shape(invalid, verify_time=False)

    def test_access_token_round_trip_and_signature_rejection(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        user_id = uuid4()
        session = AuthSession(
            id=uuid4(),
            user_id=user_id,
            family_id=uuid4(),
            token_version=3,
            csrf_token_hash="a" * 64,
            expires_at=now + REFRESH_TTL,
        )

        settings = SimpleNamespace(secret_key="s" * 32)
        with patch("app.core.session_auth.get_settings", return_value=settings):
            token = encode_access_token(session, user_id, now=now)
            claims = decode_access_token(token, now=now)

        self.assertEqual(claims.user_id, user_id)
        self.assertEqual(claims.session_id, session.id)
        self.assertEqual(claims.token_version, 3)
        header, payload, signature = token.split(".")
        replacement = "A" if signature[0] != "A" else "B"
        tampered = f"{header}.{payload}.{replacement}{signature[1:]}"
        with (
            patch("app.core.session_auth.get_settings", return_value=settings),
            self.assertRaises(ValueError),
        ):
            decode_access_token(tampered, now=now)

        with (
            patch(
                "app.core.session_auth.get_settings",
                return_value=SimpleNamespace(secret_key="too-short"),
            ),
            self.assertRaisesRegex(ValueError, "at least 32 bytes"),
        ):
            encode_access_token(session, user_id, now=now)

    def test_session_cookies_have_exact_security_and_path_contract(self) -> None:
        response = Response()
        set_session_cookies(
            response,
            access_token="access.jwt.value",
            refresh_token="refresh-secret",
            csrf_token="csrf-secret",
        )
        headers = response.headers.getlist("set-cookie")
        by_name = {header.split("=", 1)[0]: header for header in headers}

        access = by_name[ACCESS_COOKIE].lower()
        refresh = by_name[REFRESH_COOKIE].lower()
        csrf = by_name[CSRF_COOKIE].lower()
        self.assertIn("httponly", access)
        self.assertIn("secure", access)
        self.assertIn("samesite=lax", access)
        self.assertIn("path=/api/v1", access)
        self.assertIn("max-age=900", access)
        self.assertIn("httponly", refresh)
        self.assertIn("secure", refresh)
        self.assertIn("samesite=strict", refresh)
        self.assertIn("path=/api/v1/auth/refresh", refresh)
        self.assertIn("max-age=2592000", refresh)
        self.assertNotIn("httponly", csrf)
        self.assertIn("secure", csrf)
        self.assertIn("samesite=lax", csrf)
        self.assertIn("path=/", csrf)

    def test_refresh_rotation_retains_used_hash_and_invalidates_old_access_and_csrf(self) -> None:
        now = datetime.now(timezone.utc)
        session = AuthSession(
            id=uuid4(),
            user_id=uuid4(),
            family_id=uuid4(),
            token_version=1,
            csrf_token_hash="a" * 64,
            expires_at=now + REFRESH_TTL,
        )
        current = AuthRefreshToken(
            id=uuid4(),
            session_id=session.id,
            generation=1,
            token_hash="b" * 64,
            status=RefreshTokenStatus.ACTIVE,
            expires_at=now + REFRESH_TTL,
        )
        replacement_id = uuid4()

        apply_refresh_rotation(
            session,
            current,
            replacement_token_id=replacement_id,
            csrf_token_hash="c" * 64,
            now=now,
        )

        self.assertEqual(current.status, RefreshTokenStatus.USED)
        self.assertEqual(current.token_hash, "b" * 64)
        self.assertEqual(current.used_at, now)
        self.assertEqual(current.replacement_token_id, replacement_id)
        self.assertEqual(session.token_version, 2)
        self.assertEqual(session.csrf_token_hash, "c" * 64)

    def test_reused_refresh_revokes_the_whole_family_without_deleting_hashes(self) -> None:
        now = datetime.now(timezone.utc)
        session = AuthSession(
            id=uuid4(), user_id=uuid4(), family_id=uuid4(), token_version=2,
            csrf_token_hash="a" * 64, expires_at=now + REFRESH_TTL,
        )
        used = AuthRefreshToken(
            id=uuid4(), session_id=session.id, generation=1, token_hash="b" * 64,
            status=RefreshTokenStatus.USED, expires_at=now + REFRESH_TTL, used_at=now,
        )
        active = AuthRefreshToken(
            id=uuid4(), session_id=session.id, generation=2, token_hash="c" * 64,
            status=RefreshTokenStatus.ACTIVE, expires_at=now + REFRESH_TTL,
        )

        revoke_session_family(session, [used, active], now=now)

        self.assertEqual(session.revoked_at, now)
        self.assertEqual(session.token_version, 3)
        self.assertEqual({used.status, active.status}, {RefreshTokenStatus.REVOKED})
        self.assertEqual({used.token_hash, active.token_hash}, {"b" * 64, "c" * 64})

    def test_csrf_requires_matching_cookie_header_and_session_hash(self) -> None:
        from hashlib import sha256

        raw = "csrf-secret"
        digest = sha256(raw.encode("utf-8")).hexdigest()
        validate_csrf_secret(digest, raw, raw)
        for cookie, header in ((raw, "wrong"), ("wrong", raw), ("", raw), (raw, "")):
            with self.assertRaises(ValueError):
                validate_csrf_secret(digest, cookie, header)


class OAuthIntentContractTest(unittest.TestCase):
    def test_redirect_is_local_and_rejects_confusable_or_api_paths(self) -> None:
        self.assertEqual(validate_redirect_path("/pages/account/index"), "/pages/account/index")
        for path in ("https://evil.example", "//evil.example", "/\\evil", "/api/v1/me", "/auth/callback", "/ok\nset-cookie:x"):
            with self.subTest(path=path), self.assertRaises(OAuthIntentError):
                validate_redirect_path(path)

    def test_wrong_browser_reused_and_expired_intent_fail(self) -> None:
        from hashlib import sha256

        now = datetime.now(timezone.utc)
        token = "intent-secret"
        binding = "browser-secret"
        row = SimpleNamespace(
            token_hash=sha256(token.encode()).hexdigest(),
            browser_binding_hash=sha256(binding.encode()).hexdigest(),
            expires_at=now + timedelta(minutes=10),
            consumed_at=None,
        )
        validate_intent_row(row, token=token, browser_binding=binding, now=now)

        for changed in (
            {"browser_binding": "other-browser"},
            {"token": "other-token"},
            {"now": now + timedelta(minutes=11)},
        ):
            with self.assertRaises(OAuthIntentError):
                validate_intent_row(
                    row,
                    token=changed.get("token", token),
                    browser_binding=changed.get("browser_binding", binding),
                    now=changed.get("now", now),
                )
        row.consumed_at = now
        with self.assertRaises(OAuthIntentError):
            validate_intent_row(row, token=token, browser_binding=binding, now=now)

    def test_session_response_schema_never_contains_bearer_tokens(self) -> None:
        from app.schemas.auth import SupabaseSessionRequest

        request_fields = set(SupabaseSessionRequest.model_fields)
        self.assertEqual(request_fields, {"access_token", "intent_token"})
        source = (ROOT / "backend/app/routers/auth/session.py").read_text(encoding="utf-8")
        self.assertNotIn("LoginResponse", source)
        self.assertNotIn('"access_token"', source)
        self.assertNotIn("token_type", source)

    def test_oauth_intent_is_rate_limited_before_creating_a_database_row(self) -> None:
        source = (ROOT / "backend/app/routers/auth/google.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        route = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "create_google_oauth_intent"
        )
        segment = ast.get_source_segment(source, route) or ""
        self.assertLess(segment.index("_enforce_intent_rate_limit"), segment.index("create_oauth_intent"))
        self.assertNotIn("record_account_risk_event", source)

    def test_acceptance_cohort_consumes_binding_for_existing_or_new_identity(self) -> None:
        source = (ROOT / "backend/app/routers/auth/google.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        route = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "exchange_supabase_session"
        )
        segment = ast.get_source_segment(source, route) or ""
        self.assertIn("if decision.state is FeatureFlagState.ACCEPTANCE_COHORT", segment)
        self.assertNotIn("if user is None and decision.state", segment)
        self.assertIn("consume_binding_row(binding, user.id", segment)
        self.assertIn("acceptance_binding_id=binding.id if binding is not None else None", segment)

    def test_logout_revokes_the_verified_access_session_without_refresh_cookie(self) -> None:
        service_source = (ROOT / "backend/app/services/auth_session_service.py").read_text(encoding="utf-8")
        service_tree = ast.parse(service_source)
        logout_service = next(
            node for node in ast.walk(service_tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "logout_session"
        )
        service_segment = ast.get_source_segment(service_source, logout_service) or ""
        self.assertIn("auth_session_id", service_segment)
        self.assertNotIn("REFRESH_COOKIE", service_segment)
        self.assertIn("revoke_session_family", service_segment)

        route_source = (ROOT / "backend/app/routers/auth/session.py").read_text(encoding="utf-8")
        route_tree = ast.parse(route_source)
        logout_route = next(
            node for node in ast.walk(route_tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "logout_browser_session"
        )
        route_segment = ast.get_source_segment(route_source, logout_route) or ""
        self.assertIn("Depends(get_session_user)", route_segment)


if __name__ == "__main__":
    unittest.main()
