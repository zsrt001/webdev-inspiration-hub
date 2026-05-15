"""Evolink provider adapter contract tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import evolink_service as evolink_module  # noqa: E402
from app.services.evolink_service import EvolinkService  # noqa: E402


class EvolinkProviderTest(unittest.TestCase):
    def test_evolink_gemini_models_use_evolink_endpoint_not_wenwen_native(self) -> None:
        original_model = evolink_module.settings.evolink_image_model
        original_fallbacks = evolink_module.settings.evolink_image_fallback_models
        try:
            evolink_module.settings.evolink_image_model = "gemini-3.1-flash-image-preview"
            evolink_module.settings.evolink_image_fallback_models = "gemini-3-pro-image-preview"

            self.assertFalse(EvolinkService._image_edit_uses_native_model("gemini-3.1-flash-image-preview"))
            self.assertEqual(EvolinkService._effective_image_edit_model(), "gemini-3.1-flash-image-preview")
            self.assertEqual(
                EvolinkService._image_edit_model_candidates("gemini-3.1-flash-image-preview"),
                ["gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview"],
            )
        finally:
            evolink_module.settings.evolink_image_model = original_model
            evolink_module.settings.evolink_image_fallback_models = original_fallbacks

    def test_evolink_reference_entries_keep_identity_first_and_limited(self) -> None:
        service = EvolinkService()
        pack = {
            "subjects": [
                {
                    "role": "bride",
                    "identity_label": "person_a",
                    "original_url": "https://example.test/bride.jpg",
                    "face_crop_url": "https://example.test/bride-face.jpg",
                    "upper_body_crop_url": "https://example.test/bride-upper.jpg",
                },
                {
                    "role": "groom",
                    "identity_label": "person_b",
                    "original_url": "https://example.test/groom.jpg",
                    "face_crop_url": "https://example.test/groom-face.jpg",
                    "upper_body_crop_url": "https://example.test/groom-upper.jpg",
                },
            ]
        }

        entries = service._evolink_reference_entries(
            identity_refs=[],
            style_refs=["https://example.test/style.jpg"],
            current_result_refs=["https://example.test/previous.jpg"],
            identity_reference_pack=pack,
            include_previous_result=True,
            is_couple=True,
        )

        self.assertEqual(
            entries,
            [
                ("bride original portrait", "https://example.test/bride.jpg"),
                ("bride face crop", "https://example.test/bride-face.jpg"),
                ("groom original portrait", "https://example.test/groom.jpg"),
                ("groom face crop", "https://example.test/groom-face.jpg"),
                ("previous candidate canvas for composition repair only", "https://example.test/previous.jpg"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
