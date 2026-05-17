"""Analytics reporting helpers."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.order import OrderStatus  # noqa: E402
from app.services.analytics_reporting_service import (  # noqa: E402
    _build_quality_dashboard_from_orders,
    _identity_grade_from_params,
)


class AnalyticsReportingServiceTest(unittest.TestCase):
    def test_identity_grade_uses_worst_round_grade(self) -> None:
        params = {
            "debug": {
                "image_edit_rounds": [
                    {"identity_grade": "identity_pass"},
                    {
                        "identity_grade": "minor_drift",
                        "candidate_scores": [{"identity_grade": "major_mismatch"}],
                    },
                    {"identity_grade": "identity_pass"},
                ]
            }
        }

        self.assertEqual(_identity_grade_from_params(params), "major_mismatch")

    def test_quality_dashboard_groups_template_reasons_and_repair_success(self) -> None:
        order_rows = [
            (
                "royal_castle",
                OrderStatus.COMPLETED.value,
                {
                    "debug": {
                        "image_edit_rounds": [
                            {
                                "round": 1,
                                "stage": "primary_generation",
                                "repair_mode": "primary_generation",
                                "qa_passed": False,
                                "qa_reasons": ["face_underexposed", "background_brighter_than_face"],
                                "candidate_scores": [
                                    {
                                        "index": 0,
                                        "score": 42,
                                        "reasons": ["face_underexposed"],
                                        "hard_gate_reasons": ["face_underexposed"],
                                    }
                                ],
                                "selected_candidate_index": 0,
                            },
                            {
                                "round": 2,
                                "stage": "targeted_repair",
                                "repair_mode": "relight_edit_only",
                                "qa_passed": True,
                                "qa_reasons": [],
                                "candidate_scores": [{"index": 0, "score": 91, "reasons": []}],
                                "selected_candidate_index": 0,
                            },
                        ]
                    }
                },
                None,
            ),
            (
                "royal_castle",
                OrderStatus.FAILED.value,
                {
                    "failure_code": "qa_reject",
                    "qa_last_reasons": ["identity_similarity_low"],
                    "qa_last_issues": [{"code": "identity_similarity_low"}],
                },
                "QA failed: identity_similarity_low",
            ),
            (
                "solo_royal_castle",
                OrderStatus.FAILED.value,
                {
                    "qa_last_reasons": ["dress_cropped", "subject_too_small"],
                    "debug": {
                        "image_edit_rounds": [
                            {
                                "round": 1,
                                "repair_mode": "primary_generation",
                                "qa_passed": False,
                                "qa_reasons": ["dress_cropped", "subject_too_small"],
                            }
                        ]
                    },
                },
                "QA failed: dress_cropped",
            ),
        ]

        dashboard = _build_quality_dashboard_from_orders(order_rows)

        self.assertEqual(dashboard["totals"]["orders"], 3)
        self.assertEqual(dashboard["totals"]["completed_orders"], 1)
        self.assertEqual(dashboard["totals"]["qa_failed_orders"], 3)
        self.assertEqual(dashboard["totals"]["identity_failed_orders"], 1)
        self.assertEqual(dashboard["totals"]["lighting_failed_orders"], 1)
        self.assertEqual(dashboard["totals"]["composition_failed_orders"], 1)
        self.assertEqual(dashboard["totals"]["relight_attempts"], 1)
        self.assertEqual(dashboard["totals"]["relight_success_rate"], 1.0)

        reasons = {item["reason"]: item for item in dashboard["failure_reasons"]}
        self.assertEqual(reasons["identity_similarity_low"]["group"], "identity")
        self.assertEqual(reasons["face_underexposed"]["group"], "lighting")
        self.assertEqual(reasons["dress_cropped"]["group"], "composition")
        self.assertEqual(reasons["face_underexposed"]["round_counts"], {"1": 1})

        rounds = {str(item["round"]): item for item in dashboard["repair_rounds"]}
        self.assertEqual(rounds["1"]["attempts"], 2)
        self.assertEqual(rounds["2"]["success_rate"], 1.0)

        repair_modes = {item["repair_mode"]: item for item in dashboard["repair_modes"]}
        self.assertEqual(repair_modes["relight_edit_only"]["attempts"], 1)
        self.assertEqual(repair_modes["relight_edit_only"]["success_rate"], 1.0)

        templates = {item["template_id"]: item for item in dashboard["templates"]}
        self.assertEqual(templates["royal_castle"]["orders"], 2)
        self.assertEqual(templates["royal_castle"]["identity_failed_orders"], 1)
        self.assertEqual(templates["royal_castle"]["lighting_failed_orders"], 1)
        self.assertEqual(templates["solo_royal_castle"]["composition_failed_orders"], 1)


if __name__ == "__main__":
    unittest.main()
