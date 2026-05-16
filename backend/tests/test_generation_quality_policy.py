"""Generation quality policy tests."""

import asyncio
import base64
from io import BytesIO
from pathlib import Path
import sys
import unittest
import uuid
from urllib.parse import parse_qs, urlparse

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.services.generation_policy import (  # noqa: E402
    build_generation_negative_prompt,
    build_studio_generation_prompt,
    commercial_wedding_standard,
    resolve_generation_aspect_ratio,
    should_retry_qa,
)
from app.services.generation_state_service import merge_qa_failure_state  # noqa: E402
from app.services.generation_credit_policy import (  # noqa: E402
    billable_generation_credits,
    build_generation_credit_policy,
    merge_generation_refund_state,
    resolve_generation_refund_amount,
)
from app.services.prompt_brain import build_prompt, get_negative_prompt, get_studio_guardrails  # noqa: E402
from app.services import qa_service  # noqa: E402
from app.services import llm_service  # noqa: E402
from app.services import wenwen_service as wenwen_module  # noqa: E402
from app.services.qa_service import blocking_vision_reasons  # noqa: E402
from app.services.qa_rules import build_structured_qa_issues, normalize_qa_reason  # noqa: E402
from app.services.template_service import get_template_by_id  # noqa: E402
from app.services.wenwen_service import WenwenService  # noqa: E402


