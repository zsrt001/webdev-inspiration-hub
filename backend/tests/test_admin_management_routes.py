"""Admin management route contract tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class AdminManagementRoutesTest(unittest.TestCase):
    def test_admin_management_routes_are_registered(self) -> None:
        from app.routers import api_router
        from tests.route_contract import effective_routes

        routes = {(route.path, ",".join(sorted(route.methods or []))) for route in effective_routes(api_router)}
        paths = {path for path, _methods in routes}

        self.assertIn("/admin/dashboard", paths)
        self.assertIn("/admin/me", paths)
        self.assertIn("/admin/payment_config_summary", paths)
        self.assertIn("/admin/creem_product_check", paths)
        self.assertIn("/admin/creem_checkout_probe", paths)
        self.assertIn("/admin/generation_probe", paths)
        self.assertIn("/admin/analytics_overview", paths)
        self.assertIn("/admin/users", paths)
        self.assertIn("/admin/users/{user_id}/status", paths)
        self.assertIn("/admin/orders", paths)
        self.assertIn("/admin/orders/{order_id}", paths)
        self.assertIn("/admin/orders/{order_id}/status", paths)
        self.assertIn("/admin/orders/{order_id}/regenerate", paths)

    def test_order_status_options_reuse_existing_enum(self) -> None:
        from app.models.order import OrderStatus
        from app.routers import admin

        self.assertEqual({item.value for item in OrderStatus}, admin.ORDER_STATUS_VALUES)
        self.assertNotIn("PREVIEW_READY", admin.ORDER_STATUS_VALUES)
        self.assertNotIn("PAID", admin.ORDER_STATUS_VALUES)
        self.assertNotIn("UPSCALING", admin.ORDER_STATUS_VALUES)

    def test_legacy_public_routes_are_removed(self) -> None:
        from app.routers import api_router
        from tests.route_contract import effective_routes

        routes = {(route.path, ",".join(sorted(route.methods or []))) for route in effective_routes(api_router)}

        self.assertFalse(any(path == "/templates/list" and "GET" in methods for path, methods in routes))
        self.assertFalse(any(path == "/orders" and "POST" in methods for path, methods in routes))

    def test_generation_probe_and_regenerate_are_side_effect_free_tombstones(self) -> None:
        from app.routers import admin, retired

        admin_source = (BACKEND_DIR / "app/routers/admin.py").read_text(encoding="utf-8")
        retired_source = (BACKEND_DIR / "app/routers/retired.py").read_text(encoding="utf-8")
        for forbidden in (
            "GenerationProbeRequest",
            "run_order_generation",
            "enqueue_generate_order",
            "admin-probe-inline",
            "admin-regenerate-inline",
            '@router.post("/generation_probe"',
            '@router.post("/orders/{order_id}/regenerate"',
        ):
            self.assertNotIn(forbidden, admin_source)
        self.assertIn('@router.post("/admin/generation_probe"', retired_source)
        self.assertIn('@router.post("/admin/orders/{order_id}/regenerate"', retired_source)

        from app.routers.admin import AdminMeResponse

        admin_properties = AdminMeResponse.model_json_schema()["properties"]
        self.assertNotIn("remote_join_enabled", admin_properties)
        self.assertNotIn("remote_join_session_store", admin_properties)

    def test_admin_order_detail_extracts_generation_rounds_and_qa_summary(self) -> None:
        import uuid

        from app.models.order import Order, OrderStatus
        from app.routers.admin import _order_detail

        order = Order(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            status=OrderStatus.CREATED,
            template_id="solo_royal_castle",
            source_image_urls={"images": ["https://cdn.example.com/source.jpg"]},
            generation_params={
                "credits_cost": 2,
                "refunded_credits": 2,
                "failure_code": "qa_reject",
                "failure_provider": "wenwen",
                "qa_last_reasons": ["identity_mismatch"],
                "qa_last_issues": [{"code": "identity_mismatch"}],
                "qa_attempt_count": 3,
                "credit_refund": {"applied": True, "amount": 2},
                "debug": {
                    "image_edit_rounds": [
                        {
                            "round": 1,
                            "generation_attempt": 2,
                            "stage": "primary_generation",
                            "repair_mode": "relight_edit_only",
                            "candidate_url": "https://cdn.example.com/candidate.jpg",
                            "candidate_urls": [
                                "https://cdn.example.com/candidate.jpg",
                                "https://cdn.example.com/candidate-2.jpg",
                            ],
                            "selected_candidate_url": "https://cdn.example.com/candidate-2.jpg",
                            "selected_candidate_index": 1,
                            "candidate_scores": [
                                {
                                    "index": 0,
                                    "score": 12.5,
                                    "qa_passed": False,
                                    "hard_gate_reasons": ["identity_mismatch"],
                                },
                                {
                                    "index": 1,
                                    "score": 96.0,
                                    "qa_passed": True,
                                    "hard_gate_reasons": [],
                                },
                            ],
                            "selection_policy": "qa_score_v1",
                            "qa_passed": False,
                            "qa_reasons": ["identity_mismatch"],
                            "qa_issues": [{"code": "identity_mismatch"}],
                            "billable": False,
                            "billing_reason": "automatic_repair_included",
                            "extra_credits_charged": 0,
                        }
                    ]
                },
            },
            price_cents=0,
        )

        detail = _order_detail(order, None)

        self.assertFalse(detail.can_regenerate)
        self.assertEqual(detail.failure_code, "qa_reject")
        self.assertEqual(detail.qa_summary.qa_last_reasons, ["identity_mismatch"])
        self.assertEqual(detail.qa_summary.credit_refund, {"applied": True, "amount": 2})
        self.assertEqual(len(detail.generation_rounds), 1)
        self.assertEqual(detail.generation_rounds[0].candidate_url, "https://cdn.example.com/candidate.jpg")
        self.assertEqual(detail.generation_rounds[0].selected_candidate_url, "https://cdn.example.com/candidate-2.jpg")
        self.assertEqual(detail.generation_rounds[0].selected_candidate_index, 1)
        self.assertEqual(len(detail.generation_rounds[0].candidate_urls), 2)
        self.assertEqual(detail.generation_rounds[0].candidate_scores[1]["score"], 96.0)
        self.assertEqual(detail.generation_rounds[0].selection_policy, "qa_score_v1")
        self.assertEqual(detail.generation_rounds[0].repair_mode, "relight_edit_only")
        self.assertFalse(detail.generation_rounds[0].billable)
        self.assertEqual(detail.generation_rounds[0].billing_reason, "automatic_repair_included")

    def test_payment_config_summary_does_not_expose_secrets(self) -> None:
        import asyncio

        from app.routers.admin import get_payment_config_summary

        summary = asyncio.run(get_payment_config_summary()).model_dump()
        serialized = str(summary).lower()

        self.assertIn(summary["creem_api_key_mode"], {"missing", "test", "live", "unknown"})
        self.assertIsInstance(summary["debug"], bool)
        self.assertNotIn("api_key", summary)
        self.assertNotIn("webhook_secret", summary)
        self.assertNotIn("creem_test_", serialized)

    def test_admin_credit_adjustment_requires_idempotency_approval_and_lineage(self) -> None:
        from app.routers.admin import GrantCreditsRequest

        schema = GrantCreditsRequest.model_json_schema()
        required = set(schema["required"])
        self.assertTrue(
            {
                "user_id",
                "amount",
                "idempotency_key",
                "approval_id",
                "reason",
            }.issubset(required)
        )
        properties = set(schema["properties"])
        self.assertIn("positive_grant_policy", properties)
        self.assertIn("reversal_roots", properties)

        service_source = (
            BACKEND_DIR / "app/services/admin_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("def grant_credits_to_user", service_source)
        self.assertNotIn("add_credits_async", service_source)


if __name__ == "__main__":
    unittest.main()
