"""Soft account-closure contracts."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.account_tombstone import AccountTombstone  # noqa: E402
from app.models.auth_refresh_token import AuthRefreshToken, RefreshTokenStatus  # noqa: E402
from app.models.auth_session import AuthSession  # noqa: E402
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_identity import UserIdentity  # noqa: E402
from app.services.account_closure_service import close_account  # noqa: E402


def _statement_entity(statement):
    for description in getattr(statement, "column_descriptions", ()):
        entity = description.get("entity")
        if entity is not None:
            return entity
    return None


class _Rows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _FakeDb:
    def __init__(self, *, scalars=None, rows=None):
        self._scalar_results = {
            model: deque(values) for model, values in (scalars or {}).items()
        }
        self._row_results = {
            model: deque([list(value) for value in values])
            for model, values in (rows or {}).items()
        }
        self.added = []
        self.deleted = []
        self.commit_count = 0
        self.rollback_count = 0

    async def scalar(self, statement):
        queue = self._scalar_results.get(_statement_entity(statement), deque())
        return queue.popleft() if queue else None

    async def scalars(self, statement):
        queue = self._row_results.get(_statement_entity(statement), deque())
        return _Rows(queue.popleft() if queue else [])

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def delete(self, value):
        self.deleted.append(value)

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1


NOW = datetime(2026, 7, 13, 17, 30, tzinfo=timezone.utc)


class AccountClosureInterfaceTest(unittest.TestCase):
    def test_task8_account_closure_interface_exists(self) -> None:
        module_name = "app.services.account_closure_service"
        self.assertIsNotNone(find_spec(module_name), f"missing {module_name}")
        module = import_module(module_name)
        self.assertTrue(hasattr(module, "close_account"))

    def test_account_close_route_requires_explicit_confirmation(self) -> None:
        from app.routers import api_router
        from app import schemas as schemas_package
        from tests.route_contract import effective_routes

        routes = {
            (route.path, frozenset(route.methods or set()))
            for route in effective_routes(api_router)
        }
        self.assertIn(("/users/me/close", frozenset({"POST"})), routes)
        user_schema = import_module("app.schemas.user")
        self.assertTrue(hasattr(user_schema, "AccountCloseRequest"))
        request_type = user_schema.AccountCloseRequest
        request_type(confirmation="CLOSE MY ACCOUNT")
        with self.assertRaises(ValidationError):
            request_type(confirmation="close")
        self.assertIsNotNone(schemas_package)

    def test_account_page_exposes_soft_close_without_claiming_media_deleted(self) -> None:
        source = (BACKEND_DIR.parent / "frontend/src/pages/account/index.vue").read_text(
            encoding="utf-8"
        )
        self.assertIn("/users/me/close", source)
        self.assertIn("CLOSE MY ACCOUNT", source)
        self.assertIn("closeAccount", source)
        self.assertIn("media cleanup remains pending", source.lower())


class AccountClosurePolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_closure_revokes_identity_sessions_and_anonymizes_profile(self) -> None:
        user = User(
            id=uuid4(), status="active", role="admin", openid="legacy-openid",
            username="legacy-user", password="secret-hash", auth_provider="legacy",
            auth_subject="legacy-subject", email="person@example.com",
            email_verified_at=NOW, unionid="union", nickname="Person",
            avatar_url="https://assets.invalid/avatar.jpg", last_login_at=NOW,
        )
        identity = UserIdentity(
            id=uuid4(), user_id=user.id, provider="supabase", subject="google-subject",
            verified_email_snapshot=user.email,
        )
        session = AuthSession(
            id=uuid4(), user_id=user.id, family_id=uuid4(), token_version=2,
            csrf_token_hash="a" * 64, expires_at=NOW + timedelta(days=1),
        )
        active = AuthRefreshToken(
            id=uuid4(), session_id=session.id, generation=2, token_hash="b" * 64,
            status=RefreshTokenStatus.ACTIVE, expires_at=NOW + timedelta(days=1),
        )
        used = AuthRefreshToken(
            id=uuid4(), session_id=session.id, generation=1, token_hash="c" * 64,
            status=RefreshTokenStatus.USED, used_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(days=1), replacement_token_id=active.id,
        )
        db = _FakeDb(
            scalars={User: [user], AccountTombstone: [None]},
            rows={UserIdentity: [[identity]], AuthSession: [[session]], AuthRefreshToken: [[active, used]]},
        )

        tombstone = await close_account(
            db, user_id=user.id, closure_reason="USER_REQUESTED",
            audit_request_id="req-close", now=NOW,
        )

        self.assertIsInstance(tombstone, AccountTombstone)
        self.assertTrue(tombstone.media_cleanup_pending)
        self.assertEqual(tombstone.closed_at, NOW)
        self.assertEqual(identity.revoked_at, NOW)
        self.assertEqual(session.revoked_at, NOW)
        self.assertEqual(session.token_version, 3)
        self.assertEqual({active.status, used.status}, {RefreshTokenStatus.REVOKED})
        self.assertEqual({active.revoked_at, used.revoked_at}, {NOW})
        self.assertEqual(user.status, "closed")
        self.assertEqual(user.role, "user")
        for field in (
            "openid", "username", "password", "auth_provider", "auth_subject",
            "email", "email_verified_at", "unionid", "nickname", "avatar_url", "last_login_at",
        ):
            self.assertIsNone(getattr(user, field), field)
        self.assertFalse(db.deleted, "soft closure must not delete financial or media rows")

    async def test_closure_requests_private_asset_deletion_through_shared_guard(self) -> None:
        user = User(id=uuid4(), status="active", role="user")
        asset = MediaAsset(
            id=uuid4(),
            owner_user_id=user.id,
            role=MediaAssetRole.SOURCE,
            status=MediaAssetStatus.ACTIVE,
            storage_provider="s3",
            object_key=f"users/{user.id}/source.jpg",
            sha256="a" * 64,
            mime_type="image/jpeg",
            byte_size=128,
            width=10,
            height=10,
        )
        db = _FakeDb(
            scalars={User: [user], AccountTombstone: [None]},
            rows={
                UserIdentity: [[]],
                AuthSession: [[]],
                AuthRefreshToken: [[]],
                MediaAsset: [[asset]],
            },
        )
        request_deletion = AsyncMock()

        with patch(
            "app.services.account_closure_service.request_asset_deletion",
            request_deletion,
        ):
            tombstone = await close_account(
                db,
                user_id=user.id,
                closure_reason="USER_REQUESTED",
                audit_request_id="req-media-cleanup",
                now=NOW,
            )

        request_deletion.assert_awaited_once_with(
            db,
            asset.id,
            reason="account_closure",
            now=NOW,
        )
        self.assertTrue(tombstone.media_cleanup_pending)
        self.assertGreaterEqual(db.commit_count, 1)

    async def test_repeated_closure_is_idempotent_and_keeps_cleanup_pending(self) -> None:
        user = User(id=uuid4(), status="closed", role="user")
        existing = AccountTombstone(
            user_id=user.id, closure_reason="USER_REQUESTED", closed_at=NOW,
            media_cleanup_pending=True, audit_request_id="req-first",
        )
        db = _FakeDb(
            scalars={User: [user], AccountTombstone: [existing]},
            rows={UserIdentity: [[]], AuthSession: [[]], AuthRefreshToken: [[]]},
        )

        tombstone = await close_account(
            db, user_id=user.id, closure_reason="USER_REQUESTED",
            audit_request_id="req-second", now=NOW + timedelta(minutes=1),
        )

        self.assertIs(tombstone, existing)
        self.assertTrue(existing.media_cleanup_pending)
        self.assertEqual(existing.closed_at, NOW)
        self.assertFalse(db.added)
        self.assertFalse(db.deleted)


if __name__ == "__main__":
    unittest.main()
