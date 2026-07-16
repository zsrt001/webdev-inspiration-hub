"""Photometric QA hard gate tests."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

from PIL import Image, ImageDraw


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import qa_service  # noqa: E402
from app.services.photometric_qa_service import PhotometricQAService  # noqa: E402
from app.services import repair_policy  # noqa: E402


class PhotometricQAServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_dark_face_against_bright_background_fails_lighting_gate(self) -> None:
        image = Image.new("RGB", (400, 600), (236, 236, 236))
        draw = ImageDraw.Draw(image)
        draw.rectangle((125, 80, 275, 250), fill=(64, 55, 50))

        reasons, metrics = PhotometricQAService().evaluate_image(image)

        self.assertIn("face_underexposed", reasons)
        self.assertIn("background_brighter_than_face", reasons)
        self.assertLess(metrics["face_luma"], 86)
        self.assertGreater(metrics["background_face_luma_delta"], 20)

    def test_glossy_skin_and_clipped_white_dress_fail_lighting_gate(self) -> None:
        image = Image.new("RGB", (400, 600), (120, 120, 120))
        draw = ImageDraw.Draw(image)
        draw.rectangle((124, 80, 276, 245), fill=(246, 198, 165))
        draw.rectangle((95, 270, 305, 570), fill=(255, 255, 255))

        reasons, metrics = PhotometricQAService().evaluate_image(image)

        self.assertIn("oily_skin_highlight", reasons)
        self.assertIn("dress_highlights_blown", reasons)
        self.assertGreater(metrics["skin_highlight_ratio"], 0.065)
        self.assertGreater(metrics["dress_clip_ratio"], 0.16)

    async def test_output_verdict_fails_before_vision_when_photometric_gate_fails(self) -> None:
        original_basic = qa_service.basic_image_verdict
        original_embedding_required = qa_service.settings.qa_require_identity_embedding
        original_photometric_required = qa_service.settings.qa_require_photometric
        original_photometric = qa_service.photometric_qa_service.verify_lighting
        original_vision = qa_service.verify_with_vision_verdict
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

        async def fake_photometric(*args, **kwargs) -> dict:
            return {
                "passed": False,
                "reasons": ["face_underexposed", "background_brighter_than_face"],
                "issues": [
                    {
                        "code": "face_underexposed",
                        "category": "photography_quality",
                        "target": "face_exposure",
                        "severity": "major",
                        "blocking": True,
                    }
                ],
                "metrics": {"face_luma": 55.0, "background_face_luma_delta": 80.0},
                "source": "photometric",
                "notes": "photometric_threshold_failed",
            }

        async def fake_vision(*args, **kwargs) -> dict:
            nonlocal vision_called
            vision_called = True
            return {"passed": True, "reasons": [], "issues": [], "notes": "", "source": "vision"}

        qa_service.basic_image_verdict = fake_basic
        qa_service.settings.qa_require_identity_embedding = False
        qa_service.settings.qa_require_photometric = True
        qa_service.photometric_qa_service.verify_lighting = fake_photometric
        qa_service.verify_with_vision_verdict = fake_vision
        try:
            verdict = await qa_service.output_verdict("https://cdn.example.com/generated.jpg")
        finally:
            qa_service.basic_image_verdict = original_basic
            qa_service.settings.qa_require_identity_embedding = original_embedding_required
            qa_service.settings.qa_require_photometric = original_photometric_required
            qa_service.photometric_qa_service.verify_lighting = original_photometric
            qa_service.verify_with_vision_verdict = original_vision

        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["reasons"], ["face_underexposed", "background_brighter_than_face"])
        self.assertEqual(verdict["notes"], "photometric_qa_failed")
        self.assertFalse(vision_called)

    def test_photometric_lighting_reasons_enter_relight_only_mode(self) -> None:
        reasons = ["face_underexposed", "background_brighter_than_face", "oily_skin_highlight"]

        self.assertEqual(
            repair_policy.image_edit_repair_mode(round_number=2, qa_reasons=reasons),
            "relight_edit_only",
        )
        self.assertTrue(repair_policy.should_include_previous_edit_result(reasons))


if __name__ == "__main__":
    unittest.main()
