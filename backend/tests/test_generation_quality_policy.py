"""Generation quality policy tests."""

import base64
from io import BytesIO
from pathlib import Path
import sys
import unittest

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.services.generation_policy import (  # noqa: E402
    build_generation_negative_prompt,
    build_studio_generation_prompt,
    resolve_generation_aspect_ratio,
    should_retry_qa,
)
from app.services.generation_state_service import merge_qa_failure_state  # noqa: E402
from app.services.prompt_brain import build_prompt, get_negative_prompt, get_studio_guardrails  # noqa: E402
from app.services import qa_service  # noqa: E402
from app.services.qa_service import blocking_vision_reasons  # noqa: E402
from app.services.template_service import get_template_by_id  # noqa: E402
from app.services.wenwen_service import WenwenService  # noqa: E402


class GenerationQualityPolicyTest(unittest.TestCase):
    def test_single_generation_defaults_to_vertical_studio_ratio(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.wenwen_image_size_single, "3:4")
        self.assertEqual(settings.wenwen_image_size_couple, "3:4")
        self.assertTrue(settings.wenwen_require_image_edit_identity)

    def test_wenwen_upgrades_legacy_single_four_by_five_to_three_by_four(self) -> None:
        self.assertEqual(WenwenService._build_size(False), "3:4")
        self.assertEqual(WenwenService._build_size(True), "3:4")

    def test_aspect_ratio_policy_upgrades_single_and_couple_legacy_values(self) -> None:
        self.assertEqual(resolve_generation_aspect_ratio("4:5", is_couple=False), "3:4")
        self.assertEqual(resolve_generation_aspect_ratio("4:5", is_couple=True), "3:4")
        self.assertEqual(resolve_generation_aspect_ratio("3:2", is_couple=False), "3:4")
        self.assertEqual(resolve_generation_aspect_ratio("3:2", is_couple=True), "3:4")

    def test_royal_castle_prompt_requires_studio_lighting_and_full_gown(self) -> None:
        template = get_template_by_id("solo_royal_castle")
        self.assertIsNotNone(template)

        prompt = build_prompt(template)  # type: ignore[arg-type]

        self.assertIn("full-length 3:4 vertical", prompt)
        self.assertIn("complete gown and dress train visible", prompt)
        self.assertIn("controlled softbox key light", prompt)
        self.assertIn("face correctly exposed", prompt)
        self.assertIn("Identity lock is mandatory", prompt)
        self.assertIn("preserve the same face shape", prompt)
        self.assertIn("castle-inspired indoor bridal studio set", prompt)
        self.assertIn("no mountain vista", prompt)
        self.assertNotIn("painted mountain backdrop", prompt)

    def test_couple_guardrails_require_studio_lighting_for_both_subjects(self) -> None:
        guardrails = get_studio_guardrails(is_couple=True)

        self.assertIn("two-person full-length couple portrait", guardrails)
        self.assertIn("Both subjects must receive flattering studio fill light", guardrails)
        self.assertIn("both faces must be correctly exposed", guardrails)
        self.assertIn("both identities must remain recognizable", guardrails)
        self.assertIn("do not use harsh outdoor backlight", guardrails)

    def test_negative_prompt_blocks_common_non_studio_failures(self) -> None:
        negative = get_negative_prompt()

        self.assertIn("harsh backlight", negative)
        self.assertIn("generic model face", negative)
        self.assertIn("changed face shape", negative)
        self.assertIn("face in shadow", negative)
        self.assertIn("blown-out sky", negative)
        self.assertIn("cropped dress", negative)
        self.assertIn("unrequested mountain vista", negative)

    def test_legacy_prompt_override_cannot_bypass_studio_guardrails(self) -> None:
        template = get_template_by_id("solo_royal_castle")
        self.assertIsNotNone(template)

        prompt = build_studio_generation_prompt(
            template=template,
            prompt_override="outdoor cinematic harsh backlight",
            global_style_text=None,
            scene_text=None,
            outfit_text=None,
            is_couple=False,
        )

        self.assertIn("outdoor cinematic harsh backlight", prompt)
        self.assertIn("do not use harsh outdoor backlight", prompt)
        self.assertIn("controlled softbox key light", prompt)
        self.assertIn("full-length 3:4 vertical", prompt)
        self.assertIn("Identity lock is mandatory", prompt)

    def test_couple_generation_policy_adds_dedicated_negative_terms(self) -> None:
        negative = build_generation_negative_prompt(is_couple=True)

        self.assertIn("fused faces", negative)
        self.assertIn("shared torso", negative)
        self.assertIn("swapped identity", negative)
        self.assertIn("generic bride face", negative)

    def test_qa_retry_policy_allows_one_retry_for_fixable_artifacts(self) -> None:
        self.assertTrue(should_retry_qa(["bad_hands"], 1, max_attempts=2))
        self.assertTrue(should_retry_qa(["identity_mismatch"], 1, max_attempts=2))
        self.assertTrue(should_retry_qa(["poor_studio_quality"], 1, max_attempts=2))
        self.assertFalse(should_retry_qa(["bad_hands"], 2, max_attempts=2))
        self.assertFalse(should_retry_qa(["low_resolution"], 1, max_attempts=2))

    def test_qa_failure_state_uses_shared_audit_shape(self) -> None:
        params = merge_qa_failure_state(
            {"debug": {"qa_history": [{"attempt": 1}]}},
            attempt=2,
            reasons=["bad_hands"],
            candidate_url="https://example.com/candidate.png",
            engine="wenwen",
            extra_params={"couple_guardrails": {"is_couple": True}},
        )

        self.assertEqual(params["qa_last_reasons"], ["bad_hands"])
        self.assertEqual(params["qa_attempt_count"], 2)
        self.assertEqual(params["couple_guardrails"], {"is_couple": True})
        self.assertEqual(params["debug"]["qa_history"][-1]["engine"], "wenwen")
        self.assertEqual(params["debug"]["qa_history"][-1]["candidate_url"], "https://example.com/candidate.png")

    def test_wenwen_inline_reference_preserves_aspect_with_high_quality_transport(self) -> None:
        image = Image.effect_noise((2200, 1600), 64).convert("RGB")
        source = BytesIO()
        image.save(source, format="PNG")

        prepared, content_type = WenwenService._prepare_inline_image_reference(source.getvalue(), "image/png")

        self.assertEqual(content_type, "image/jpeg")
        self.assertLess(len(prepared), len(source.getvalue()))
        with Image.open(BytesIO(prepared)) as prepared_image:
            self.assertLessEqual(max(prepared_image.size), WenwenService.INLINE_REFERENCE_MAX_EDGE)
            self.assertAlmostEqual(prepared_image.size[0] / prepared_image.size[1], 2200 / 1600, places=2)

    def test_wenwen_native_generation_has_fallback_model(self) -> None:
        candidates = WenwenService._native_model_candidates()

        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(candidates[0], "gemini-3-pro-image-preview")
        self.assertIn("gemini-3.1-flash-image-preview", candidates)

    def test_wenwen_image_edit_binary_outputs_support_b64_json(self) -> None:
        encoded = "iVBORw0KGgo="
        outputs = WenwenService._extract_image_edit_binary_outputs({"data": [{"b64_json": encoded}]})

        self.assertEqual(outputs, [(b"\x89PNG\r\n\x1a\n", "image/png")])

    def test_generic_vision_other_does_not_block_delivery(self) -> None:
        self.assertEqual(blocking_vision_reasons(["other"]), [])
        self.assertEqual(blocking_vision_reasons(["bad_hands", "other"]), ["bad_hands"])
        self.assertEqual(blocking_vision_reasons(["vision_error"]), ["vision_error"])
        self.assertEqual(blocking_vision_reasons(["poor_studio_quality", "other"]), ["poor_studio_quality"])



class WenwenGenerationPayloadPolicyTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _data_url(width: int = 720, height: int = 960) -> str:
        image = Image.new("RGB", (width, height), (220, 210, 200))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    async def test_remote_couple_payload_uses_shared_generation_policy(self) -> None:
        template = get_template_by_id("solo_royal_castle")
        self.assertIsNotNone(template)

        payload, prompt, negative = await WenwenService()._build_payload(
            template=template,
            user_images=[],
            subject_count=2,
            prompt_override="outdoor cinematic backlight",
            global_style_text=None,
            scene_text=None,
            outfit_text=None,
            scene_image_url=None,
            clothing_image_url=None,
            couple_flow="remote",
            prompt_enrichment=False,
        )

        self.assertEqual(payload["size"], "3:4")
        self.assertEqual(payload["couple_flow"], "remote")
        self.assertIn("do not use harsh outdoor backlight", prompt)
        self.assertIn("Balanced couple blocking", prompt)
        self.assertIn("bride identity anchored to reference image 1", prompt)
        self.assertIn("fused faces", negative)
        self.assertIn("swapped identity", negative)

    async def test_native_payload_labels_identity_references(self) -> None:
        template = get_template_by_id("royal_castle")
        self.assertIsNotNone(template)

        payload, _prompt, _negative = await WenwenService()._build_native_payload(
            template=template,
            user_images=[
                "data:image/png;base64,iVBORw0KGgo=",
                "data:image/png;base64,iVBORw0KGgo=",
            ],
            subject_count=2,
            prompt_override=None,
            global_style_text=None,
            scene_text=None,
            outfit_text=None,
            scene_image_url=None,
            clothing_image_url=None,
            couple_flow="remote",
            prompt_enrichment=False,
        )

        text_parts = [
            part.get("text", "")
            for part in payload["contents"][0]["parts"]
            if isinstance(part, dict) and "text" in part
        ]
        joined = "\n".join(text_parts)
        self.assertIn("Reference identity order", joined)
        self.assertIn("Identity reference image 1", joined)
        self.assertIn("Identity reference image 2", joined)

    async def test_image_edit_files_add_identity_closeup_refs_before_style_refs(self) -> None:
        files = await WenwenService()._build_image_edit_reference_files(
            [
                self._data_url(),
                self._data_url(),
                self._data_url(),
            ]
        )

        names = [item[1][0] for item in files]
        self.assertEqual(len(files), 4)
        self.assertTrue(names[0].startswith("identity_full_1."))
        self.assertEqual(names[1], "identity_closeup_1.jpg")
        self.assertTrue(names[2].startswith("identity_full_2."))
        self.assertEqual(names[3], "identity_closeup_2.jpg")

    async def test_single_image_edit_does_not_treat_style_ref_as_second_identity(self) -> None:
        files = await WenwenService()._build_image_edit_reference_files(
            [self._data_url()],
            style_refs=[self._data_url()],
        )

        names = [item[1][0] for item in files]
        self.assertEqual(len(files), 3)
        self.assertTrue(names[0].startswith("identity_full_1."))
        self.assertEqual(names[1], "identity_closeup_1.jpg")
        self.assertTrue(names[2].startswith("style_reference_1."))
        self.assertFalse(any(name.startswith("identity_full_2.") for name in names))

    async def test_identity_qa_fails_closed_when_vision_is_unavailable(self) -> None:
        original = qa_service.llm_service.is_vision_provider_configured
        original_required = qa_service.settings.qa_require_identity_vision
        qa_service.llm_service.is_vision_provider_configured = lambda: False
        qa_service.settings.qa_require_identity_vision = True
        try:
            passed, reasons = await qa_service.verify_with_vision(
                "https://cdn.example.com/generated.jpg",
                source_image_urls=["https://cdn.example.com/source.jpg"],
            )
        finally:
            qa_service.llm_service.is_vision_provider_configured = original
            qa_service.settings.qa_require_identity_vision = original_required

        self.assertFalse(passed)
        self.assertEqual(reasons, ["vision_not_configured"])


if __name__ == "__main__":
    unittest.main()
