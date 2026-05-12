"""Generation quality policy tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.services.prompt_brain import build_prompt, get_negative_prompt, get_studio_guardrails  # noqa: E402
from app.services.template_service import get_template_by_id  # noqa: E402
from app.services.wenwen_service import WenwenService  # noqa: E402


class GenerationQualityPolicyTest(unittest.TestCase):
    def test_single_generation_defaults_to_vertical_studio_ratio(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.wenwen_image_size_single, "3:4")
        self.assertEqual(settings.wenwen_image_size_couple, "3:4")

    def test_wenwen_upgrades_legacy_single_four_by_five_to_three_by_four(self) -> None:
        self.assertEqual(WenwenService._build_size(False), "3:4")
        self.assertEqual(WenwenService._build_size(True), "3:4")

    def test_royal_castle_prompt_requires_studio_lighting_and_full_gown(self) -> None:
        template = get_template_by_id("solo_royal_castle")
        self.assertIsNotNone(template)

        prompt = build_prompt(template)  # type: ignore[arg-type]

        self.assertIn("full-length 3:4 vertical", prompt)
        self.assertIn("complete gown and dress train visible", prompt)
        self.assertIn("controlled softbox key light", prompt)
        self.assertIn("face correctly exposed", prompt)
        self.assertIn("controlled sky highlights", prompt)

    def test_couple_guardrails_require_studio_lighting_for_both_subjects(self) -> None:
        guardrails = get_studio_guardrails(is_couple=True)

        self.assertIn("two-person full-length couple portrait", guardrails)
        self.assertIn("Both subjects must receive flattering studio fill light", guardrails)
        self.assertIn("both faces must be correctly exposed", guardrails)
        self.assertIn("do not use harsh outdoor backlight", guardrails)

    def test_negative_prompt_blocks_common_non_studio_failures(self) -> None:
        negative = get_negative_prompt()

        self.assertIn("harsh backlight", negative)
        self.assertIn("face in shadow", negative)
        self.assertIn("blown-out sky", negative)
        self.assertIn("cropped dress", negative)


if __name__ == "__main__":
    unittest.main()
