"""Order creation service contract tests."""

from pathlib import Path
import asyncio
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import HTTPException  # noqa: E402

from app.schemas.order import OrderCreate  # noqa: E402
from app.services.gatekeeper_service import GatekeeperResult  # noqa: E402
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

    def test_gatekeeper_quality_warnings_do_not_block_order_creation(self) -> None:
        original_check = service.gatekeeper_service.check_image_quality

        async def fake_check(_image_url: str) -> GatekeeperResult:
            return GatekeeperResult(
                passed=True,
                reasons=[],
                advice=[],
                metrics={"portrait_roi_edge_mean": 6.4},
                warnings=["too_blurry"],
                warning_advice=["A sharper portrait is recommended, but you can continue."],
            )

        service.gatekeeper_service.check_image_quality = fake_check
        try:
            result = asyncio.run(service._run_gatekeeper_checks(["https://cdn.example.com/person.jpg"]))
        finally:
            service.gatekeeper_service.check_image_quality = original_check

        self.assertEqual(result[0]["warnings"], ["too_blurry"])
        self.assertEqual(result[0]["reasons"], [])

    def test_gatekeeper_hard_failure_still_blocks_order_creation(self) -> None:
        original_check = service.gatekeeper_service.check_image_quality

        async def fake_check(_image_url: str) -> GatekeeperResult:
            return GatekeeperResult(
                passed=False,
                reasons=["no_face_detected"],
                advice=["No clear face was detected."],
                metrics={},
            )

        service.gatekeeper_service.check_image_quality = fake_check
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(service._run_gatekeeper_checks(["https://cdn.example.com/no-face.jpg"]))
        finally:
            service.gatekeeper_service.check_image_quality = original_check

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail["reasons"], ["no_face_detected"])

    def test_director_scene_text_overrides_upload_and_preset(self) -> None:
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

        self.assertEqual(decision.effective_scene_source, "text")
        self.assertIsNone(decision.effective_scene_image_url)
        self.assertIsNone(decision.effective_scene_ip_weight)
        self.assertIn("scene_image_url", decision.ignored_inputs)
        self.assertIn("scene_preset_id", decision.ignored_inputs)
        self.assertIn("scene:text", decision.director_decision_hints)

    def test_global_style_text_does_not_trigger_random_scene_or_outfit(self) -> None:
        request = OrderCreate(
            template_id="custom",
            user_images=["https://cdn.example.com/person-a.jpg"],
            legal_accepted=True,
            director_mode=True,
            global_style_text="editorial black-and-white wedding portrait in a quiet museum mood",
        )

        decision = service._resolve_director_decision(
            request,
            is_couple_request=False,
            couple_flow=None,
        )

        self.assertEqual(decision.global_style_text, "editorial black-and-white wedding portrait in a quiet museum mood")
        self.assertIsNone(decision.effective_scene_source)
        self.assertIsNone(decision.effective_outfit_source)
        self.assertIsNone(decision.effective_scene_image_url)
        self.assertIsNone(decision.effective_clothing_image_url)
        self.assertEqual(decision.ignored_inputs, [])
        self.assertEqual(decision.director_decision_hints, ["director_mode_enabled"])

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

    def test_generation_params_forbid_text_to_image_identity_fallback(self) -> None:
        request = OrderCreate(
            template_id="classic",
            user_images=["https://cdn.example.com/person-a.jpg"],
            legal_accepted=True,
            upload_quality=[
                {
                    "slot_index": "0",
                    "role": "subject",
                    "image_url": "https://cdn.example.com/person-a.jpg",
                    "quality_score": 82.6,
                    "quality_level": "good",
                    "reasons": ["clear_face"],
                    "risk_flags": [],
                    "metrics": {"face_ratio": 0.34},
                }
            ],
        )
        identity_reference_pack = {
            "version": 1,
            "role_order": ["subject"],
            "subjects": [
                {
                    "role": "subject",
                    "original_url": "https://cdn.example.com/person-a.jpg",
                    "face_crop_url": "https://cdn.example.com/person-a-face.jpg",
                    "upper_body_crop_url": "https://cdn.example.com/person-a-upper.jpg",
                }
            ],
        }

        params = service._build_generation_params(
            request=request,
            gatekeeper_results=[{"passed": True}],
            identity_reference_pack=identity_reference_pack,
            subject_count=1,
            is_couple_request=False,
            couple_flow=None,
            credit_context=service.CreditAccessContext(
                credits_cost=1,
                access_tier="paid",
                has_paid_credits=True,
                retention_plan_code=None,
            ),
            director_decision=service.DirectorDecision(
                global_style_text=None,
                scene_text=None,
                outfit_text=None,
                legacy_prompt_override=None,
                effective_scene_source=None,
                effective_outfit_source=None,
                effective_scene_image_url=None,
                effective_clothing_image_url=None,
                effective_scene_ip_weight=None,
                effective_clothing_ip_weight=None,
                effective_scene_preset_id=None,
                effective_outfit_preset_id=None,
                effective_scene_preset_title=None,
                effective_outfit_preset_title=None,
                ignored_inputs=[],
                director_decision_hints=[],
            ),
        )

        generation_policy = params["quality_control"]["generation"]
        self.assertTrue(generation_policy["identity_edit_required"])
        self.assertTrue(generation_policy["identity_edit_required_by_code"])
        self.assertFalse(generation_policy["text_to_image_fallback_allowed"])
        self.assertFalse(generation_policy["native_generation_fallback_allowed_for_identity"])
        self.assertTrue(generation_policy["shot_library_director"])
        self.assertTrue(generation_policy["multi_round_image_edit"])
        self.assertEqual(
            generation_policy["image_edit_rounds"],
            ["primary_generation", "targeted_repair", "final_polish"],
        )
        self.assertTrue(generation_policy["lighting_only_round_2_relight_edit"])
        self.assertTrue(generation_policy["lighting_only_round_2_no_face_redraw"])
        self.assertEqual(generation_policy["automatic_lighting_repair_extra_charge"], 0)
        self.assertFalse(generation_policy["process_images_customer_visible"])
        self.assertTrue(generation_policy["best_passing_round_delivery"])
        self.assertEqual(generation_policy["automatic_repair_extra_charge"], 0)
        self.assertTrue(generation_policy["no_extra_debit_for_image_edit_rounds"])
        self.assertTrue(params["credit_policy"]["charge_once_per_order"])
        self.assertTrue(params["credit_policy"]["automatic_repair_rounds_included"])
        self.assertEqual(params["credit_policy"]["automatic_repair_extra_charge"], 0)
        self.assertTrue(params["credit_policy"]["refund_on_blocking_qa_failure"])
        self.assertIn("qa_reject", params["credit_policy"]["refund_failure_codes"])
        self.assertEqual(params["identity_reference_pack"], identity_reference_pack)
        self.assertEqual(params["upload_quality"][0]["quality_score"], 83)
        self.assertEqual(params["upload_quality_summary"]["avg_score"], 83)
        self.assertEqual(params["upload_quality_summary"]["warning_count"], 0)

    def test_upload_quality_is_normalized_for_order_params(self) -> None:
        request = OrderCreate(
            template_id="classic",
            user_images=["https://cdn.example.com/person-a.jpg"],
            legal_accepted=True,
            upload_quality=[
                {
                    "slot_index": "bad",
                    "role": "host",
                    "image_url": "https://cdn.example.com/person-a.jpg",
                    "quality_score": 142,
                    "quality_level": "unknown",
                    "reasons": ["low_light"] * 20,
                    "risk_flags": ["small_face"],
                    "metrics": {"blur": "3.45678", "bad": "nan"},
                },
                {
                    "slot_index": 1,
                    "role": "guest",
                    "quality_score": 31,
                    "quality_level": "poor",
                },
            ],
        )

        self.assertEqual(request.upload_quality[0]["slot_index"], 0)
        self.assertEqual(request.upload_quality[0]["quality_score"], 100)
        self.assertEqual(request.upload_quality[0]["quality_level"], "good")
        self.assertEqual(len(request.upload_quality[0]["reasons"]), 12)

        summary = service._upload_quality_summary(request.upload_quality)
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["avg_score"], 65.5)
        self.assertEqual(summary["poor_count"], 1)


if __name__ == "__main__":
    unittest.main()
