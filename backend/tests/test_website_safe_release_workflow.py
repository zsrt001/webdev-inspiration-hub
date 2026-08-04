"""Static safety contract for the website-only Production release."""

from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "website-safe-release.yml"


class WebsiteSafeReleaseWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.document = yaml.safe_load(cls.source)

    def test_is_manual_serialized_and_production_protected(self) -> None:
        self.assertIn("workflow_dispatch", self.document[True])
        self.assertEqual(
            self.document["concurrency"]["group"],
            "vowpic-production-release",
        )
        job = self.document["jobs"]["website-safe-release"]
        self.assertEqual(job["environment"], "production")
        self.assertEqual(job["timeout-minutes"], 45)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', self.source)
        self.assertIn('test "$GITHUB_SHA" = "$SOURCE_SHA"', self.source)
        self.assertIn('item.get("name") == "quality-gate"', self.source)

    def test_requires_exact_schema_and_all_seven_flags_off(self) -> None:
        for capability in (
            "google_auth",
            "authenticated_upload",
            "generation",
            "credit_pack_checkout",
            "subscription_billing",
            "private_download",
            "partner_invite",
        ):
            self.assertIn(f'"{capability}"', self.source)
        self.assertIn('revisions != [("20260710_0020",)]', self.source)
        self.assertIn('row[1] != "OFF"', self.source)
        self.assertIn("Object.values(capabilities).some(Boolean)", self.source)

    def test_deploys_unaliased_candidate_then_promotes_once_with_rollback(self) -> None:
        self.assertEqual(self.source.count(" deploy --prebuilt --prod --skip-domain "), 1)
        self.assertEqual(self.source.count('"$VERCEL_CLI" promote "$CANDIDATE_URL"'), 1)
        self.assertEqual(self.source.count('"$VERCEL_CLI" promote "$PREVIOUS_DEPLOYMENT_URL"'), 1)
        self.assertIn("Capture the current formal deployment for bounded rollback", self.source)
        self.assertIn("Verify the unaliased candidate without generation or payment", self.source)
        self.assertIn("roll back on any formal verification failure", self.source)
        self.assertIn(
            "runtime.deployment_id !== process.env.PREVIOUS_DEPLOYMENT_ID",
            self.source,
        )

    def test_never_runs_generation_payment_or_database_mutation(self) -> None:
        forbidden = (
            "images/generations",
            "orders/create",
            "payments/checkout",
            "CREEM_CARD",
            "alembic upgrade",
            "data-migration.yml",
            "set_capability_state",
            "configure_evolink_callback_origin.py",
            "register_bundle.py advance",
            "RAILWAY_",
            "redis://",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, self.source)

    def test_runtime_and_formal_evidence_are_exactly_bound(self) -> None:
        self.assertIn("--release-role \"$RELEASE_ROLE\"", self.source)
        self.assertIn('--env "RELEASE_ROLE=$RELEASE_ROLE"', self.source)
        self.assertIn('--env "VERCEL_GIT_COMMIT_SHA=$SOURCE_SHA"', self.source)
        self.assertIn('runtime.release_role !== \'COMMERCIAL_7A\'', self.source)
        self.assertIn(
            "runtime.deployment_id !== process.env.CANDIDATE_DEPLOYMENT_ID",
            self.source,
        )
        self.assertIn("Persist sanitized website-release evidence", self.source)


if __name__ == "__main__":
    unittest.main()
