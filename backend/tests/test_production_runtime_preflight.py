"""Secret-safe preflight contracts for the COMMERCIAL_7A runtime."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "validate_production_runtime_environment.py"
SOURCE_SHA = "a" * 40


def _module():
    spec = importlib.util.spec_from_file_location(
        "validate_production_runtime_environment_test",
        SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_environment() -> dict[str, str]:
    return {
        "RUNTIME_ENVIRONMENT": "production",
        "RELEASE_ROLE": "COMMERCIAL_7A",
        "TASK_EXECUTION_MODE": "backend",
        "STORAGE_PROVIDER": "vercel",
        "GENERATION_ENGINE": "evolink",
        "LLM_PROVIDER": "wenwen",
        "PAYMENT_PROVIDER": "creem",
        "RATE_LIMIT_ENABLED": "true",
        "QA_REQUIRE_VISION": "true",
        "GATEKEEPER_ALLOW_WITHOUT_PILLOW": "false",
        "QA_ALLOW_WITHOUT_PILLOW": "false",
        "ALLOW_MEMORY_FALLBACK": "false",
        "DATABASE_URL": "postgresql://app_runtime:secret@db.example.com:5432/vowpic",
        "CONTROL_PLANE_DATABASE_URL": (
            "postgresql://control_writer:secret@db.example.com:5432/vowpic"
        ),
        "REDIS_URL": "rediss://default:redis-secret@redis.example.com:6379/0",
        "SECRET_KEY": "runtime-secret-" + ("s" * 32),
        "ACCEPTANCE_IDENTITY_HMAC_KEY": "identity-secret-" + ("i" * 32),
        "PRIVATE_BLOB_READ_WRITE_TOKEN": "blob-secret-value",
        "EVOLINK_API_KEY": "evolink-secret-value",
        "EVOLINK_API_BASE_URL": "https://api.evolink.ai",
        "EVOLINK_IMAGE_MODEL": "gemini-3.1-flash-image-preview",
        "WENWEN_VISION_API_KEY": "wenwen-secret-value",
        "WENWEN_API_BASE_URL": "https://breakout.wenwen-ai.com/v1",
        "CREEM_API_KEY": "creem-secret-value",
        "CREEM_WEBHOOK_SECRET": "webhook-secret-value",
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_ANON_KEY": "supabase-publishable-value",
        "SUPPORT_EMAIL": "support@example.com",
        "SUPPORT_MONITORED": "true",
        "CLEANUP_CRON_TOKEN": "cleanup-secret-value",
        "FRONTEND_BASE_URL": "https://www.vowpic.com",
        "WEBHOOK_BASE_URL": "https://www.vowpic.com",
        "CORS_ALLOW_ORIGINS": "https://www.vowpic.com",
        "PRODUCTION_CANARY_MAX_COST_MINOR": "500",
    }


class ProductionRuntimePreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _module()

    def test_complete_environment_passes_without_legacy_product_ids(self) -> None:
        environment = _valid_environment()

        errors = self.module.validate_environment(environment, source_sha=SOURCE_SHA)

        self.assertEqual(errors, [])
        self.assertFalse(any(name.startswith("CREEM_PRODUCT_") for name in environment))
        self.assertFalse(
            any(name.startswith("CREEM_SUBSCRIPTION_") for name in environment)
        )

    def test_commercial_oauth_is_required_but_redis_does_not_gate_generation(self) -> None:
        environment = _valid_environment()
        environment["SUPABASE_ANON_KEY"] = ""
        environment["REDIS_URL"] = "redis://localhost:6379/0"

        errors = self.module.validate_environment(environment, source_sha=SOURCE_SHA)

        self.assertIn(
            "SUPABASE_URL and SUPABASE_ANON_KEY are required for commercial OAuth",
            errors,
        )
        self.assertFalse(any("REDIS_URL" in error for error in errors))

    def test_cost_cap_and_source_sha_fail_closed(self) -> None:
        environment = _valid_environment()
        environment["PRODUCTION_CANARY_MAX_COST_MINOR"] = "0"

        errors = self.module.validate_environment(environment, source_sha="not-a-sha")

        self.assertIn(
            "PRODUCTION_CANARY_MAX_COST_MINOR must be a positive integer",
            errors,
        )
        self.assertIn(
            "SOURCE_SHA must be an exact 40-character lowercase commit",
            errors,
        )
        uppercase_errors = self.module.validate_environment(
            _valid_environment(),
            source_sha="A" * 40,
        )
        self.assertIn(
            "SOURCE_SHA must be an exact 40-character lowercase commit",
            uppercase_errors,
        )

    def test_cli_output_never_contains_secret_values(self) -> None:
        environment = _valid_environment()
        output = StringIO()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(sys, "argv", [str(SCRIPT), "--source-sha", SOURCE_SHA]),
            redirect_stdout(output),
        ):
            exit_code = self.module.main()

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        serialized = output.getvalue()
        protected_names = {
            "DATABASE_URL",
            "CONTROL_PLANE_DATABASE_URL",
            "REDIS_URL",
        }
        for name, value in environment.items():
            if (
                name in protected_names
                or "SECRET" in name
                or "KEY" in name
                or "TOKEN" in name
            ):
                self.assertNotIn(value, serialized)


if __name__ == "__main__":
    unittest.main()
