"""Authenticated owner-only source-asset read tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.routers import media
from app.services.media_asset_service import AssetAccessError


NOW = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc)


def _asset(*, owner_id: uuid.UUID, role=MediaAssetRole.SOURCE) -> MediaAsset:
    return MediaAsset(
        id=uuid.uuid4(),
        owner_user_id=owner_id,
        role=role,
        storage_provider="s3",
        object_key="private/never-expose.jpg",
        sha256="c" * 64,
        mime_type="image/jpeg",
        byte_size=12,
        width=20,
        height=30,
        access_level="private",
        policy_version="source-v1",
        expires_at=NOW + timedelta(days=1),
        status=MediaAssetStatus.ACTIVE,
    )


class PrivateAssetReadTest(unittest.IsolatedAsyncioTestCase):
    def test_owner_delete_route_is_registered(self) -> None:
        routes = {
            (route.path, frozenset(route.methods or set()))
            for route in media.router.routes
        }
        self.assertIn(("/media/{asset_id}", frozenset({"DELETE"})), routes)

    def test_authorization_allows_only_active_unrevoked_owned_source(self) -> None:
        from app.services.media_asset_service import authorize_owner_asset_read

        owner_id = uuid.uuid4()
        user = SimpleNamespace(id=owner_id)
        source = _asset(owner_id=owner_id)
        self.assertIs(authorize_owner_asset_read(user, source, now=NOW), source)

        cases = [
            (SimpleNamespace(id=uuid.uuid4()), source, "asset_forbidden"),
            (user, _asset(owner_id=owner_id, role=MediaAssetRole.CANDIDATE), "asset_role_forbidden"),
        ]
        revoked = _asset(owner_id=owner_id)
        revoked.read_revoked_at = NOW
        cases.append((user, revoked, "asset_unavailable"))
        for case_user, asset, code in cases:
            with self.subTest(code=code), self.assertRaises(AssetAccessError) as raised:
                authorize_owner_asset_read(case_user, asset, now=NOW)
            self.assertEqual(raised.exception.code, code)

    async def test_route_streams_private_bytes_without_object_key_or_url(self) -> None:
        owner_id = uuid.uuid4()
        asset = _asset(owner_id=owner_id)
        private = SimpleNamespace(asset=asset, content=b"private-image", mime_type="image/jpeg")
        loader = AsyncMock(return_value=private)
        with patch.object(media, "load_owner_source_asset", loader):
            response = await media.read_owner_source_asset(
                asset.id,
                SimpleNamespace(id=owner_id),
                AsyncMock(),
            )

        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(chunk.encode() if isinstance(chunk, str) else chunk for chunk in chunks)
        self.assertEqual(body, b"private-image")
        self.assertEqual(response.headers["cache-control"], "private, no-store, max-age=0")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertNotIn(b"never-expose", body)
        self.assertNotIn(b"http", body)
        loader.assert_awaited_once()

    async def test_owner_delete_request_uses_shared_deletion_guard(self) -> None:
        from app.services import media_asset_service

        owner_id = uuid.uuid4()
        asset = _asset(owner_id=owner_id)
        deletion = AsyncMock(return_value=SimpleNamespace(code="deletion_requested"))
        with (
            patch.object(media_asset_service, "_asset_by_id", AsyncMock(return_value=asset)),
            patch.object(media_asset_service, "request_asset_deletion", deletion),
        ):
            result = await media_asset_service.request_owner_asset_deletion(
                AsyncMock(),
                user=SimpleNamespace(id=owner_id),
                asset_id=asset.id,
                now=NOW,
            )

        self.assertEqual(result.code, "deletion_requested")
        deletion.assert_awaited_once()

    async def test_cross_user_delete_is_denied_before_state_transition(self) -> None:
        from app.services import media_asset_service

        asset = _asset(owner_id=uuid.uuid4())
        deletion = AsyncMock()
        with (
            patch.object(media_asset_service, "_asset_by_id", AsyncMock(return_value=asset)),
            patch.object(media_asset_service, "request_asset_deletion", deletion),
            self.assertRaises(AssetAccessError) as raised,
        ):
            await media_asset_service.request_owner_asset_deletion(
                AsyncMock(),
                user=SimpleNamespace(id=uuid.uuid4()),
                asset_id=asset.id,
                now=NOW,
            )

        self.assertEqual(raised.exception.code, "asset_forbidden")
        deletion.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
