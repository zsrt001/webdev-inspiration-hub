"""Six-case quality evidence requires separate review and exact DB lineage."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release._acceptance_evidence import seal_collected_input
from scripts.release._acceptance_quality_facts import collect_quality


TMP = ROOT / ".tmp" / "acceptance-quality-collector"
NODE = shutil.which("node") or "node"
KEY = b"quality-collector-test-key-at-least-32-bytes"
CASES = json.loads((ROOT / "release/quality-cases.json").read_text(encoding="utf-8"))
RUBRIC = json.loads((ROOT / "release/quality-rubric.json").read_text(encoding="utf-8"))


class _Cursor:
    def __init__(
        self,
        *,
        wrong_candidate: bool = False,
        wrong_review_asset: bool = False,
    ):
        self.current: list[dict] = []
        self.wrong_candidate = wrong_candidate
        self.wrong_review_asset = wrong_review_asset

    def execute(self, _query: str, params: tuple) -> None:
        review_asset_id, order_id, user_id, candidate_id = params
        self.current = [{
            "order_id": order_id,
            "user_id": user_id,
            "order_status": "READY",
            "job_id": str(order_id).replace("order", "job"),
            "job_status": "FINISHED",
            "selected_candidate_id": (
                "different-candidate" if self.wrong_candidate else candidate_id
            ),
            "candidate_status": "ACTIVE",
            "candidate_role": "candidate",
            "review_asset_id": (
                "different-final-master"
                if self.wrong_review_asset
                else review_asset_id
            ),
            "review_asset_status": "ACTIVE",
            "review_asset_role": "final_master",
            "initial_candidate_count": 1,
            "repair_candidate_count": 1,
        }]

    def fetchall(self) -> list[dict]:
        return self.current


def _binding() -> dict:
    return {
        "source_sha": "a" * 40,
        "runtime_bundle_id": "rtb_" + "b" * 64,
        "deployment_id": "dpl-quality",
        "manifest_sha256": "c" * 64,
        "user_subject_hmac_sha256": "d" * 64,
    }


def _browser() -> dict:
    return {
        **_binding(),
        "user_id": "quality-user",
        "cases": [
            {"id": item["id"], "order_id": f"{item['id']}-order"}
            for item in CASES["cases"]
        ],
    }


def _review() -> dict:
    return {
        **_binding(),
        "review_complete": True,
        "produced_at": "2026-07-19T00:00:00Z",
        "cases": [
            {
                "id": item["id"],
                "order_id": f"{item['id']}-order",
                "job_id": f"{item['id']}-job",
                "selected_candidate_id": f"{item['id']}-candidate",
                "review_asset_id": f"{item['id']}-final-master",
                "reviewer_ref": f"reviewer-{index}",
                "scores": {
                    "identity": 5,
                    "composition": 4,
                    "attire_style": 4,
                    "naturalness_exposure": 5,
                },
                "hard_defects": [],
                "reviewed_at": "2026-07-19T00:00:00Z",
            }
            for index, item in enumerate(CASES["cases"], start=1)
        ],
    }


class AcceptanceQualityCollectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        if TMP.parent.exists() and not any(TMP.parent.iterdir()):
            TMP.parent.rmdir()

    def test_six_db_bound_human_reviews_are_accepted_cross_runtime(self) -> None:
        payload, facts = collect_quality(
            _Cursor(),
            browser=_browser(),
            human_review=_review(),
            cases_contract=CASES,
            rubric=RUBRIC,
        )
        sealed = seal_collected_input(
            payload,
            phase="quality",
            browser_report_sha256="f" * 64,
            database_facts=facts,
            key=KEY,
        )
        input_path = TMP / "quality.input.json"
        output_path = TMP / "quality.json"
        input_path.write_text(json.dumps(sealed, sort_keys=True), encoding="utf-8")
        completed = subprocess.run(
            [
                NODE,
                str(ROOT / "scripts/release/run_quality_acceptance.mjs"),
                "--base-url",
                "https://staged.example",
                "--cases",
                str(ROOT / "release/quality-cases.json"),
                "--rubric",
                str(ROOT / "release/quality-rubric.json"),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "RUNNER_TEMP": str(TMP),
                "ACCEPTANCE_EVIDENCE_SIGNING_KEY": KEY.decode("utf-8"),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(len(report["cases"]), 6)
        self.assertTrue(all(item["passed"] for item in report["cases"]))

    def test_signed_hard_defect_still_fails_the_quality_gate(self) -> None:
        review = _review()
        review["cases"][0]["hard_defects"] = ["identity"]
        payload, facts = collect_quality(
            _Cursor(),
            browser=_browser(),
            human_review=review,
            cases_contract=CASES,
            rubric=RUBRIC,
        )
        self.assertFalse(payload["cases"][0]["passed"])
        self.assertEqual(facts["cases"][0]["reviewed_at"], "2026-07-19T00:00:00Z")

    def test_review_candidate_must_equal_the_passed_database_candidate(self) -> None:
        with self.assertRaisesRegex(ValueError, "generation lineage"):
            collect_quality(
                _Cursor(wrong_candidate=True),
                browser=_browser(),
                human_review=_review(),
                cases_contract=CASES,
                rubric=RUBRIC,
            )

    def test_review_asset_must_be_the_candidates_active_final_master(self) -> None:
        with self.assertRaisesRegex(ValueError, "generation lineage"):
            collect_quality(
                _Cursor(wrong_review_asset=True),
                browser=_browser(),
                human_review=_review(),
                cases_contract=CASES,
                rubric=RUBRIC,
            )


if __name__ == "__main__":
    unittest.main()
