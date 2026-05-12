"""Order creation service contract tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import HTTPException  # noqa: E402

from app.schemas.order import OrderCreate  # noqa: E402
from app.services import order_creation_service as service  # noqa: E402


class OrderCreationServiceTest(unittest.TestCase):
    def test_identity_image_url_normalization_ignores_case_query_and_hash(self) -> None:
        normalized = service.normalize_identity_image_url("HTTPS://Example.COM/person/?v=1#face")

        self.assertEqual(normalized, "https://example.com/person")

    def test_duplicate_couple_subjects_are_rejected_after_url_normalization(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            service._validate_distinct_couple_subjects(
                [
                    "https://cdn.example.com/person/?v=1",
                    "https://CDN.EXAMPLE.com/person/#again",
                ]
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail["error"], "duplicate_subject_images")

    def test_director_upload_overrides_scene_text_and_preset(self) -> None:
        request = OrderCreate(
            template_id="classic",
            user_images=["https://cdn.example.com/person-a.jpg"],
            legal_accepted=True,
            director_mode=True,
            scene_image_url="https://cdn.example.com/scene.jpg",
            scene_text="a bright garden",
            scene_preset_id="studio",
        )

        decision = service._resolve_director_decision(
            request,
            is_couple_request=False,
            couple_flow=None,
        )

        self.assertEqual(decision.effective_scene_source, "upload")
        self.assertEqual(decision.effective_scene_image_url, "https://cdn.example.com/scene.jpg")
        self.assertEqual(decision.effective_scene_ip_weight, 0.6)
        self.assertIn("scene_text", decision.ignored_inputs)
        self.assertIn("scene_preset_id", decision.ignored_inputs)
        self.assertIn("scene:upload:w=0.60", decision.director_decision_hints)

    def test_director_hints_include_remote_couple_flow_and_sorted_ignored_inputs(self) -> None:
        hints = service.build_director_decision_hints(
            director_mode=True,
            effective_scene_source="preset",
            effective_outfit_source="text",
            ignored_inputs=["scene_text", "clothing_preset_id", "scene_text"],
            effective_scene_preset_title="Editorial Studio",
            effective_outfit_preset_title=None,
            effective_scene_ip_weight=0.5,
            effective_outfit_ip_weight=None,
            is_couple_request=True,
            couple_flow="remote",
        )

        self.assertEqual(
            hints,
            [
                "director_mode_enabled",
                "scene:preset:Editorial Studio:w=0.50",
                "outfit:text",
                "ignored:clothing_preset_id,scene_text",
                "couple:remote",
            ],
        )


if __name__ == "__main__":
    unittest.main()
