"""Durable migration lease and append-only checkpoint schema contracts."""

from __future__ import annotations

import importlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DataMigrationSchemaTest(unittest.TestCase):
    def test_run_model_contains_parent_child_lease_and_fencing_contract(self) -> None:
        module = importlib.import_module("app.models.data_migration_run")
        columns = set(module.DataMigrationRun.__table__.columns.keys())
        required = {
            "id", "parent_run_id", "environment", "runtime_bundle_id", "manifest_sha256",
            "inventory_sha256", "script_sha256", "mode", "approval", "lease_owner",
            "lease_expires_at", "heartbeat_at", "fencing_token", "state", "counts_json",
            "created_at", "updated_at",
        }
        self.assertTrue(required <= columns, sorted(required - columns))

    def test_checkpoint_model_is_hash_bound_and_has_no_secret_payload(self) -> None:
        module = importlib.import_module("app.models.data_migration_checkpoint")
        columns = set(module.DataMigrationCheckpoint.__table__.columns.keys())
        required = {
            "id", "run_id", "script_sha256", "mode", "batch_boundary",
            "inventory_sha256", "manifest_sha256", "approval", "counts_json", "created_at",
        }
        self.assertTrue(required <= columns, sorted(required - columns))
        self.assertFalse({"database_url", "token", "secret", "raw_payload"} & columns)

    def test_0013_has_checkpoint_uniqueness_and_append_only_guards(self) -> None:
        path = ROOT / "backend" / "alembic" / "versions" / "20260710_0013_ops_feature_flags.py"
        self.assertTrue(path.exists(), "0013 migration is missing")
        source = path.read_text(encoding="utf-8")
        self.assertIn("uq_data_migration_checkpoint_boundary", source)
        self.assertIn("uq_data_migration_parent_release", source)
        self.assertIn("uq_data_migration_child_contract", source)
        self.assertIn("prevent_control_plane_mutation", source)
        self.assertIn("data_migration_checkpoints", source)


if __name__ == "__main__":
    unittest.main()
