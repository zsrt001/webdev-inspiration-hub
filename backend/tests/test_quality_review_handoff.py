"""The quality handoff must bind one human review to one exact staged run."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from scripts.release.quality_review_handoff import (
    build_draft_template,
    build_request,
    build_signed_review,
)


ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "release/quality-cases.json").read_text(encoding="utf-8"))
RUBRIC = json.loads((ROOT / "release/quality-rubric.json").read_text(encoding="utf-8"))
ACCEPTANCE_KEY = b"quality-request-acceptance-key-32-bytes"
REVIEW_KEY = b"independent-quality-review-key-32-bytes"
NOW = datetime(2026, 7, 19, 1, tzinfo=timezone.utc)
REVIEW_TIME = datetime(2026, 7, 19, 2, tzinfo=timezone.utc)


def _browser() -> dict:
    return {
        "source_sha": "a" * 40,
        "runtime_bundle_id": "rtb_" + "b" * 64,
        "deployment_id": "dpl-quality",
        "manifest_sha256": "c" * 64,
        "user_subject_hmac_sha256": "d" * 64,
        "user_id": "quality-user",
        "cases": [
            {"id": item["id"], "order_id": f"{item['id']}-order"}
            for item in CASES["cases"]
        ],
    }


def _rows() -> list[dict]:
    return [
        {
            "order_id": f"{item['id']}-order",
            "user_id": "quality-user",
            "order_status": "READY",
            "job_id": f"{item['id']}-job",
            "job_status": "FINISHED",
            "selected_candidate_id": f"{item['id']}-candidate",
            "candidate_status": "ACTIVE",
            "candidate_role": "candidate",
            "review_asset_id": f"{item['id']}-final-master",
            "review_asset_status": "ACTIVE",
            "review_asset_role": "final_master",
        }
        for item in CASES["cases"]
    ]


class QualityReviewHandoffTest(unittest.TestCase):
    def test_request_draft_and_review_preserve_exact_coordinates(self) -> None:
        request = build_request(
            browser=_browser(),
            cases_contract=CASES,
            rows=_rows(),
            key=ACCEPTANCE_KEY,
            produced_at=NOW,
        )
        draft = build_draft_template(request, RUBRIC)
        draft["review_complete"] = True
        for case in draft["cases"]:
            case["reviewer_ref"] = "reviewer-independent-01"
            case["scores"] = {
                "identity": 5,
                "composition": 4,
                "attire_style": 4,
                "naturalness_exposure": 5,
            }
            case["reviewed_at"] = "2026-07-19T01:30:00Z"
        review = build_signed_review(
            request=request,
            draft=draft,
            rubric=RUBRIC,
            acceptance_key=ACCEPTANCE_KEY,
            review_key=REVIEW_KEY,
            produced_at=REVIEW_TIME,
        )
        self.assertTrue(review["review_complete"])
        self.assertEqual(
            review["cases"][0]["selected_candidate_id"],
            request["cases"][0]["selected_candidate_id"],
        )
        self.assertEqual(
            review["cases"][0]["review_asset_id"],
            request["cases"][0]["review_asset_id"],
        )
        self.assertRegex(review["signature"], r"^hmac-sha256:[0-9a-f]{64}$")

    def test_review_cannot_be_reused_for_a_different_request(self) -> None:
        request = build_request(
            browser=_browser(),
            cases_contract=CASES,
            rows=_rows(),
            key=ACCEPTANCE_KEY,
            produced_at=NOW,
        )
        draft = build_draft_template(request, RUBRIC)
        draft["review_complete"] = True
        for case in draft["cases"]:
            case["reviewer_ref"] = "reviewer-independent-01"
            case["scores"] = {name: 4 for name in RUBRIC["dimensions"]}
            case["reviewed_at"] = "2026-07-19T00:30:00Z"
        other_browser = _browser()
        other_browser["cases"][0]["order_id"] = "different-order"
        other_rows = _rows()
        other_rows[0]["order_id"] = "different-order"
        other = build_request(
            browser=other_browser,
            cases_contract=CASES,
            rows=other_rows,
            key=ACCEPTANCE_KEY,
            produced_at=NOW,
        )
        with self.assertRaisesRegex(ValueError, "misbound"):
            build_signed_review(
                request=other,
                draft=draft,
                rubric=RUBRIC,
                acceptance_key=ACCEPTANCE_KEY,
                review_key=REVIEW_KEY,
                produced_at=REVIEW_TIME,
            )

    def test_zero_placeholder_scores_cannot_be_signed(self) -> None:
        request = build_request(
            browser=_browser(),
            cases_contract=CASES,
            rows=_rows(),
            key=ACCEPTANCE_KEY,
            produced_at=NOW,
        )
        draft = build_draft_template(request, RUBRIC)
        draft["review_complete"] = True
        with self.assertRaisesRegex(ValueError, "invalid"):
            build_signed_review(
                request=request,
                draft=draft,
                rubric=RUBRIC,
                acceptance_key=ACCEPTANCE_KEY,
                review_key=REVIEW_KEY,
                produced_at=REVIEW_TIME,
            )

    def test_expired_request_cannot_be_signed(self) -> None:
        request = build_request(
            browser=_browser(),
            cases_contract=CASES,
            rows=_rows(),
            key=ACCEPTANCE_KEY,
            produced_at=NOW,
        )
        draft = build_draft_template(request, RUBRIC)
        draft["review_complete"] = True
        with self.assertRaisesRegex(ValueError, "expired"):
            build_signed_review(
                request=request,
                draft=draft,
                rubric=RUBRIC,
                acceptance_key=ACCEPTANCE_KEY,
                review_key=REVIEW_KEY,
                produced_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            )

    def test_duplicate_passed_candidate_rows_cannot_prepare_a_request(self) -> None:
        rows = _rows()
        rows.append(dict(rows[0]))
        with self.assertRaisesRegex(ValueError, "browser cases"):
            build_request(
                browser=_browser(),
                cases_contract=CASES,
                rows=rows,
                key=ACCEPTANCE_KEY,
                produced_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
