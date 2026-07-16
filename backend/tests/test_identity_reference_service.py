"""Private MediaAsset identity-reference pack tests."""

from __future__ import annotations

from io import BytesIO
import inspect
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from PIL import Image

from app.models.media_asset import MediaAssetRole
from app.services import identity_reference_service as service


def _portrait_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (800, 1200), color)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


class IdentityReferenceServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_couple_pack_contains_only_private_asset_ids_and_lineage(self) -> None:
        owner_id = uuid.uuid4()
        source_ids = [uuid.uuid4(), uuid.uuid4()]
        sources = {
            source_ids[0]: SimpleNamespace(id=source_ids[0], role=MediaAssetRole.SOURCE),
            source_ids[1]: SimpleNamespace(id=source_ids[1], role=MediaAssetRole.SOURCE),
        }
        contents = {
            source_ids[0]: _portrait_bytes((220, 180, 170)),
            source_ids[1]: _portrait_bytes((120, 140, 180)),
        }

        async def fake_load(_db, *, owner_user_id, asset_id):
            self.assertEqual(owner_user_id, owner_id)
            return SimpleNamespace(
                asset=sources[asset_id],
                content=contents[asset_id],
                mime_type="image/jpeg",
            )

        stored: list[tuple[uuid.UUID, list[tuple[MediaAssetRole, object]]]] = []

        async def fake_store(_db, *, owner_user_id, parent_asset, derivatives):
            self.assertEqual(owner_user_id, owner_id)
            stored.append((parent_asset.id, derivatives))
            return [
                SimpleNamespace(id=uuid.uuid4(), parent_asset_id=parent_asset.id)
                for _role, _validated in derivatives
            ]

        with (
            patch.object(service, "load_owned_asset_bytes", AsyncMock(side_effect=fake_load)),
            patch.object(service, "store_private_derivatives", AsyncMock(side_effect=fake_store)),
        ):
            pack = await service.build_identity_reference_pack(
                object(),
                owner_user_id=owner_id,
                source_asset_ids=source_ids,
                is_couple_request=True,
                couple_flow="remote",
            )

        self.assertEqual(pack["kind"], "couple_remote")
        self.assertEqual(pack["role_order"], ["bride", "groom"])
        self.assertEqual(pack["identity_order"], ["person_a", "person_b"])
        self.assertEqual(pack["subject_count"], 2)
        self.assertEqual(len(stored), 2)
        self.assertTrue(all(len(derivatives) == 2 for _source, derivatives in stored))
        self.assertTrue(
            all(
                role == MediaAssetRole.INTERMEDIATE
                for _source, derivatives in stored
                for role, _validated in derivatives
            )
        )

        first = pack["subjects"][0]
        self.assertEqual(first["source_asset_id"], str(source_ids[0]))
        self.assertTrue(uuid.UUID(first["face_crop_asset_id"]))
        self.assertTrue(uuid.UUID(first["upper_body_crop_asset_id"]))
        self.assertEqual(first["source_metrics"]["width"], 800)
        self.assertNotIn("original_url", first)
        self.assertNotIn("face_crop_url", first)
        self.assertNotIn("upper_body_crop_url", first)
        self.assertNotIn("user_images", inspect.signature(service.build_identity_reference_pack).parameters)
        self.assertIn("source_asset_ids", inspect.signature(service.build_identity_reference_pack).parameters)


if __name__ == "__main__":
    unittest.main()
