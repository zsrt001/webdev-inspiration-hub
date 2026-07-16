"""Strict, versioned QA contract tests."""

from __future__ import annotations

import json
import math
from io import BytesIO
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic import ValidationError
from PIL import Image

from app.schemas.qa import (
    QA_CHECKER_VERSION,
    QA_MODEL_VERSION,
    QA_SCHEMA_VERSION,
    QaDecision,
    StrictQaVerdict,
    StrictQaRequest,
    StrictQaResponse,
    failed_qa_response,
)


CHECK_NAMES = (
    "technical",
    "identity",
    "subject",
    "safety",
    "style",
    "composition",
    "exposure",
    "watermark",
)


def _check(*, passed: bool = True, score: float = 1.0) -> dict:
    return {
        "passed": passed,
        "score": score,
        "reason_codes": [] if passed else ["check_failed"],
    }


def _response_payload() -> dict:
    return {
        "schema_version": QA_SCHEMA_VERSION,
        "checker_version": QA_CHECKER_VERSION,
        "model_version": QA_MODEL_VERSION,
        "passed": True,
        "reason_codes": [],
        "checks": {name: _check() for name in CHECK_NAMES},
    }


class StrictQaSchemaTest(unittest.TestCase):
    def test_valid_request_and_response_are_accepted(self) -> None:
        request = StrictQaRequest(
            schema_version=QA_SCHEMA_VERSION,
            candidate_asset_id=uuid4(),
            source_asset_ids=[uuid4()],
            is_couple=False,
        )
        response = StrictQaResponse.model_validate(_response_payload())

        self.assertFalse(request.is_couple)
        self.assertTrue(response.passed)

    def test_string_booleans_and_numeric_strings_are_rejected(self) -> None:
        payload = _response_payload()
        payload["passed"] = "false"
        with self.assertRaises(ValidationError):
            StrictQaResponse.model_validate(payload)

        payload = _response_payload()
        payload["checks"]["technical"]["score"] = "0.9"
        with self.assertRaises(ValidationError):
            StrictQaResponse.model_validate(payload)

    def test_missing_checks_and_unknown_keys_are_rejected(self) -> None:
        payload = _response_payload()
        del payload["checks"]["watermark"]
        with self.assertRaises(ValidationError):
            StrictQaResponse.model_validate(payload)

        payload = _response_payload()
        payload["checks"]["technical"]["unexpected"] = True
        with self.assertRaises(ValidationError):
            StrictQaResponse.model_validate(payload)

    def test_malformed_json_score_overflow_and_nan_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            StrictQaResponse.model_validate_json('{"passed":')

        payload = _response_payload()
        payload["checks"]["technical"]["score"] = 1.01
        with self.assertRaises(ValidationError):
            StrictQaResponse.model_validate(payload)

        payload = _response_payload()
        payload["checks"]["technical"]["score"] = math.nan
        with self.assertRaises(ValidationError):
            StrictQaResponse.model_validate(payload)

    def test_schema_checker_and_model_version_mismatch_are_rejected(self) -> None:
        for field in ("schema_version", "checker_version", "model_version"):
            payload = _response_payload()
            payload[field] = "unsupported-version"
            with self.subTest(field=field), self.assertRaises(ValidationError):
                StrictQaResponse.model_validate(payload)

    def test_top_level_pass_must_equal_all_mandatory_checks(self) -> None:
        payload = _response_payload()
        payload["checks"]["identity"] = _check(passed=False, score=0.0)
        with self.assertRaises(ValidationError):
            StrictQaResponse.model_validate(payload)

    def test_typed_dependency_failure_is_never_pass(self) -> None:
        response = failed_qa_response("vision_dependency_unavailable")
        encoded = json.loads(response.model_dump_json())

        self.assertFalse(response.passed)
        self.assertEqual(response.reason_codes, ["vision_dependency_unavailable"])
        self.assertTrue(all(not check["passed"] for check in encoded["checks"].values()))

    def test_internal_decision_contract_is_strict_and_complete(self) -> None:
        response = StrictQaResponse.model_validate(_response_payload())
        verdict = StrictQaVerdict.from_response(response, decision=QaDecision.PASS)

        self.assertEqual(verdict.decision, QaDecision.PASS)
        self.assertEqual(verdict.reasons, ())
        self.assertEqual(verdict.scores.technical, 1.0)
        payload = verdict.model_dump(mode="json")
        payload["decision"] = "pass"
        with self.assertRaises(ValidationError):
            StrictQaVerdict.model_validate(payload)
        del payload["scores"]
        with self.assertRaises(ValidationError):
            StrictQaVerdict.model_validate(payload)


class StrictQaRuntimeAdapterTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _image_bytes() -> bytes:
        output = BytesIO()
        Image.new("RGB", (900, 1200), (120, 100, 90)).save(output, format="JPEG")
        return output.getvalue()

    async def test_identity_dependency_failure_overrides_a_vision_pass(self) -> None:
        from app.services import qa_service

        with (
            patch.object(qa_service.settings, "qa_require_vision", True),
            patch.object(qa_service.settings, "qa_require_identity_embedding", True),
            patch.object(qa_service.settings, "qa_require_photometric", True),
            patch.object(qa_service, "run_local_qa_rules", return_value=([], {})),
            patch.object(
                qa_service.identity_embedding_service,
                "verify_identity_similarity",
                AsyncMock(return_value={"passed": False, "reasons": ["identity_mismatch"]}),
            ),
            patch.object(
                qa_service.photometric_qa_service,
                "verify_lighting",
                AsyncMock(return_value={"passed": True, "reasons": []}),
            ),
            patch.object(
                qa_service.llm_service,
                "verify_generated_image_quality",
                AsyncMock(
                    return_value={
                        "passed": True,
                        "reasons": [],
                        "qa_contract": _response_payload(),
                    }
                ),
            ),
        ):
            verdict = await qa_service.strict_output_verdict(
                self._image_bytes(),
                candidate_url="https://grant.example.test/candidate",
                source_image_urls=["https://grant.example.test/source"],
                is_couple=False,
            )

        self.assertFalse(verdict.passed)
        self.assertFalse(verdict.checks.identity.passed)
        self.assertIn("identity_mismatch", verdict.reason_codes)

    async def test_missing_strict_vision_contract_is_typed_failure(self) -> None:
        from app.services import qa_service

        with (
            patch.object(qa_service.settings, "qa_require_vision", True),
            patch.object(qa_service.settings, "qa_require_identity_embedding", True),
            patch.object(qa_service.settings, "qa_require_photometric", True),
            patch.object(qa_service, "run_local_qa_rules", return_value=([], {})),
            patch.object(
                qa_service.identity_embedding_service,
                "verify_identity_similarity",
                AsyncMock(return_value={"passed": True, "reasons": []}),
            ),
            patch.object(
                qa_service.photometric_qa_service,
                "verify_lighting",
                AsyncMock(return_value={"passed": True, "reasons": []}),
            ),
            patch.object(
                qa_service.llm_service,
                "verify_generated_image_quality",
                AsyncMock(return_value={"passed": False, "reasons": ["vision_error"]}),
            ),
        ):
            verdict = await qa_service.strict_output_verdict(
                self._image_bytes(),
                candidate_url="https://grant.example.test/candidate",
                source_image_urls=["https://grant.example.test/source"],
                is_couple=False,
            )

        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason_codes, ["vision_error"])


if __name__ == "__main__":
    unittest.main()
