"""Release activation role, uniqueness, immutability, and fault-intent schema."""

from __future__ import annotations

import importlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ReleaseActivationSchemaTest(unittest.TestCase):
    def test_0013_has_exact_parent_and_does_not_recreate_existing_baseline_tables(self) -> None:
        path = ROOT / "backend" / "alembic" / "versions" / "20260710_0013_ops_feature_flags.py"
        source = path.read_text(encoding="utf-8")
        self.assertIn('down_revision = "20260516_0012"', source)
        for existing in ("admin_audit_logs", "email_delivery_logs", "account_risk_events"):
            self.assertNotIn(f'op.create_table(\n        "{existing}"', source)
        self.assertEqual(source.count("ux_credit_transactions_order_refund_once"), 2)

    def test_model_exposes_release_coordinates_and_fault_fence(self) -> None:
        module = importlib.import_module("app.models.release_activation")
        columns = set(module.ReleaseActivation.__table__.columns.keys())
        required = {
            "id", "environment", "kind", "source_sha", "runtime_bundle_id",
            "manifest_sha256", "report_sha256", "api_deployment_id", "api_deployment_url", "api_role",
            "worker_deployment_id", "worker_role", "worker_image_digest",
            "private_evidence_prefix", "workflow_run_id", "workflow_attempt",
            "build_artifact_id", "build_artifact_digest",
            "phase", "phase_rank", "version", "approval", "reservation_expires_at",
            "current_snapshot_hash", "target_snapshot_hash",
            "acceptance_fault_intent_id", "acceptance_fault_intent_sha256",
            "acceptance_fault_state", "acceptance_fault_expires_at",
            "acceptance_fault_cleanup_claim_id", "acceptance_fault_cleanup_fencing_token",
        }
        self.assertTrue(required <= columns, sorted(required - columns))

    def test_control_plane_rls_uses_forced_non_bypass_database_roles(self) -> None:
        migration = (
            ROOT / "backend" / "alembic" / "versions" / "20260710_0013_ops_feature_flags.py"
        ).read_text(encoding="utf-8")
        for required in (
            "vowpic_runtime",
            "vowpic_control_writer",
            "NOLOGIN",
            "NOBYPASSRLS",
            "FORCE ROW LEVEL SECURITY",
            "TO vowpic_runtime",
            "TO vowpic_control_writer",
        ):
            self.assertIn(required, migration)
        self.assertNotIn("FOR ALL TO PUBLIC", migration)

        for example in (ROOT / ".env.example", ROOT / "backend" / ".env.example"):
            source = example.read_text(encoding="utf-8")
            self.assertIn("CONTROL_PLANE_DATABASE_URL=", source)
            database_line = next(
                line for line in source.splitlines() if line.startswith("DATABASE_URL=")
            )
            self.assertNotIn("postgres.", database_line)
            self.assertNotIn("postgres:", database_line)

    def test_fault_intent_expiry_is_anchored_to_creation_not_mutable_updated_at(self) -> None:
        migration = (
            ROOT / "backend" / "alembic" / "versions" / "20260710_0013_ops_feature_flags.py"
        ).read_text(encoding="utf-8")
        start = migration.index('name="ck_release_activation_fault_ttl"')
        expression = migration[migration.rfind("sa.CheckConstraint(", 0, start) : start]
        self.assertIn("acceptance_fault_expires_at > created_at", expression)
        self.assertIn("created_at + INTERVAL '300 seconds'", expression)
        self.assertNotIn("updated_at", expression)

    def test_migration_fixes_roles_environment_uniqueness_and_immutability(self) -> None:
        path = ROOT / "backend" / "alembic" / "versions" / "20260710_0013_ops_feature_flags.py"
        self.assertTrue(path.exists(), "0013 migration is missing")
        source = path.read_text(encoding="utf-8")
        for value in (
            "SAFE_BASELINE_INSTALL", "PREVIEW_IDENTITY", "PREVIEW_COMMERCIAL",
            "COMMERCIAL_7A", "CONTRACT_7B",
            "uq_release_activation_runtime_bundle", "uq_release_activation_active_source",
            "uq_release_activation_production_safe_baseline", "prevent_release_activation_regression",
            "phase rank must advance", "release_activations", "_no_delete BEFORE DELETE",
        ):
            self.assertIn(value, source)

    def test_fault_intent_is_hash_only_short_lived_and_production_7a_only(self) -> None:
        path = ROOT / "backend" / "alembic" / "versions" / "20260710_0013_ops_feature_flags.py"
        self.assertTrue(path.exists(), "0013 migration is missing")
        source = path.read_text(encoding="utf-8")
        for value in (
            "ck_release_activation_fault_complete", "ck_release_activation_fault_role",
            "ck_release_activation_fault_ttl", "uq_release_activation_fault_intent_id",
            "uq_release_activation_fault_intent_sha256", "CLEANUP_CLAIMED", "DISARMED",
        ):
            self.assertIn(value, source)
        self.assertNotIn("acceptance_fault_raw_correlation", source)
        self.assertNotIn("acceptance_fault_host_secret", source)


if __name__ == "__main__":
    unittest.main()
