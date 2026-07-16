"""Opt-in real strict-QA runtime probe; no dependency is mocked."""

from __future__ import annotations

import os
from pathlib import Path
import unittest

from app.schemas.qa import StrictQaResponse
from app.services import qa_service


_INFRASTRUCTURE_REASONS = {
    "vision_error",
    "vision_schema_invalid",
    "qa_strict_runtime_disabled",
    "qa_local_checker_unavailable",
    "qa_source_identity_missing",
    "identity_embedding_unavailable",
    "photometric_qa_unavailable",
}


@unittest.skipUnless(
    os.getenv("RUN_QA_RUNTIME_INTEGRATION") == "1",
    "RUN_QA_RUNTIME_INTEGRATION is not enabled",
)
class StrictQaRuntimeIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_runtime_returns_typed_non_infrastructure_verdict(self) -> None:
        candidate_path = Path(os.getenv("QA_RUNTIME_CANDIDATE_PATH", "")).expanduser()
        candidate_url = os.getenv("QA_RUNTIME_CANDIDATE_URL", "").strip()
        source_urls = tuple(
            item.strip()
            for item in os.getenv("QA_RUNTIME_SOURCE_URLS", "").split(",")
            if item.strip()
        )
        missing = []
        if not candidate_path.is_file():
            missing.append("QA_RUNTIME_CANDIDATE_PATH")
        if not candidate_url.startswith("https://"):
            missing.append("QA_RUNTIME_CANDIDATE_URL")
        if not 1 <= len(source_urls) <= 2 or any(
            not item.startswith("https://") for item in source_urls
        ):
            missing.append("QA_RUNTIME_SOURCE_URLS")
        required_flags = {
            "QA_REQUIRE_VISION": qa_service.settings.qa_require_vision,
            "QA_REQUIRE_IDENTITY_VISION": qa_service.settings.qa_require_identity_vision,
            "QA_REQUIRE_IDENTITY_EMBEDDING": qa_service.settings.qa_require_identity_embedding,
            "QA_REQUIRE_PHOTOMETRIC": qa_service.settings.qa_require_photometric,
        }
        missing.extend(name for name, enabled in required_flags.items() if not enabled)
        if missing:
            self.fail("strict QA runtime inputs/config missing: " + ",".join(sorted(set(missing))))

        verdict = await qa_service.strict_output_verdict(
            candidate_path.read_bytes(),
            candidate_url=candidate_url,
            source_image_urls=list(source_urls),
            is_couple=len(source_urls) == 2,
            template_style_context="runtime integration wedding portrait",
        )

        self.assertIsInstance(verdict, StrictQaResponse)
        self.assertFalse(_INFRASTRUCTURE_REASONS.intersection(verdict.reason_codes))


if __name__ == "__main__":
    unittest.main()
