"""Fail-closed contracts for the production GOOGLE_AUTH_ONLY release type."""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _path_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GoogleAuthOnlyActivationTest(unittest.IsolatedAsyncioTestCase):
    def test_0021_adds_only_the_production_google_auth_activation_kind(self) -> None:
        source = (
            ROOT / "backend/alembic/versions/20260710_0021_google_auth_only_activation.py"
        ).read_text(encoding="utf-8")
        self.assertIn('revision = "20260710_0021"', source)
        self.assertIn('down_revision = "20260710_0020"', source)
        self.assertIn("GOOGLE_AUTH_ONLY", source)
        environment_expression = source.split("_ENVIRONMENT_KINDS_WITH_GOOGLE_AUTH_ONLY = (", 1)[1].split(")\n_ORIGINAL_KINDS", 1)[0]
        self.assertIn("environment = 'production'", environment_expression)
        self.assertNotIn(
            "GOOGLE_AUTH_ONLY",
            environment_expression.split("environment = 'preview'", 1)[1].split("OR", 1)[0],
        )

    async def test_service_rejects_every_non_google_capability_for_google_auth_only(self) -> None:
        flags = importlib.import_module("app.core.feature_flags")
        service = importlib.import_module("app.services.feature_flag_service")
        activation = SimpleNamespace(
            id=uuid4(),
            environment="production",
            kind="GOOGLE_AUTH_ONLY",
            phase="ACCEPTANCE_READY",
            runtime_bundle_id="rtb_" + "a" * 64,
            api_deployment_id="dpl_target",
        )
        result = SimpleNamespace(scalar_one_or_none=lambda: activation)
        db = AsyncMock()
        db.execute.return_value = result
        accepted = await service._validate_activation_for_state(
            db,
            activation_id=activation.id,
            environment="production",
            capability=flags.Capability.GOOGLE_AUTH,
            deployment_id="dpl_target",
            runtime_bundle_id="rtb_" + "a" * 64,
        )
        self.assertIs(accepted, activation)
        for capability in flags.Capability:
            if capability is flags.Capability.GOOGLE_AUTH:
                continue
            with self.subTest(capability=capability.value), self.assertRaisesRegex(
                ValueError, "cannot authorize"
            ):
                await service._validate_activation_for_state(
                    db,
                    activation_id=activation.id,
                    environment="production",
                    capability=capability,
                    deployment_id="dpl_target",
                    runtime_bundle_id="rtb_" + "a" * 64,
                )

    def test_google_auth_only_plan_cannot_select_a_commercial_phase(self) -> None:
        module = _path_module(
            "google_auth_only_plan",
            ROOT / "scripts/release/apply_activation_plan.py",
        )
        plan = json.loads((ROOT / "release/activation-plan.json").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "only Google auth"):
            module.apply_phase(
                "postgresql://unused",
                phase="formal-cohort",
                plan=plan,
                approval="approved",
                kind="GOOGLE_AUTH_ONLY",
                deployment_id="dpl_target",
                source_sha="a" * 40,
                binding_report=None,
            )

    def test_runtime_registration_is_exactly_bound_to_schema_0021(self) -> None:
        module = _path_module(
            "manage_google_auth_only",
            ROOT / "scripts/release/manage_google_auth_only_activation.py",
        )
        source_sha = "a" * 40
        coordinates = module.validate_runtime_report(
            {
                "source_sha": source_sha,
                "runtime_bundle_id": "rtb_" + "b" * 64,
                "deployment_id": "dpl_target",
                "release_role": "COMMERCIAL_7A",
                "runtime_environment": "production",
                "schema_revision": "20260710_0021",
            },
            source_sha=source_sha,
            base_url="https://www.vowpic.com",
        )
        manifest = module.activation_manifest(coordinates)
        self.assertEqual(manifest["kind"], "GOOGLE_AUTH_ONLY")
        self.assertEqual(manifest["schema_revision"], "20260710_0021")
        with self.assertRaisesRegex(ValueError, "schema"):
            module.validate_runtime_report(
                {**coordinates, "schema_revision": "20260710_0020"},
                source_sha=source_sha,
                base_url="https://www.vowpic.com",
            )

    def test_workflow_has_no_generation_payment_or_commercial_activation_path(self) -> None:
        path = ROOT / ".github/workflows/production-google-auth-only.yml"
        source = path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(source)
        self.assertIn("workflow_dispatch", workflow[True])
        self.assertEqual(workflow["concurrency"]["group"], "vowpic-production-release")
        self.assertEqual(workflow["jobs"]["google-auth-only"]["environment"], "production")
        for required in (
            "--kind GOOGLE_AUTH_ONLY",
            "--phase google-auth-only",
            "--phase emergency-off",
            "npm --prefix frontend run playwright:install",
            "google-only-final-state.json",
            "manage_google_auth_only_activation.py complete",
        ):
            self.assertIn(required, source)
        self.assertLess(
            source.index("npm --prefix frontend run playwright:install"),
            source.index("npm --prefix frontend run test:e2e"),
        )
        for forbidden in (
            "images/generations",
            "orders/create",
            "payments/checkout",
            "CREEM_",
            "EVOLINK_",
            "--phase staged-user-cohort",
            "--phase formal-cohort",
            "vercel deploy",
            "vercel promote",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
