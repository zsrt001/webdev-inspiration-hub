"""Strict Evolink adapter schema and transport tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import sys
import unittest

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import evolink_service as evolink_module
from app.services.evolink_service import (
    EvolinkGenerationRequest,
    EvolinkProviderError,
    EvolinkService,
    EvolinkTaskState,
    parse_evolink_submit_fact,
    parse_evolink_task_fact,
)


class EvolinkProviderTest(unittest.TestCase):
    def test_request_is_exact_https_image_edit_payload(self) -> None:
        request = EvolinkGenerationRequest(
            model="gemini-3.1-flash-image-preview",
            prompt="Identity-safe commercial wedding portrait",
            image_urls=("https://example.test/api/v1/media/grants/token",),
            size="3:4",
            quality="2K",
            model_params={"web_search": False},
        )

        self.assertEqual(
            set(request.provider_payload()),
            {"model", "prompt", "image_urls", "size", "quality", "model_params"},
        )
        with self.assertRaises(ValidationError):
            EvolinkGenerationRequest.model_validate(
                {**request.model_dump(), "image_urls": ("http://example.test/source.jpg",)}
            )
        with self.assertRaises(ValidationError):
            EvolinkGenerationRequest.model_validate({**request.model_dump(), "seed": 1})

    def test_submit_fact_requires_stable_task_id_and_exact_cost_pair(self) -> None:
        fact = parse_evolink_submit_fact(
            {"data": {"task_id": "task_123", "usage": {"cost_minor_units": 17, "currency": "usd"}}}
        )

        self.assertEqual(fact.task_id, "task_123")
        self.assertEqual(fact.cost_minor_units, 17)
        self.assertEqual(fact.currency, "USD")
        with self.assertRaisesRegex(EvolinkProviderError, "evolink_submit_task_id_missing"):
            parse_evolink_submit_fact({"data": {"status": "queued"}})

    def test_task_fact_rejects_success_without_outputs_or_non_https_output(self) -> None:
        with self.assertRaisesRegex(EvolinkProviderError, "evolink_task_outputs_invalid"):
            parse_evolink_task_fact("task_123", {"status": "completed", "results": []})
        with self.assertRaisesRegex(EvolinkProviderError, "evolink_task_schema_invalid"):
            parse_evolink_task_fact(
                "task_123", {"status": "completed", "results": ["http://example.test/out.jpg"]}
            )

    def test_task_fact_classifies_pending_success_and_failure(self) -> None:
        pending = parse_evolink_task_fact("task_123", {"status": "processing"})
        success = parse_evolink_task_fact(
            "task_123", {"status": "completed", "results": ["https://example.test/out.jpg"]}
        )
        failure = parse_evolink_task_fact(
            "task_123", {"status": "failed", "error_code": "MODEL REJECTED"}
        )

        self.assertEqual(pending.state, EvolinkTaskState.RUNNING)
        self.assertEqual(success.state, EvolinkTaskState.SUCCEEDED)
        self.assertEqual(failure.state, EvolinkTaskState.FAILED)
        self.assertEqual(failure.failure_code, "model_rejected")

    def test_runtime_requires_exact_evolink_and_no_fallback_surface(self) -> None:
        service = EvolinkService()
        with patch.multiple(
            evolink_module.settings,
            generation_engine="evolink",
            evolink_api_key="test-key",
            evolink_api_base_url="https://api.evolink.ai",
            evolink_image_model="gemini-3.1-flash-image-preview",
        ):
            service.validate_runtime_requirements(force=True)
        with patch.object(evolink_module.settings, "generation_engine", "EVOLINK"):
            with self.assertRaisesRegex(ValueError, "exactly evolink"):
                service.validate_runtime_requirements(force=True)
        self.assertFalse(hasattr(evolink_module.settings, "evolink_image_fallback_models"))

    def test_prompt_compaction_preserves_identity_subject_and_commercial_gates(self) -> None:
        compacted = EvolinkService.compact_prompt("studio lighting full gown " * 400, subject_count=2)

        self.assertLessEqual(len(compacted), EvolinkService.PROMPT_CHAR_LIMIT)
        self.assertIn("Exactly two primary wedding subjects", compacted)
        self.assertIn("preserve each identity separately", compacted)
        self.assertIn("upright 3:4", compacted)
        self.assertIn("no watermark", compacted)


if __name__ == "__main__":
    unittest.main()