class GenerationQualityPolicyTest(unittest.TestCase):
    def test_single_generation_defaults_to_vertical_studio_ratio(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.wenwen_image_size_single, "3:4")
        self.assertEqual(settings.wenwen_image_size_couple, "3:4")
        self.assertTrue(settings.wenwen_require_image_edit_identity)
        self.assertEqual(settings.wenwen_image_edit_model, "gemini-3-pro-image-preview")
        self.assertEqual(settings.wenwen_image_edit_candidate_count, 2)
        self.assertEqual(settings.wenwen_native_image_size, "4K")

    def test_wenwen_api_keys_have_separate_responsibilities(self) -> None:
        settings = Settings(
            _env_file=None,
            wenwen_api_key="image-key",
            wenwen_chat_api_key="chat-key",
            wenwen_vision_api_key="vision-key",
        )

        self.assertEqual(settings.wenwen_api_key, "image-key")
        self.assertEqual(settings.wenwen_text_api_key_effective, "chat-key")
        self.assertEqual(settings.wenwen_vision_api_key_effective, "vision-key")

        missing_dedicated = Settings(_env_file=None, wenwen_api_key="image-key")
        self.assertEqual(missing_dedicated.wenwen_text_api_key_effective, "")
        self.assertEqual(missing_dedicated.wenwen_vision_api_key_effective, "")

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

        # Skin & photorealism (new v2 high-priority sections)
        self.assertIn("SKIN REALISM:", prompt)
        self.assertIn("natural human skin with visible pores", prompt)
        self.assertIn("PHOTO REALISM:", prompt)
        self.assertIn("Hasselblad", prompt)
        self.assertIn("ANTI AI ARTIFACTS:", prompt)
        self.assertIn("wedding photograph", prompt)
        # Composition
        self.assertIn("full-length 3:4 vertical", prompt)
        self.assertIn("complete gown and dress train visible", prompt)
        self.assertIn("controlled softbox key light", prompt)
        self.assertIn("face correctly exposed", prompt)
        self.assertIn("semi-matte skin", prompt)
        self.assertIn("no oily shine", prompt)
        # Smart lighting: royal castle scene (castle-inspired indoor) = INDOOR LIGHTING
        self.assertIn("INDOOR LIGHTING:", prompt)
        self.assertIn("45 degrees", prompt)
        self.assertIn("0.3 to 0.8 stops darker than the face", prompt)
        # No longer includes all lighting protocols
        self.assertNotIn("OUTDOOR LIGHTING:", prompt)
        self.assertNotIn("WINDOW AND ARCHITECTURAL LIGHTING:", prompt)
        self.assertNotIn("NIGHT AND LOW-LIGHTING:", prompt)
        # Standard sections
        self.assertIn("CANVAS PROPORTION:", prompt)
        self.assertIn("72-86% of the canvas height", prompt)
        self.assertIn("face should remain large enough", prompt)
        self.assertIn("HAND AND ANATOMY SAFETY:", prompt)
        self.assertIn("fingers mostly covered", prompt)
        self.assertIn("IDENTITY LOCK:", prompt)
        self.assertIn("STUDIO QUALITY:", prompt)
        self.assertIn("FORBIDDEN CONSTRAINTS:", prompt)
        self.assertIn("Identity lock is mandatory", prompt)
        self.assertIn("preserve the same face shape", prompt)
        self.assertIn("castle-inspired indoor bridal studio set", prompt)
        self.assertIn("no mountain vista", prompt)
        self.assertNotIn("painted mountain backdrop", prompt)

    def test_couple_guardrails_require_studio_lighting_for_both_subjects(self) -> None:
        guardrails = get_studio_guardrails(is_couple=True)

        # Skin & photorealism (v2 additions)
        self.assertIn("SKIN REALISM:", guardrails)
        self.assertIn("ANTI AI ARTIFACTS:", guardrails)
        # Couple-specific quality
        self.assertIn("two-person full-length couple portrait", guardrails)
        self.assertIn("Both subjects must receive flattering studio fill light", guardrails)
        self.assertIn("both faces must be correctly exposed", guardrails)
        self.assertIn("COUPLE CANVAS PROPORTION:", guardrails)
        self.assertIn("68-84% of the canvas height", guardrails)
        self.assertIn("52-78% of the canvas width", guardrails)
        # Lighting negative guardrails (still present via LIGHTING NEGATIVE GUARDRAILS)
        self.assertIn("do not use harsh outdoor backlight", guardrails.lower())
        self.assertIn("frontal softbox-style fill", guardrails)
        # Standard sections
        self.assertIn("STUDIO QUALITY:", guardrails)
        self.assertIn("DELIVERY GATE:", guardrails)
        self.assertIn("CANDIDATE SELECTION:", guardrails)

    def test_negative_prompt_blocks_common_non_studio_failures(self) -> None:
        negative = get_negative_prompt()

        self.assertIn("harsh backlight", negative)
        self.assertIn("generic model face", negative)
        self.assertIn("changed face shape", negative)
        self.assertIn("face in shadow", negative)
        self.assertIn("underexposed face", negative)
        self.assertIn("background brighter than face", negative)
        self.assertIn("blown-out sky", negative)
        self.assertIn("blown-out window", negative)
        self.assertIn("direct on-camera flash", negative)
        self.assertIn("phone-flash lighting", negative)
        self.assertIn("mixed color temperature", negative)
        self.assertIn("cropped dress", negative)
        self.assertIn("subject too small", negative)
        self.assertIn("background dominates the subject", negative)
        self.assertIn("weak couple interaction", negative)
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

        # User direction preserved
        self.assertIn("outdoor cinematic harsh backlight", prompt)
        self.assertIn("USER DIRECTION:", prompt)
        # Forbidden constraints block harmful directives (via NEGATIVE_PROMPT)
        self.assertIn("FORBIDDEN CONSTRAINTS:", prompt)
        self.assertIn("harsh backlight", prompt.lower())
        # Studio quality section still present
        self.assertIn("controlled softbox key light", prompt)
        self.assertIn("45 degrees", prompt)  # indoor lighting
        # Skin realism v2
        self.assertIn("SKIN REALISM:", prompt)
        self.assertIn("INDOOR LIGHTING:", prompt)  # castle is indoor
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
        self.assertTrue(should_retry_qa(["face_underexposed"], 1, max_attempts=2))
        self.assertTrue(should_retry_qa(["dress_highlights_blown"], 1, max_attempts=2))
        self.assertTrue(should_retry_qa(["background_brighter_than_face"], 1, max_attempts=2))
        self.assertTrue(should_retry_qa(["subject_too_small"], 1, max_attempts=2))
        self.assertTrue(should_retry_qa(["dress_cropped"], 1, max_attempts=2))
        self.assertFalse(should_retry_qa(["bad_hands"], 2, max_attempts=2))
        self.assertFalse(should_retry_qa(["low_resolution"], 1, max_attempts=2))

    def test_qa_failure_state_uses_shared_audit_shape(self) -> None:
        issues = build_structured_qa_issues(["bad_hands"], source="vision", notes="extra fingers")
        params = merge_qa_failure_state(
            {"debug": {"qa_history": [{"attempt": 1}]}},
            attempt=2,
            reasons=["bad_hands"],
            candidate_url="https://example.com/candidate.png",
            engine="wenwen",
            issues=issues,
            extra_params={"couple_guardrails": {"is_couple": True}},
        )

        self.assertEqual(params["qa_last_reasons"], ["bad_hands"])
        self.assertEqual(params["qa_last_issues"][0]["code"], "bad_hands")
        self.assertEqual(params["qa_last_issues"][0]["category"], "anatomy")
        self.assertEqual(params["qa_last_issues"][0]["repair_action"], "repair_hands_only")
        self.assertEqual(params["qa_attempt_count"], 2)
        self.assertEqual(params["couple_guardrails"], {"is_couple": True})
        self.assertEqual(params["debug"]["qa_history"][-1]["engine"], "wenwen")
        self.assertEqual(params["debug"]["qa_history"][-1]["candidate_url"], "https://example.com/candidate.png")
        self.assertEqual(params["debug"]["qa_history"][-1]["issues"][0]["target"], "hands")

    def test_lighting_qa_reasons_are_structured_and_actionable(self) -> None:
        issues = build_structured_qa_issues(
            [
                "underexposed_face",
                "flat_light",
                "missing_catchlights",
                "oily_skin",
                "blown_out_dress",
                "mixed_color_temp",
                "background_brighter",
            ],
            source="vision",
            notes="lighting fail",
        )

        self.assertEqual(
            [issue["code"] for issue in issues],
            [
                "face_underexposed",
                "flat_lighting",
                "no_catchlights",
                "oily_skin_highlight",
                "dress_highlights_blown",
                "mixed_color_temperature",
                "background_brighter_than_face",
            ],
        )
        self.assertTrue(all(issue["blocking"] for issue in issues))
        self.assertTrue(all(issue["category"] == "photography_quality" for issue in issues))
        self.assertEqual(issues[0]["repair_action"], "raise_face_exposure_with_soft_fill")
        self.assertEqual(issues[3]["repair_action"], "reduce_specular_skin_highlights")
        self.assertEqual(issues[6]["target"], "face_background_exposure_balance")

    def test_structured_qa_issues_classify_face_and_identity_failures(self) -> None:
        issues = build_structured_qa_issues(
            ["bad_face", "identity_mismatch", "poor_studio_quality", "other"],
            source="vision",
            notes="QA evidence",
        )

        self.assertEqual(normalize_qa_reason("bad_face"), "face_distortion")
        self.assertEqual(normalize_qa_reason("oily_skin"), "oily_skin_highlight")
        self.assertEqual([issue["code"] for issue in issues], [
            "face_distortion",
            "identity_mismatch",
            "poor_studio_quality",
            "other",
        ])
        self.assertEqual(issues[0]["category"], "face")
        self.assertEqual(issues[1]["category"], "identity")
        self.assertEqual(issues[1]["repair_action"], "regenerate_from_identity_refs")
        self.assertEqual(issues[2]["repair_stage"], "final_polish")
        self.assertFalse(issues[3]["blocking"])

    def test_commercial_composition_qa_reasons_are_blocking_and_actionable(self) -> None:
        issues = build_structured_qa_issues(
            [
                "person_too_small",
                "tiny_face",
                "background_overpowering_subject",
                "too_much_headroom",
                "dress_cutoff",
                "weak_interaction",
            ],
            source="vision",
            notes="commercial composition fail",
        )

        self.assertEqual(
            [issue["code"] for issue in issues],
            [
                "subject_too_small",
                "face_too_small",
                "background_dominates",
                "excessive_headroom",
                "dress_cropped",
                "weak_couple_interaction",
            ],
        )
        self.assertTrue(all(issue["blocking"] for issue in issues))
        self.assertEqual(issues[0]["category"], "composition")
        self.assertEqual(issues[4]["category"], "wardrobe")
        self.assertEqual(issues[5]["repair_action"], "improve_couple_interaction")

    def test_commercial_wedding_standard_records_canvas_ranges(self) -> None:
        standard = commercial_wedding_standard()

        self.assertEqual(standard["version"], "commercial_wedding_v1")
        self.assertEqual(standard["single"]["subject_height_range"], [0.72, 0.86])
        self.assertEqual(standard["single"]["minimum_outdoor_subject_height"], 0.55)
        self.assertEqual(standard["couple"]["group_height_range"], [0.68, 0.84])
        self.assertIn("background_dominates", standard["blocking_reasons"])
        self.assertTrue(standard["delivery_gate"]["identity_required"])
        self.assertTrue(standard["candidate_selection"]["enabled"])

    def test_generation_credit_policy_charges_once_and_refunds_failed_qa(self) -> None:
        policy = build_generation_credit_policy(credits_cost=4)
        params = {
            "credits_cost": 4,
            "credit_policy": policy,
            "qa_last_reasons": ["identity_mismatch"],
        }

        refund_amount = resolve_generation_refund_amount(
            params,
            fallback_amount=2,
            failure_code="qa_reject",
        )
        refunded_params = merge_generation_refund_state(
            params,
            refund_amount=refund_amount,
            refund_applied=True,
            failure_code="qa_reject",
            failure_provider="wenwen",
        )

        self.assertTrue(policy["charge_once_per_order"])
        self.assertEqual(policy["automatic_repair_extra_charge"], 0)
        self.assertEqual(refund_amount, 4)
        self.assertEqual(refunded_params["refunded_credits"], 4)
        self.assertEqual(refunded_params["credit_refund"]["amount"], 4)
        self.assertEqual(billable_generation_credits(refunded_params), 0)

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

    def test_wenwen_native_generation_uses_only_configured_model(self) -> None:
        candidates = WenwenService._native_model_candidates()

        self.assertEqual(candidates, ["gemini-3-pro-image-preview"])

    def test_image_edit_candidate_count_is_clamped(self) -> None:
        original = wenwen_module.settings.wenwen_image_edit_candidate_count
        try:
            wenwen_module.settings.wenwen_image_edit_candidate_count = 9
            self.assertEqual(WenwenService._image_edit_candidate_count(), 4)
            wenwen_module.settings.wenwen_image_edit_candidate_count = 0
            self.assertEqual(WenwenService._image_edit_candidate_count(), 1)
        finally:
            wenwen_module.settings.wenwen_image_edit_candidate_count = original

    def test_identity_image_edit_is_hard_required_even_if_config_is_disabled(self) -> None:
        original = wenwen_module.settings.wenwen_require_image_edit_identity
        wenwen_module.settings.wenwen_require_image_edit_identity = False
        try:
            self.assertTrue(WenwenService._identity_edit_required(["https://cdn.example.com/source.jpg"]))
            self.assertFalse(WenwenService._identity_edit_required([]))
        finally:
            wenwen_module.settings.wenwen_require_image_edit_identity = original

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

    def test_image_edit_uses_only_configured_model(self) -> None:
        self.assertTrue(WenwenService._image_edit_uses_native_model("gemini-3-pro-image-preview"))
        self.assertTrue(WenwenService._image_edit_uses_native_model("models/gemini-3-pro-image-preview"))
        self.assertFalse(WenwenService._image_edit_uses_native_model("gpt-image-1"))
        candidates = WenwenService._image_edit_model_candidates("gemini-3-pro-image-preview")
        self.assertEqual(candidates, ["gemini-3-pro-image-preview"])
        self.assertEqual(
            WenwenService._image_edit_model_candidates("gemini-3.1-flash-image-preview"),
            ["gemini-3.1-flash-image-preview"],
        )
        self.assertEqual(
            WenwenService._native_image_edit_model_candidates("gemini-3.1-flash-image-preview"),
            ["gemini-3.1-flash-image-preview"],
        )
        self.assertTrue(
            WenwenService._is_model_unavailable_error(
                RuntimeError("not supported model for image generation, only imagen models are supported")
            )
        )

    def test_image_edit_fallback_settings_are_ignored_by_candidates(self) -> None:
        original_fallbacks = wenwen_module.settings.wenwen_image_edit_fallback_models
        try:
            wenwen_module.settings.wenwen_image_edit_fallback_models = (
                "gemini-3.1-flash-image-preview"
            )

            self.assertEqual(
                WenwenService._image_edit_model_candidates("gemini-3-pro-image-preview"),
                ["gemini-3-pro-image-preview"],
            )
        finally:
            wenwen_module.settings.wenwen_image_edit_fallback_models = original_fallbacks

    def test_native_gemini_request_uses_url_key_without_bearer_auth(self) -> None:
        original_key = wenwen_module.settings.wenwen_api_key
        original_base_url = wenwen_module.settings.wenwen_api_base_url
        try:
            wenwen_module.settings.wenwen_api_key = "test-wenwen-key"
            wenwen_module.settings.wenwen_api_base_url = "https://breakout.wenwen-ai.com/v1"

            public_url = WenwenService._native_generation_url_for_model("gemini-3-pro-image-preview")
            request_url = WenwenService._native_generation_request_url_for_model("gemini-3-pro-image-preview")
            parsed = urlparse(request_url)

            self.assertEqual(public_url, "https://breakout.wenwen-ai.com/v1beta/models/gemini-3-pro-image-preview:generateContent")
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "breakout.wenwen-ai.com")
            self.assertEqual(parsed.path, "/v1beta/models/gemini-3-pro-image-preview:generateContent")
            self.assertEqual(parse_qs(parsed.query), {"key": ["test-wenwen-key"]})
            self.assertEqual(WenwenService._native_headers(), {"Content-Type": "application/json"})
        finally:
            wenwen_module.settings.wenwen_api_key = original_key
            wenwen_module.settings.wenwen_api_base_url = original_base_url

    async def test_native_image_edit_payload_prioritizes_identity_pack(self) -> None:
        original = self._data_url()
        face_crop = self._data_url(width=360, height=360)
        upper_body = self._data_url(width=540, height=720)
        current_candidate = self._data_url(width=768, height=1024)
        style_ref = self._data_url(width=900, height=1200)
        identity_pack = {
            "subjects": [
                {
                    "role": "bride",
                    "identity_label": "person_a",
                    "original_url": original,
                    "face_crop_url": face_crop,
                    "upper_body_crop_url": upper_body,
                }
            ]
        }

        payload, entries = await WenwenService()._build_native_image_edit_payload(
            edit_prompt="Preserve identity and create a professional wedding portrait.",
            negative_prompt="generic face, warped hands",
            identity_refs=[original],
            style_refs=[style_ref],
            current_result_refs=[current_candidate],
            identity_reference_pack=identity_pack,
            include_previous_result=True,
            is_couple=False,
        )

        labels = [label for label, _url in entries]
        self.assertIn("Identity anchor - bride original portrait", labels[0])
        self.assertIn("Identity anchor - bride face crop", labels[1])
        self.assertIn("Identity anchor - bride upper-body crop", labels[2])
        self.assertTrue(any(label.startswith("Current candidate canvas") for label in labels))
        self.assertTrue(any(label.startswith("Style or scene reference image") for label in labels))
        self.assertEqual(entries.count(("Identity full source image 1", original)), 0)
        self.assertEqual(payload["contents"][0]["role"], "user")
        self.assertEqual(payload["generationConfig"]["responseModalities"], ["TEXT", "IMAGE"])
        self.assertIn("imageConfig", payload["generationConfig"])
        self.assertEqual(payload["generationConfig"]["imageConfig"]["imageSize"], "4K")
        joined_text = "\n".join(
            part.get("text", "")
            for part in payload["contents"][0]["parts"]
            if isinstance(part, dict) and "text" in part
        )
        self.assertIn("NATIVE GEMINI IMAGE-EDIT MODE", joined_text)
        self.assertIn("identity anchor images override style", joined_text)

    def test_gemini_image_edit_dispatches_to_native_generate_content(self) -> None:
        service = WenwenService()
        calls: list[tuple[str, str]] = []

        async def fake_resume_state(order_uuid: uuid.UUID) -> dict:
            return {}

        async def fake_native_round(order_uuid: uuid.UUID, **kwargs) -> dict:
            calls.append(("native", kwargs["model"]))
            return {
                "round": kwargs["round_number"],
                "stage": "primary_generation",
                "delivered_urls": ["https://example.test/generated.png"],
                "provider_urls": [],
                "qa_ok": True,
                "qa_reasons": [],
                "qa_issues": [],
                "selected_candidate_index": 0,
                "candidate_scores": [{"index": 0, "score": 98.0}],
                "selection": {"score": 98.0},
            }

        async def fail_multipart_round(order_uuid: uuid.UUID, **kwargs) -> dict:
            raise AssertionError("Gemini image models must not call /v1/images/edits")

        async def fake_complete_order(order_uuid: uuid.UUID, **kwargs) -> None:
            calls.append(("complete", ",".join(kwargs["delivered_urls"])))

        service._load_image_edit_resume_state = fake_resume_state  # type: ignore[method-assign]
        service._submit_native_image_edit_round = fake_native_round  # type: ignore[method-assign]
        service._submit_image_edit_round = fail_multipart_round  # type: ignore[method-assign]
        service._complete_order = fake_complete_order  # type: ignore[method-assign]

        completed = asyncio.run(
            service._run_image_edit_generation(
                uuid.uuid4(),
                refs=[self._data_url()],
                prompt_text="Create a commercial wedding portrait.",
                negative_prompt="generic face",
                user_images=[self._data_url()],
                identity_reference_pack=None,
                subject_count=1,
                couple_flow=None,
            )
        )

        self.assertTrue(completed)
        self.assertTrue(all(call[0] != "multipart" for call in calls))
        self.assertEqual(
            calls,
            [
                ("native", "gemini-3-pro-image-preview"),
                ("native", "gemini-3-pro-image-preview"),
                ("native", "gemini-3-pro-image-preview"),
                ("complete", "https://example.test/generated.png"),
            ],
        )

    async def test_image_edit_vision_error_queues_same_candidate_recheck(self) -> None:
        service = WenwenService()
        calls: list[tuple] = []

        async def fake_resume_state(order_uuid: uuid.UUID) -> dict:
            return {}

        async def fake_native_round(order_uuid: uuid.UUID, **kwargs) -> dict:
            calls.append(("round", kwargs["round_number"]))
            return {
                "round": kwargs["round_number"],
                "stage": "primary_generation",
                "delivered_urls": ["https://example.test/candidate.jpg"],
                "provider_urls": ["https://provider.example.test/candidate.png"],
                "qa_ok": False,
                "qa_reasons": ["vision_error"],
                "qa_issues": [{"code": "vision_error", "severity": "critical"}],
                "selected_candidate_index": 0,
                "candidate_scores": [{"index": 0, "score": 0.0, "hard_gate_reasons": ["vision_error"]}],
                "selection": {"score": 0.0},
            }

        async def fake_mark_retry(
            order_uuid: uuid.UUID,
            *,
            attempt: int,
            reasons: list[str],
            candidate_url: str,
            retry_kind: str = "generation_repair",
        ) -> None:
            calls.append(("mark_retry", attempt, retry_kind, candidate_url, tuple(reasons)))

        async def fail_multipart_round(order_uuid: uuid.UUID, **kwargs) -> dict:
            raise AssertionError("Gemini image models must not call /v1/images/edits")

        async def fail_complete_order(order_uuid: uuid.UUID, **kwargs) -> None:
            raise AssertionError("vision_error must not complete or regenerate before QA recheck")

        service._load_image_edit_resume_state = fake_resume_state  # type: ignore[method-assign]
        service._submit_native_image_edit_round = fake_native_round  # type: ignore[method-assign]
        service._submit_image_edit_round = fail_multipart_round  # type: ignore[method-assign]
        service._mark_qa_retry_pending = fake_mark_retry  # type: ignore[method-assign]
        service._complete_order = fail_complete_order  # type: ignore[method-assign]

        completed = await service._run_image_edit_generation(
            uuid.uuid4(),
            refs=[self._data_url()],
            prompt_text="Create a commercial wedding portrait.",
            negative_prompt="generic face",
            user_images=[self._data_url()],
            identity_reference_pack=None,
            subject_count=1,
            couple_flow=None,
        )

        self.assertTrue(completed)
        self.assertEqual(
            calls,
            [
                ("round", 1),
                (
                    "mark_retry",
                    1,
                    "vision_recheck",
                    "https://example.test/candidate.jpg",
                    ("vision_error",),
                ),
            ],
        )

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

    async def test_image_edit_files_include_current_candidate_before_style_refs(self) -> None:
        files = await WenwenService()._build_image_edit_reference_files(
            [self._data_url()],
            style_refs=[self._data_url()],
            current_result_refs=[self._data_url()],
        )

        names = [item[1][0] for item in files]
        self.assertEqual(len(files), 4)
        self.assertTrue(names[0].startswith("identity_full_1."))
        self.assertEqual(names[1], "identity_closeup_1.jpg")
        self.assertTrue(names[2].startswith("current_candidate_1."))
        self.assertTrue(names[3].startswith("style_reference_1."))

    def test_image_edit_round_prompts_are_stage_specific(self) -> None:
        repair = WenwenService._build_image_edit_round_prompt(
            base_prompt="IDENTITY LOCK: keep face.\nSTUDIO QUALITY: premium.",
            negative_prompt="generic face",
            round_number=2,
            qa_reasons=["identity_mismatch", "poor_studio_quality"],
            identity_pack_note="Identity reference pack role order: person_a=bride.",
            include_previous_result=False,
            is_couple=False,
        )
        polish = WenwenService._build_image_edit_round_prompt(
            base_prompt="IDENTITY LOCK: keep face.\nSTUDIO QUALITY: premium.",
            negative_prompt="generic face",
            round_number=3,
            qa_reasons=[],
            identity_pack_note="",
            include_previous_result=True,
            is_couple=True,
        )

        self.assertIn("ROUND 2 TARGETED REPAIR", repair)
        self.assertIn("restore facial identity", repair)
        self.assertIn("Do not rely on the previous failed candidate", repair)
        self.assertIn("DELIVERY HARD GATE", repair)
        self.assertIn("ROUND 3 FINAL POLISH", polish)
        self.assertIn("Couple rule", polish)
        self.assertIn("Negative prompt: generic face", polish)

    def test_lighting_only_round_two_is_relight_edit_not_face_regeneration(self) -> None:
        reasons = ["face_underexposed", "oily_skin_highlight", "background_brighter_than_face"]
        prompt = WenwenService._build_image_edit_round_prompt(
            base_prompt="IDENTITY LOCK: keep face.\nSTUDIO QUALITY: premium.",
            negative_prompt="generic face",
            round_number=2,
            qa_reasons=reasons,
            identity_pack_note="",
            include_previous_result=True,
            is_couple=False,
        )

        self.assertTrue(WenwenService._is_lighting_only_repair(reasons, round_number=2))
        self.assertEqual(
            WenwenService._image_edit_repair_mode(round_number=2, qa_reasons=reasons),
            "relight_edit_only",
        )
        self.assertIn("ROUND 2 RELIGHT/EDIT ONLY", prompt)
        self.assertIn("previous candidate is the current canvas", prompt)
        self.assertIn("Only edit lighting and finish", prompt)
        self.assertIn("Treat this as relight/edit", prompt)
        self.assertIn("Do not redraw", prompt)
        self.assertIn("Do not change facial geometry", prompt)
        self.assertNotIn("regenerate the repair", prompt)
        self.assertNotIn("replace facial identity", prompt)

    def test_mixed_round_two_failures_do_not_use_relight_only_mode(self) -> None:
        reasons = ["face_underexposed", "identity_mismatch"]
        prompt = WenwenService._build_image_edit_round_prompt(
            base_prompt="IDENTITY LOCK: keep face.\nSTUDIO QUALITY: premium.",
            negative_prompt="generic face",
            round_number=2,
            qa_reasons=reasons,
            identity_pack_note="",
            include_previous_result=False,
            is_couple=False,
        )

        self.assertFalse(WenwenService._is_lighting_only_repair(reasons, round_number=2))
        self.assertEqual(
            WenwenService._image_edit_repair_mode(round_number=2, qa_reasons=reasons),
            "targeted_repair",
        )
        self.assertIn("ROUND 2 TARGETED REPAIR", prompt)
        self.assertIn("restore facial identity", prompt)
        self.assertIn("regenerate the repair from the original identity references", prompt)

    def test_candidate_selection_prefers_deliverable_identity_safe_image(self) -> None:
        failed_verdict = {
            "passed": False,
            "reasons": ["identity_mismatch"],
            "issues": [{"code": "identity_mismatch", "severity": "critical"}],
        }
        passed_verdict = {"passed": True, "reasons": [], "issues": []}
        failed_selection = WenwenService._score_candidate_verdict(
            failed_verdict,
            round_number=1,
            candidate_index=0,
        )
        passed_selection = WenwenService._score_candidate_verdict(
            passed_verdict,
            round_number=1,
            candidate_index=1,
        )
        selected = WenwenService._select_best_candidate(
            [
                {
                    "index": 0,
                    "url": "https://cdn.example.com/bad.jpg",
                    "qa_ok": bool(failed_selection["passed"]),
                    "selection": failed_selection,
                },
                {
                    "index": 1,
                    "url": "https://cdn.example.com/good.jpg",
                    "qa_ok": bool(passed_selection["passed"]),
                    "selection": passed_selection,
                },
            ]
        )

        self.assertFalse(failed_selection["passed"])
        self.assertIn("identity_mismatch", failed_selection["hard_gate_reasons"])
        self.assertTrue(passed_selection["passed"])
        self.assertEqual(selected["url"], "https://cdn.example.com/good.jpg")

    def test_identity_mismatch_repair_does_not_use_previous_failed_candidate(self) -> None:
        self.assertFalse(WenwenService._should_include_previous_edit_result(["identity_mismatch"]))
        self.assertFalse(WenwenService._should_include_previous_edit_result(["identity_swap"]))
        self.assertTrue(WenwenService._should_include_previous_edit_result(["poor_studio_quality"]))
        self.assertTrue(WenwenService._should_include_previous_edit_result(["bad_hands"]))

    def test_bad_hands_repair_allows_simpler_hand_pose(self) -> None:
        focus = WenwenService._repair_focus_from_reasons(["bad_hands"], is_couple=False)

        self.assertIn("simple professional bridal hand placement", focus)
        self.assertIn("do not preserve the failed hand pose", focus)

    def test_lighting_repair_focus_uses_specific_qa_reasons(self) -> None:
        focus = WenwenService._repair_focus_from_reasons(
            [
                "face_underexposed",
                "flat_lighting",
                "no_catchlights",
                "oily_skin_highlight",
                "dress_highlights_blown",
                "mixed_color_temperature",
                "background_brighter_than_face",
            ],
            is_couple=False,
        )

        self.assertIn("raise facial exposure", focus)
        self.assertIn("directional key light", focus)
        self.assertIn("eye catchlights", focus)
        self.assertIn("semi-matte natural skin texture", focus)
        self.assertIn("recover white dress", focus)
        self.assertIn("unify color temperature", focus)
        self.assertIn("face the clear exposure priority", focus)

    async def test_identity_qa_hard_fails_when_vision_provider_is_unavailable(self) -> None:
        original = qa_service.llm_service.is_vision_provider_configured
        original_require_vision = qa_service.settings.qa_require_vision
        original_required = qa_service.settings.qa_require_identity_vision
        original_fail_on_error = qa_service.settings.qa_fail_on_vision_error
        qa_service.llm_service.is_vision_provider_configured = lambda: False
        qa_service.settings.qa_require_vision = False
        qa_service.settings.qa_require_identity_vision = True
        qa_service.settings.qa_fail_on_vision_error = False
        try:
            passed, reasons = await qa_service.verify_with_vision(
                "https://cdn.example.com/generated.jpg",
                source_image_urls=["https://cdn.example.com/source.jpg"],
            )
        finally:
            qa_service.llm_service.is_vision_provider_configured = original
            qa_service.settings.qa_require_vision = original_require_vision
            qa_service.settings.qa_require_identity_vision = original_required
            qa_service.settings.qa_fail_on_vision_error = original_fail_on_error

        self.assertFalse(passed)
        self.assertEqual(reasons, ["vision_error"])

    async def test_non_identity_qa_can_still_degrade_when_vision_provider_is_unavailable(self) -> None:
        original = qa_service.llm_service.is_vision_provider_configured
        original_require_vision = qa_service.settings.qa_require_vision
        original_required = qa_service.settings.qa_require_identity_vision
        qa_service.llm_service.is_vision_provider_configured = lambda: False
        qa_service.settings.qa_require_vision = False
        qa_service.settings.qa_require_identity_vision = True
        try:
            passed, reasons = await qa_service.verify_with_vision(
                "https://cdn.example.com/generated.jpg",
                source_image_urls=[],
            )
        finally:
            qa_service.llm_service.is_vision_provider_configured = original
            qa_service.settings.qa_require_vision = original_require_vision
            qa_service.settings.qa_require_identity_vision = original_required

        self.assertTrue(passed)
        self.assertEqual(reasons, [])

    def test_vision_qa_accepts_json_array_wrapped_verdict(self) -> None:
        async def fake_chat(payload, *, title, timeout, provider):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '[{"passed": false, "reasons": ["bad_hands"], '
                                '"issues": [{"code": "bad_hands", "evidence": "broken fingers"}], '
                                '"notes": "hand issue"}]'
                            )
                        }
                    }
                ]
            }

        original_configured = llm_service.is_vision_provider_configured
        original_chat = llm_service._llm_chat_for_provider
        original_provider = llm_service.settings.llm_provider
        original_jiekou_key = llm_service.settings.jiekou_api_key
        original_wenwen_vision_key = llm_service.settings.wenwen_vision_api_key
        llm_service.is_vision_provider_configured = lambda: True
        llm_service._llm_chat_for_provider = fake_chat
        llm_service.settings.llm_provider = "jiekou"
        llm_service.settings.jiekou_api_key = "test-jiekou-key"
        llm_service.settings.wenwen_vision_api_key = ""
        try:
            verdict = asyncio.run(
                llm_service.verify_generated_image_quality("https://cdn.example.com/generated.jpg")
            )
        finally:
            llm_service.is_vision_provider_configured = original_configured
            llm_service._llm_chat_for_provider = original_chat
            llm_service.settings.llm_provider = original_provider
            llm_service.settings.jiekou_api_key = original_jiekou_key
            llm_service.settings.wenwen_vision_api_key = original_wenwen_vision_key

        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["reasons"], ["bad_hands"])
        self.assertEqual(verdict["issues"][0]["code"], "bad_hands")

    def test_vision_qa_preserves_specific_lighting_reasons(self) -> None:
        async def fake_chat(payload, *, title, timeout, provider):
            prompt_text = str(payload["messages"][0]["content"][0]["text"])
            self.assertIn("face_underexposed", prompt_text)
            self.assertIn("oily_skin_highlight", prompt_text)
            self.assertIn("background_brighter_than_face", prompt_text)
            self.assertIn("Prefer these specific lighting reasons", prompt_text)
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"passed": false, '
                                '"reasons": ["underexposed_face", "oily_skin", "background_brighter"], '
                                '"issues": ['
                                '{"code": "underexposed_face", "evidence": "face is in shadow"}, '
                                '{"code": "oily_skin", "evidence": "forehead is wet glossy"}, '
                                '{"code": "background_brighter", "evidence": "window is brighter than face"}'
                                '], '
                                '"notes": "lighting issue"}'
                            )
                        }
                    }
                ]
            }

        original_configured = llm_service.is_vision_provider_configured
        original_chat = llm_service._llm_chat_for_provider
        original_provider = llm_service.settings.llm_provider
        original_jiekou_key = llm_service.settings.jiekou_api_key
        original_wenwen_vision_key = llm_service.settings.wenwen_vision_api_key
        llm_service.is_vision_provider_configured = lambda: True
        llm_service._llm_chat_for_provider = fake_chat
        llm_service.settings.llm_provider = "jiekou"
        llm_service.settings.jiekou_api_key = "test-jiekou-key"
        llm_service.settings.wenwen_vision_api_key = ""
        try:
            verdict = asyncio.run(
                llm_service.verify_generated_image_quality("https://cdn.example.com/generated.jpg")
            )
        finally:
            llm_service.is_vision_provider_configured = original_configured
            llm_service._llm_chat_for_provider = original_chat
            llm_service.settings.llm_provider = original_provider
            llm_service.settings.jiekou_api_key = original_jiekou_key
            llm_service.settings.wenwen_vision_api_key = original_wenwen_vision_key

        self.assertFalse(verdict["passed"])
        self.assertEqual(
            verdict["reasons"],
            ["face_underexposed", "oily_skin_highlight", "background_brighter_than_face"],
        )
        self.assertEqual(verdict["issues"][0]["repair_action"], "raise_face_exposure_with_soft_fill")
        self.assertEqual(verdict["issues"][1]["code"], "oily_skin_highlight")
        self.assertEqual(verdict["issues"][2]["target"], "face_background_exposure_balance")

    def test_vision_qa_falls_back_to_secondary_provider_on_timeout(self) -> None:
        calls: list[tuple[str | None, str]] = []

        async def fake_chat(payload, *, title, timeout, provider):
            calls.append((provider, str(payload.get("model") or "")))
            if provider == "wenwen":
                raise TimeoutError("read timed out")
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"passed": true, "reasons": [], "issues": [], "notes": "ok"}'
                        }
                    }
                ]
            }

        original_chat = llm_service._llm_chat_for_provider
        original_provider = llm_service.settings.llm_provider
        original_jiekou_key = llm_service.settings.jiekou_api_key
        original_jiekou_model = llm_service.settings.jiekou_vision_model
        original_wenwen_vision_key = llm_service.settings.wenwen_vision_api_key
        original_wenwen_model = llm_service.settings.wenwen_vision_model
        llm_service._llm_chat_for_provider = fake_chat
        llm_service.settings.llm_provider = "wenwen"
        llm_service.settings.wenwen_vision_api_key = "test-wenwen-vision-key"
        llm_service.settings.wenwen_vision_model = "wenwen-vision-model"
        llm_service.settings.jiekou_api_key = "test-jiekou-key"
        llm_service.settings.jiekou_vision_model = "jiekou-vision-model"
        try:
            verdict = asyncio.run(
                llm_service.verify_generated_image_quality(
                    "https://cdn.example.com/generated.jpg",
                    source_image_urls=["https://cdn.example.com/source.jpg"],
                )
            )
        finally:
            llm_service._llm_chat_for_provider = original_chat
            llm_service.settings.llm_provider = original_provider
            llm_service.settings.jiekou_api_key = original_jiekou_key
            llm_service.settings.jiekou_vision_model = original_jiekou_model
            llm_service.settings.wenwen_vision_api_key = original_wenwen_vision_key
            llm_service.settings.wenwen_vision_model = original_wenwen_model

        self.assertTrue(verdict["passed"])
        self.assertEqual(calls, [("wenwen", "wenwen-vision-model"), ("jiekou", "jiekou-vision-model")])
        self.assertIn("qa_provider_fallback=jiekou", verdict["notes"])


if __name__ == "__main__":
    unittest.main()
