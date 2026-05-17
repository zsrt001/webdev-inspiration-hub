"""Evolink provider adapter contract tests."""

from pathlib import Path
import asyncio
import sys
import unittest
import uuid
from inspect import signature


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import evolink_service as evolink_module  # noqa: E402
from app.services import provider_workflow as workflow_module  # noqa: E402
from app.services.evolink_service import EvolinkService  # noqa: E402
from app.services.provider_workflow import GenerationProviderWorkflow  # noqa: E402
from app.services.provider_workflow import build_generation_negative_prompt, build_studio_generation_prompt  # noqa: E402
from app.services.template_service import get_template_by_id  # noqa: E402
from app.services.trial_access_service import prepare_delivered_image_urls  # noqa: E402
from app.services.wenwen_service import WenwenService  # noqa: E402


class EvolinkProviderTest(unittest.TestCase):
    def test_evolink_shares_workflow_without_inheriting_wenwen_provider(self) -> None:
        self.assertTrue(issubclass(EvolinkService, GenerationProviderWorkflow))
        self.assertTrue(issubclass(WenwenService, GenerationProviderWorkflow))
        self.assertFalse(issubclass(EvolinkService, WenwenService))

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
                ["gemini-3.1-flash-image-preview"],
            )
        finally:
            evolink_module.settings.evolink_image_model = original_model
            evolink_module.settings.evolink_image_fallback_models = original_fallbacks

    def test_evolink_runtime_rejects_fallback_model_configuration(self) -> None:
        original_model = evolink_module.settings.evolink_image_model
        original_fallbacks = evolink_module.settings.evolink_image_fallback_models
        original_api_key = evolink_module.settings.evolink_api_key
        try:
            evolink_module.settings.evolink_api_key = "test-key"
            evolink_module.settings.evolink_image_model = "gemini-3-pro-image-preview"
            evolink_module.settings.evolink_image_fallback_models = "gemini-3.1-flash-image-preview"

            with self.assertRaisesRegex(ValueError, "EVOLINK_IMAGE_FALLBACK_MODELS must be empty"):
                EvolinkService().validate_runtime_requirements(force=True)
        finally:
            evolink_module.settings.evolink_image_model = original_model
            evolink_module.settings.evolink_image_fallback_models = original_fallbacks
            evolink_module.settings.evolink_api_key = original_api_key

    def test_evolink_bad_request_is_classified_as_provider_rejection(self) -> None:
        failure_code = EvolinkService()._classify_error(RuntimeError("evolink_request_rejected:400:{\"error\":\"bad request\"}"))

        self.assertEqual(failure_code, "provider_request_rejected")

    def test_delivery_helper_accepts_template_id_for_provider_completion(self) -> None:
        self.assertIn("template_id", signature(prepare_delivered_image_urls).parameters)

    def test_evolink_compacts_long_prompts_under_provider_limit(self) -> None:
        prompt = (
            "IDENTITY LOCK: preserve face shape, eyes, nose, mouth, jawline, and skin undertone. "
            "STUDIO QUALITY: controlled softbox lighting, realistic skin texture, complete wardrobe and crop. "
            "Negative prompt: generic face, plastic skin, bad hands, harsh backlight. "
        ) * 140

        compacted = EvolinkService._compact_prompt_for_evolink(prompt)

        self.assertLessEqual(len(compacted), EvolinkService.PROMPT_CHAR_LIMIT)
        self.assertIn("Identity-preserving image edit", compacted)
        self.assertIn("Negative", compacted)
        self.assertIn("lighting", compacted.lower())
        self.assertIn("composition", compacted.lower())

    def test_evolink_real_wedding_round_prompt_stays_within_provider_limit(self) -> None:
        service = EvolinkService()
        template = get_template_by_id("royal_castle")
        self.assertIsNotNone(template)
        base_prompt = build_studio_generation_prompt(
            template=template,
            prompt_override=None,
            global_style_text="high-end wedding magazine portrait, both identities preserved",
            scene_text="warm European indoor bridal studio, controlled soft window light",
            outfit_text="ivory couture wedding dress and tailored cream suit",
            is_couple=True,
        )
        round_prompt = service._build_image_edit_round_prompt(
            base_prompt=base_prompt,
            negative_prompt=build_generation_negative_prompt(is_couple=True),
            round_number=1,
            qa_reasons=[],
            qa_issues=[],
            identity_pack_note=(
                "Identity reference pack role order: person_a=bride, person_b=groom. "
                "Preserve each role separately."
            ),
            include_previous_result=False,
            is_couple=True,
        )

        compacted = service._compact_prompt_for_evolink(round_prompt)

        self.assertLessEqual(len(compacted), EvolinkService.PROMPT_CHAR_LIMIT)
        self.assertIn("image_urls", compacted)
        self.assertIn("Couple rule", compacted)
        self.assertIn("Negative", compacted)

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
                ("bride face reference - IDENTITY ANCHOR", "https://example.test/bride-face.jpg"),
                ("groom face reference - IDENTITY ANCHOR", "https://example.test/groom-face.jpg"),
                ("previous result canvas - composition reference only", "https://example.test/previous.jpg"),
                ("style reference image 1", "https://example.test/style.jpg"),
            ],
        )

    def test_evolink_reference_entries_keep_four_identity_refs_for_couple_primary_round(self) -> None:
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
            current_result_refs=[],
            identity_reference_pack=pack,
            include_previous_result=False,
            is_couple=True,
        )

        self.assertEqual(
            entries,
            [
                ("bride face reference - IDENTITY ANCHOR", "https://example.test/bride-face.jpg"),
                ("bride full reference", "https://example.test/bride.jpg"),
                ("groom face reference - IDENTITY ANCHOR", "https://example.test/groom-face.jpg"),
                ("groom full reference", "https://example.test/groom.jpg"),
            ],
        )

    def test_vision_error_retry_exhaustion_delivers_candidate_when_non_blocking(self) -> None:
        service = EvolinkService()
        order_id = uuid.uuid4()
        candidate_url = "https://example.test/generated.png"
        calls: list[tuple[str, object]] = []
        original_output_verdict = workflow_module.output_verdict
        original_fail_on_error = workflow_module.settings.qa_fail_on_vision_error
        original_retry_attempts = workflow_module.settings.qa_vision_error_retry_attempts

        async def fake_output_verdict(*args, **kwargs) -> dict:
            return {
                "passed": False,
                "reasons": ["vision_error"],
                "issues": [{"code": "vision_error", "blocking": False}],
            }

        async def fake_update_state(*args, **kwargs) -> None:
            calls.append(("update", kwargs["attempt"]))

        async def fake_record_failure(*args, **kwargs) -> None:
            calls.append(("record_failure", kwargs["reasons"]))

        async def fake_mark_retry(*args, **kwargs) -> None:
            calls.append(("retry", kwargs["attempt"]))

        async def fake_fail_order(*args, **kwargs) -> None:
            calls.append(("fail", args))

        async def fake_complete_order(*args, **kwargs) -> None:
            calls.append(("complete", kwargs["selection_summary"]))

        async def fake_completion_email(*args, **kwargs) -> None:
            calls.append(("email", args))

        try:
            workflow_module.output_verdict = fake_output_verdict  # type: ignore[assignment]
            workflow_module.settings.qa_fail_on_vision_error = False
            workflow_module.settings.qa_vision_error_retry_attempts = 3
            service._update_image_edit_round_qa_state = fake_update_state  # type: ignore[method-assign]
            service._record_qa_failure = fake_record_failure  # type: ignore[method-assign]
            service._mark_qa_retry_pending = fake_mark_retry  # type: ignore[method-assign]
            service._fail_order = fake_fail_order  # type: ignore[method-assign]
            service._complete_order = fake_complete_order  # type: ignore[method-assign]
            service._queue_completion_email = fake_completion_email  # type: ignore[method-assign]

            handled = asyncio.run(
                service._retry_pending_vision_recheck(
                    order_id,
                    params={
                        "qa_attempt_count": 2,
                        "qa_retry_kind": "vision_recheck",
                        "qa_retry_candidate_url": candidate_url,
                        "debug": {
                            "image_edit_rounds": [
                                {
                                    "round": 1,
                                    "stage": "primary_generation",
                                    "candidate_url": candidate_url,
                                    "provider_urls": [candidate_url],
                                    "candidate_scores": [{"index": 0, "score": 72.0}],
                                }
                            ]
                        },
                    },
                    user_images=["https://example.test/source.jpg"],
                    subject_count=1,
                    couple_flow=None,
                )
            )
        finally:
            workflow_module.output_verdict = original_output_verdict  # type: ignore[assignment]
            workflow_module.settings.qa_fail_on_vision_error = original_fail_on_error
            workflow_module.settings.qa_vision_error_retry_attempts = original_retry_attempts

        self.assertTrue(handled)
        self.assertIn(("update", 3), calls)
        self.assertTrue(any(call[0] == "record_failure" for call in calls))
        self.assertFalse(any(call[0] == "retry" for call in calls))
        self.assertFalse(any(call[0] == "fail" for call in calls))
        complete_calls = [call for call in calls if call[0] == "complete"]
        self.assertEqual(len(complete_calls), 1)
        self.assertEqual(complete_calls[0][1]["qa_degraded_reason"], "vision_error_retry_exhausted")
        self.assertTrue(complete_calls[0][1]["requires_admin_review"])

    def test_exhausted_pending_vision_error_does_not_call_vision_again(self) -> None:
        service = EvolinkService()
        order_id = uuid.uuid4()
        candidate_url = "https://example.test/generated.png"
        calls: list[tuple[str, object]] = []
        original_output_verdict = workflow_module.output_verdict
        original_fail_on_error = workflow_module.settings.qa_fail_on_vision_error
        original_retry_attempts = workflow_module.settings.qa_vision_error_retry_attempts

        async def fake_output_verdict(*args, **kwargs) -> dict:
            calls.append(("vision_called", args))
            return {"passed": False, "reasons": ["vision_error"], "issues": []}

        async def fake_complete_order(*args, **kwargs) -> None:
            calls.append(("complete", kwargs["qa_attempt_count"]))

        async def fake_completion_email(*args, **kwargs) -> None:
            calls.append(("email", args))

        try:
            workflow_module.output_verdict = fake_output_verdict  # type: ignore[assignment]
            workflow_module.settings.qa_fail_on_vision_error = False
            workflow_module.settings.qa_vision_error_retry_attempts = 3
            service._complete_order = fake_complete_order  # type: ignore[method-assign]
            service._queue_completion_email = fake_completion_email  # type: ignore[method-assign]

            handled = asyncio.run(
                service._retry_pending_vision_recheck(
                    order_id,
                    params={
                        "qa_attempt_count": 3,
                        "qa_last_reasons": ["vision_error"],
                        "qa_retry_kind": "vision_recheck",
                        "qa_retry_candidate_url": candidate_url,
                        "debug": {
                            "image_edit_rounds": [
                                {
                                    "round": 1,
                                    "stage": "primary_generation",
                                    "candidate_url": candidate_url,
                                    "provider_urls": [candidate_url],
                                    "candidate_scores": [{"index": 0, "score": 72.0}],
                                }
                            ]
                        },
                    },
                    user_images=["https://example.test/source.jpg"],
                    subject_count=1,
                    couple_flow=None,
                )
            )
        finally:
            workflow_module.output_verdict = original_output_verdict  # type: ignore[assignment]
            workflow_module.settings.qa_fail_on_vision_error = original_fail_on_error
            workflow_module.settings.qa_vision_error_retry_attempts = original_retry_attempts

        self.assertTrue(handled)
        self.assertNotIn("vision_called", [call[0] for call in calls])
        self.assertIn(("complete", 3), calls)
        self.assertTrue(any(call[0] == "email" for call in calls))


if __name__ == "__main__":
    unittest.main()
