"""Identity embedding hard gate tests."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import qa_service  # noqa: E402
from app.services.identity_embedding_service import FaceEmbedding, IdentityEmbeddingService  # noqa: E402


def _face(vector: list[float], *, offset: float = 0.0) -> FaceEmbedding:
    return FaceEmbedding(
        embedding=vector,
        bbox=(offset, 0.0, offset + 100.0, 120.0),
        det_score=0.99,
    )


class IdentityEmbeddingServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_single_subject_low_similarity_blocks_delivery(self) -> None:
        service = IdentityEmbeddingService()

        async def fake_faces_for_url(url: str):
            if "source" in url:
                return [_face([1.0, 0.0, 0.0])]
            return [_face([0.0, 1.0, 0.0])]

        service._faces_for_url = fake_faces_for_url  # type: ignore[method-assign]

        verdict = await service.verify_identity_similarity(
            "https://cdn.example.com/generated.jpg",
            source_image_urls=["https://cdn.example.com/source.jpg"],
        )

        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["reasons"], ["identity_similarity_low"])
        self.assertEqual(verdict["issues"][0]["category"], "identity")
        self.assertTrue(verdict["issues"][0]["blocking"])

    async def test_couple_ambiguous_identity_match_blocks_as_averaging(self) -> None:
        service = IdentityEmbeddingService()

        async def fake_faces_for_url(url: str):
            if "source-a" in url:
                return [_face([1.0, 0.0, 0.0])]
            if "source-b" in url:
                return [_face([0.0, 1.0, 0.0])]
            return [
                _face([0.707, 0.707, 0.0]),
                _face([0.707, 0.707, 0.0], offset=140.0),
            ]

        service._faces_for_url = fake_faces_for_url  # type: ignore[method-assign]

        verdict = await service.verify_identity_similarity(
            "https://cdn.example.com/generated.jpg",
            source_image_urls=["https://cdn.example.com/source-a.jpg", "https://cdn.example.com/source-b.jpg"],
            is_couple=True,
        )

        self.assertFalse(verdict["passed"])
        self.assertIn("identity_margin_low", verdict["reasons"])
        self.assertIn("identity_averaging", verdict["reasons"])

    async def test_output_verdict_fails_before_vision_when_embedding_gate_fails(self) -> None:
        original_basic = qa_service.basic_image_verdict
        original_embedding = qa_service.identity_embedding_service.verify_identity_similarity
        original_vision = qa_service.verify_with_vision_verdict
        original_required = qa_service.settings.qa_require_identity_embedding
        vision_called = False

        async def fake_basic(_url: str) -> dict:
            return {
                "passed": True,
                "reasons": [],
                "issues": [],
                "metrics": {},
                "source": "local",
                "notes": "",
            }

        async def fake_embedding(*args, **kwargs) -> dict:
            return {
                "passed": False,
                "reasons": ["identity_similarity_low"],
                "issues": [
                    {
                        "code": "identity_similarity_low",
                        "category": "identity",
                        "target": "face_embedding_similarity",
                        "severity": "critical",
                        "blocking": True,
                    }
                ],
                "metrics": {"identity_similarity": 0.2},
                "source": "identity_embedding",
                "notes": "single_similarity=0.200",
            }

        async def fake_vision(*args, **kwargs) -> dict:
            nonlocal vision_called
            vision_called = True
            return {"passed": True, "reasons": [], "issues": [], "notes": "", "source": "vision"}

        qa_service.basic_image_verdict = fake_basic
        qa_service.identity_embedding_service.verify_identity_similarity = fake_embedding
        qa_service.verify_with_vision_verdict = fake_vision
        qa_service.settings.qa_require_identity_embedding = True
        try:
            verdict = await qa_service.output_verdict(
                "https://cdn.example.com/generated.jpg",
                source_image_urls=["https://cdn.example.com/source.jpg"],
            )
        finally:
            qa_service.basic_image_verdict = original_basic
            qa_service.identity_embedding_service.verify_identity_similarity = original_embedding
            qa_service.verify_with_vision_verdict = original_vision
            qa_service.settings.qa_require_identity_embedding = original_required

        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["reasons"], ["identity_similarity_low"])
        self.assertEqual(verdict["identity_grade"], "major_mismatch")
        self.assertFalse(vision_called)


if __name__ == "__main__":
    unittest.main()
