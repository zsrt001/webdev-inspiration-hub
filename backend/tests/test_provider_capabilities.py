"""Provider policy must describe implemented behavior without source-bound proof."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_provider_capabilities.py"
CAPABILITIES = ROOT / "release" / "provider-capabilities.json"


def _module():
    spec = importlib.util.spec_from_file_location("verify_provider_capabilities", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProviderCapabilitiesTest(unittest.TestCase):
    def test_current_capabilities_match_real_adapters(self) -> None:
        document = json.loads(CAPABILITIES.read_text(encoding="utf-8"))
        result = _module().validate_provider_capabilities(document)
        self.assertEqual(set(result), {"evolink", "creem"})
        self.assertEqual(
            document["providers"]["evolink"]["ambiguous_submission_policy"],
            "hold_without_resubmit",
        )
        self.assertEqual(
            document["providers"]["evolink"]["submit_endpoint"],
            "/v1/images/generations",
        )
        self.assertEqual(
            document["providers"]["evolink"]["credit_endpoint"],
            "/v1/credits",
        )
        self.assertEqual(
            document["providers"]["evolink"]["acceptance_minimum_credits"],
            "10",
        )
        self.assertIn(
            "https://evolink.ai/docs/en/api-manual/image-series/"
            "nanobanana/nanobanana-pro-image-generate",
            document["providers"]["evolink"]["official_sources"],
        )
        self.assertIn(
            "https://docs.evolink.ai/en/api-manual/account-management/get-credits",
            document["providers"]["evolink"]["official_sources"],
        )
        self.assertNotIn(
            "gpt-image-2",
            " ".join(document["providers"]["evolink"]["official_sources"]),
        )
        serialized = json.dumps(document, sort_keys=True)
        self.assertNotIn("tested_source_sha", serialized)
        self.assertNotIn("idempotency_key", serialized)

    def test_policy_drift_fails_closed(self) -> None:
        document = json.loads(CAPABILITIES.read_text(encoding="utf-8"))
        document["providers"]["evolink"]["ambiguous_submission_policy"] = "retry"
        with self.assertRaisesRegex(ValueError, "ambiguous_submission_policy"):
            _module().validate_provider_capabilities(document)


if __name__ == "__main__":
    unittest.main()
