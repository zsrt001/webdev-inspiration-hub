"""Owner-checked private-byte identity embedding tests."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.services import identity_embedding_service as module
from app.services.identity_embedding_service import FaceEmbedding, IdentityEmbeddingService


def _face(vector: list[float], *, offset: float = 0.0) -> FaceEmbedding:
    return FaceEmbedding(
        embedding=vector,
        bbox=(offset, 0.0, offset + 100.0, 120.0),
        det_score=0.99,
    )


class IdentityEmbeddingServiceTest(unittest.IsolatedAsyncioTestCase):
    async def _verify(
        self,
        *,
        is_couple: bool,
        faces_by_content: dict[bytes, list[FaceEmbedding]],
    ):
        service = IdentityEmbeddingService()
        owner_id = uuid.uuid4()
        source_ids = [uuid.uuid4(), uuid.uuid4()] if is_couple else [uuid.uuid4()]
        generated_id = uuid.uuid4()
        content_by_id = {
            source_id: f"source-{index}".encode("ascii")
            for index, source_id in enumerate(source_ids, start=1)
        }
        content_by_id[generated_id] = b"generated"

        async def fake_load(_db, *, owner_user_id, asset_id):
            self.assertEqual(owner_user_id, owner_id)
            return SimpleNamespace(content=content_by_id[asset_id])

        service._detect_faces_from_bytes = lambda content: faces_by_content[content]  # type: ignore[method-assign]
        load = AsyncMock(side_effect=fake_load)
        with patch.object(module, "load_owned_asset_bytes", load):
            verdict = await service.verify_identity_similarity(
                object(),
                owner_user_id=owner_id,
                generated_asset_id=generated_id,
                source_asset_ids=source_ids,
                is_couple=is_couple,
            )
        return verdict, load

    async def test_single_subject_low_similarity_blocks_delivery(self) -> None:
        verdict, load = await self._verify(
            is_couple=False,
            faces_by_content={
                b"source-1": [_face([1.0, 0.0, 0.0])],
                b"generated": [_face([0.0, 1.0, 0.0])],
            },
        )

        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["reasons"], ["identity_similarity_low"])
        self.assertEqual(verdict["issues"][0]["category"], "identity")
        self.assertEqual(load.await_count, 2)

    async def test_couple_ambiguous_identity_match_blocks_as_averaging(self) -> None:
        verdict, load = await self._verify(
            is_couple=True,
            faces_by_content={
                b"source-1": [_face([1.0, 0.0, 0.0])],
                b"source-2": [_face([0.0, 1.0, 0.0])],
                b"generated": [
                    _face([0.707, 0.707, 0.0]),
                    _face([0.707, 0.707, 0.0], offset=140.0),
                ],
            },
        )

        self.assertFalse(verdict["passed"])
        self.assertIn("identity_margin_low", verdict["reasons"])
        self.assertIn("identity_averaging", verdict["reasons"])
        self.assertEqual(load.await_count, 3)

    async def test_missing_or_wrong_source_count_is_blocking_without_fetching(self) -> None:
        service = IdentityEmbeddingService()
        load = AsyncMock()
        with patch.object(module, "load_owned_asset_bytes", load):
            verdict = await service.verify_identity_similarity(
                object(),
                owner_user_id=uuid.uuid4(),
                generated_asset_id=uuid.uuid4(),
                source_asset_ids=[],
                is_couple=False,
            )
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["reasons"], ["identity_source_count_invalid"])
        load.assert_not_awaited()

    def test_public_url_parameters_are_absent(self) -> None:
        parameters = inspect.signature(IdentityEmbeddingService.verify_identity_similarity).parameters
        self.assertIn("generated_asset_id", parameters)
        self.assertIn("source_asset_ids", parameters)
        self.assertNotIn("image_url", parameters)
        self.assertNotIn("source_image_urls", parameters)
        self.assertFalse(hasattr(IdentityEmbeddingService, "_fetch_image_bytes"))
        self.assertFalse(hasattr(IdentityEmbeddingService, "_faces_for_url"))


if __name__ == "__main__":
    unittest.main()
