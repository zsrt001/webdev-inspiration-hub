"""Customer-facing order response contract tests."""

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
import uuid


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.order import Order, OrderStatus  # noqa: E402
from app.schemas.order import OrderRead  # noqa: E402


class OrderPublicContractTest(unittest.TestCase):
    def test_customer_order_read_hides_internal_generation_artifacts(self) -> None:
        now = datetime.now(timezone.utc)
        order = Order(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            status=OrderStatus.COMPLETED,
            template_id="solo_royal_castle",
            source_image_urls={
                "images": ["https://cdn.example.com/source.jpg"],
                "identity_reference_pack": {
                    "subjects": [
                        {
                            "face_crop_url": "https://cdn.example.com/internal-face.jpg",
                            "upper_body_crop_url": "https://cdn.example.com/internal-upper.jpg",
                        }
                    ]
                },
            },
            preview_image_urls={
                "image_1": "https://cdn.example.com/preview.jpg",
                "image_1_square_1x1": "https://cdn.example.com/preview-square.jpg",
            },
            final_image_urls={
                "image_1": "https://cdn.example.com/final.jpg",
                "image_1_square_1x1": "https://cdn.example.com/final-square.jpg",
            },
            generation_params={
                "credits_cost": 4,
                "refunded_credits": 0,
                "commercial_standard_version": "commercial_wedding_v3",
                "director_mode": True,
                "subject_count": 1,
                "generation_stage": "completed",
                "generation_stage_history": [{"stage": "completed"}],
                "identity_reference_pack": {"secret": True},
                "qa_last_reasons": ["subject_too_small"],
                "qa_last_issues": [{"code": "subject_too_small", "candidate_url": "https://cdn.example.com/bad.jpg"}],
                "prompt": "internal prompt",
                "negative_prompt": "internal negative prompt",
                "provider_task_id": "secret-provider-task",
                "debug": {
                    "image_edit_rounds": [
                        {
                            "candidate_url": "https://cdn.example.com/round-1.jpg",
                            "repair_mode": "relight_edit_only",
                            "billing_reason": "automatic_repair_included",
                            "qa_issues": [{"code": "face_too_small"}],
                        }
                    ]
                },
            },
            price_cents=0,
            created_at=now,
            updated_at=now,
        )

        payload = OrderRead.model_validate(order)
        dumped = payload.model_dump()

        self.assertEqual(payload.source_image_urls, {"images": ["https://cdn.example.com/source.jpg"]})
        self.assertEqual(payload.preview_master_image_url, "https://cdn.example.com/preview.jpg")
        self.assertEqual(payload.final_master_image_url, "https://cdn.example.com/final.jpg")
        self.assertEqual(
            payload.download_variants,
            [
                {
                    "key": "image_1_square_1x1",
                    "url": "https://cdn.example.com/final-square.jpg",
                    "label": "1:1 square crop",
                    "type": "download_crop",
                }
            ],
        )
        self.assertEqual(payload.credits_cost, 4)
        self.assertEqual(payload.refunded_credits, 0)
        self.assertEqual(payload.generation_stage, "completed")
        self.assertEqual(payload.generation_stage_history, [{"stage": "completed"}])
        self.assertEqual(payload.qa_last_reasons, [])
        self.assertNotIn("identity_reference_pack", dumped)
        self.assertNotIn("qa_last_issues", dumped)
        self.assertNotIn("debug", payload.generation_params or {})
        self.assertNotIn("identity_reference_pack", payload.generation_params or {})
        self.assertNotIn("qa_last_issues", payload.generation_params or {})
        self.assertNotIn("prompt", payload.generation_params or {})
        self.assertNotIn("negative_prompt", payload.generation_params or {})
        self.assertNotIn("provider_task_id", payload.generation_params or {})
        self.assertEqual(
            payload.generation_params,
            {
                "credits_cost": 4,
                "refunded_credits": 0,
                "commercial_standard_version": "commercial_wedding_v3",
                "director_mode": True,
                "subject_count": 1,
                "generation_stage": "completed",
                "generation_stage_history": [{"stage": "completed"}],
            },
        )


if __name__ == "__main__":
    unittest.main()
