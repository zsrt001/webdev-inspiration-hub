"""Bounded Preview-cleanup RLS repair contract tests."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
REPAIR = ROOT / "scripts/release/repair_preview_cleanup_rls.py"
INTEGRATION = ROOT / ".github/workflows/integration.yml"
RECOVERY = ROOT / ".github/workflows/preview-identity-recovery.yml"


class PreviewCleanupRlsRepairTest(unittest.TestCase):
    def test_policy_surface_is_exact_row_scoped_and_non_destructive(self) -> None:
        repair = importlib.import_module(
            "scripts.release.repair_preview_cleanup_rls"
        )
        repair._validate_policy_specs()

        self.assertEqual(
            set(repair.POLICY_SPECS),
            set(repair.PREVIEW_USER_TABLES)
            | set(repair.PREVIEW_MUTABLE_TABLES),
        )
        self.assertEqual(
            set(repair.PREVIEW_USER_TABLES),
            {
                "user_credits",
                "credit_transactions",
                "credit_purchases",
                "orders",
                "live_portrait_jobs",
                "user_subscriptions",
                "subscription_credit_grants",
            },
        )
        self.assertEqual(
            set(repair.PREVIEW_MUTABLE_TABLES),
            {"auth_sessions", "auth_refresh_tokens", "media_assets"},
        )
        for table, commands in repair.POLICY_SPECS.items():
            expected = (
                {"SELECT", "UPDATE"}
                if table in repair.PREVIEW_MUTABLE_TABLES
                else {"SELECT"}
            )
            self.assertEqual(set(commands), expected)
            for predicate in commands.values():
                self.assertIn("acceptance_identity_bindings", predicate)
                self.assertIn("environment = 'preview'", predicate)
                self.assertNotEqual(predicate.strip().lower(), "true")

        source = REPAIR.read_text(encoding="utf-8")
        self.assertIn('TARGET_REVISION = "20260710_0020"', source)
        self.assertIn('MIGRATION_OWNER = "vowpic_migration_owner"', source)
        self.assertIn("rolbypassrls", source)
        self.assertIn("owner_is_migration_role", source)
        self.assertIn("ownership_preserved", source)
        self.assertIn("prove_preview_database_isolation", source)
        self.assertIn("pg_advisory_xact_lock", source)
        self.assertNotIn("FOR ALL TO", source)
        self.assertNotIn("CREATE ROLE", source)
        self.assertNotIn("ALTER ROLE", source)
        self.assertNotIn("GRANT INSERT", source)
        self.assertNotIn("GRANT DELETE", source)

    def test_only_authenticated_preview_workflows_can_run_the_repair(self) -> None:
        repair = importlib.import_module(
            "scripts.release.repair_preview_cleanup_rls"
        )
        base = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "zsrt001/webdev-inspiration-hub",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
        }
        for workflow in sorted(repair.TRUSTED_WORKFLOWS):
            environment = {
                **base,
                "GITHUB_WORKFLOW_REF": (
                    f"{base['GITHUB_REPOSITORY']}/{workflow}@refs/heads/main"
                ),
            }
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(
                    repair._require_trusted_preview_workflow(),
                    environment["GITHUB_WORKFLOW_REF"],
                )

        rejected = {
            **base,
            "GITHUB_WORKFLOW_REF": (
                f"{base['GITHUB_REPOSITORY']}/"
                ".github/workflows/production-release.yml@refs/heads/main"
            ),
        }
        with patch.dict(os.environ, rejected, clear=True):
            with self.assertRaisesRegex(ValueError, "trusted workflow"):
                repair._require_trusted_preview_workflow()

    def test_postgresql_internal_char_is_normalized_before_json_evidence(self) -> None:
        repair = importlib.import_module(
            "scripts.release.repair_preview_cleanup_rls"
        )

        self.assertEqual(repair._pg_char(b"r"), "r")
        self.assertEqual(repair._pg_char("w"), "w")

    def test_integration_and_exact_recovery_install_and_persist_the_repair(self) -> None:
        integration = INTEGRATION.read_text(encoding="utf-8")
        recovery = RECOVERY.read_text(encoding="utf-8")
        invocation = "python scripts/release/repair_preview_cleanup_rls.py"

        self.assertEqual(integration.count(invocation), 1)
        self.assertEqual(recovery.count(invocation), 1)
        self.assertIn(
            '--output "$EVIDENCE_DIR/preview-cleanup-rls-repair.json"',
            integration,
        )
        self.assertIn(
            "--output artifacts/preview-identity/cleanup-rls-repair.json",
            recovery,
        )
        self.assertIn(
            "artifacts/preview-identity/cleanup-rls-repair.json",
            recovery,
        )
        for source in (integration, recovery):
            for name in (
                "PREVIEW_MIGRATION_DATABASE_URL",
                "PREVIEW_RUNTIME_DATABASE_URL",
                "PREVIEW_CONTROL_PLANE_DATABASE_URL",
                "PREVIEW_CONTROL_READ_DATABASE_URL",
                "SUPABASE_PROJECT_REF",
                "PRODUCTION_SUPABASE_URL",
            ):
                self.assertIn(name, source)


if __name__ == "__main__":
    unittest.main()
