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

    def test_installs_checksum_pinned_uv_before_local_vercel_build(self) -> None:
        steps = self.document["jobs"]["website-safe-release"]["steps"]
        uv_step = next(
            step
            for step in steps
            if step.get("name") == "Install the Vercel-compatible uv build tool"
        )
        self.assertEqual(
            uv_step["uses"],
            "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990",
        )
        self.assertEqual(uv_step["with"]["version"], "0.10.11")
        self.assertEqual(
            uv_step["with"]["checksum"],
            "5a360b0de092ddf4131f5313d0411b48c4e95e8107e40c3f8f2e9fcb636b3583",
        )
        self.assertFalse(uv_step["with"]["enable-cache"])
        self.assertFalse(uv_step["with"]["download-from-astral-mirror"])
        self.assertLess(
            self.source.index("Install the Vercel-compatible uv build tool"),
            self.source.index("Build once and deploy unaliased bridge and website candidates"),
        )

    def test_requires_supported_source_exact_target_and_all_seven_flags_off(self) -> None:
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
        self.assertIn('"20260712_0014", "20260710_0020", "20260710_0021"', self.source)
        self.assertIn('revisions != [("20260710_0021",)]', self.source)
        self.assertIn('row[1] != "OFF"', self.source)
        self.assertIn("Object.values(capabilities).some(Boolean)", self.source)

    def test_schema_transition_is_exact_minimum_privilege_and_forward_only(self) -> None:
        self.assertEqual(
            self.source.count("python -m alembic -c alembic.ini upgrade 20260710_0021"),
            1,
        )
        self.assertIn('if [[ "$SCHEMA_BEFORE" != "20260710_0021" ]]', self.source)
        self.assertIn("production_database_login_proof.py", self.source)
        self.assertIn('--expected-schema "$SCHEMA_BEFORE"', self.source)
        self.assertIn("--expected-schema 20260710_0021", self.source)
        self.assertIn("lock_timeout=5s", self.source)
        self.assertIn("statement_timeout=1800s", self.source)
        self.assertIn("rollback baseline readiness is not green after schema transition", self.source)
        self.assertNotIn("alembic downgrade", self.source)

    def test_proves_and_injects_the_least_privilege_runtime_database_logins(self) -> None:
        self.assertIn(
            "PRODUCTION_RUNTIME_DATABASE_URL: "
            "${{ secrets.PRODUCTION_RUNTIME_DATABASE_URL }}",
            self.source,
        )
        self.assertIn(
            "PRODUCTION_CONTROL_PLANE_DATABASE_URL: "
            "${{ secrets.PRODUCTION_CONTROL_PLANE_DATABASE_URL }}",
            self.source,
        )
        self.assertEqual(
            self.source.count("verify_production_database_credentials.py"),
            1,
        )
        self.assertEqual(
            self.source.count(
                '--env "DATABASE_URL=$PRODUCTION_RUNTIME_DATABASE_URL"'
            ),
            2,
        )
        self.assertEqual(
            self.source.count(
                '--env "CONTROL_PLANE_DATABASE_URL=$PRODUCTION_CONTROL_PLANE_DATABASE_URL"'
            ),
            2,
        )
        self.assertNotIn(
            '--env "DATABASE_URL=$PRODUCTION_MIGRATION_DATABASE_URL"',
            self.source,
        )

    def test_injects_the_same_protected_acceptance_identity_key_into_both_targets(self) -> None:
        self.assertIn(
            "ACCEPTANCE_IDENTITY_HMAC_KEY: "
            "${{ secrets.ACCEPTANCE_IDENTITY_HMAC_KEY }}",
            self.source,
        )
        self.assertEqual(
            self.source.count(
                '--env "ACCEPTANCE_IDENTITY_HMAC_KEY=$ACCEPTANCE_IDENTITY_HMAC_KEY"'
            ),
            2,
        )

    def test_injects_the_approved_monitored_support_channel_into_the_candidate(self) -> None:
        job_env = self.document["jobs"]["website-safe-release"]["env"]
        self.assertEqual(
            job_env["PRODUCTION_SUPPORT_EMAIL"],
            "${{ vars.PRODUCTION_SUPPORT_EMAIL }}",
        )
        self.assertEqual(
            job_env["PRODUCTION_SUPPORT_URL"],
            "${{ vars.PRODUCTION_SUPPORT_URL }}",
        )
        self.assertEqual(
            job_env["PRODUCTION_SUPPORT_MONITORED"],
            "${{ vars.PRODUCTION_SUPPORT_MONITORED }}",
        )
        self.assertIn(
            '[[ -n "$PRODUCTION_SUPPORT_EMAIL" || -n "$PRODUCTION_SUPPORT_URL" ]]',
            self.source,
        )
        self.assertIn(
            'test "$PRODUCTION_SUPPORT_MONITORED" = "true"',
            self.source,
        )
        for binding in (
            '--env "SUPPORT_EMAIL=$PRODUCTION_SUPPORT_EMAIL"',
            '--env "SUPPORT_URL=$PRODUCTION_SUPPORT_URL"',
            '--env "SUPPORT_MONITORED=$PRODUCTION_SUPPORT_MONITORED"',
        ):
            with self.subTest(binding=binding):
                self.assertEqual(self.source.count(binding), 1)

    def test_deploys_unaliased_candidate_then_promotes_once_with_rollback(self) -> None:
        self.assertEqual(self.source.count(" deploy --prebuilt --prod --skip-domain "), 2)
        self.assertEqual(self.source.count('"$VERCEL_CLI" promote "$BRIDGE_URL"'), 1)
        self.assertEqual(self.source.count('"$VERCEL_CLI" promote "$CANDIDATE_URL"'), 1)
        self.assertEqual(self.source.count('"$VERCEL_CLI" rollback "$PREVIOUS_DEPLOYMENT_ID"'), 1)
        self.assertEqual(self.source.count('"$VERCEL_CLI" rollback "$ORIGINAL_DEPLOYMENT_ID"'), 1)
        self.assertNotIn("PREVIOUS_DEPLOYMENT_URL", self.source)
        self.assertNotIn("ORIGINAL_DEPLOYMENT_URL", self.source)
        self.assertNotIn("api.vercel.com/v13/deployments", self.source)
        self.assertIn("/^dpl_[A-Za-z0-9]+$/", self.source)
        self.assertIn("Capture the current formal deployment for bounded rollback", self.source)
        self.assertIn("Verify the unaliased compatibility bridge on the current schema", self.source)
        self.assertIn("Promote the verified compatibility bridge before schema mutation", self.source)
        self.assertIn("Verify the unaliased candidate without generation or payment", self.source)
        self.assertIn("roll back on any formal verification failure", self.source)
        self.assertIn(
            "runtime.deployment_id !== process.env.PREVIOUS_DEPLOYMENT_ID",
            self.source,
        )

    def test_never_runs_generation_payment_or_broad_data_migration(self) -> None:
        self.assertEqual(
            self.source.count('--env "TASK_EXECUTION_MODE=backend"'),
            2,
        )
        forbidden = (
            "images/generations",
            "orders/create",
            "payments/checkout",
            "CREEM_CARD",
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
