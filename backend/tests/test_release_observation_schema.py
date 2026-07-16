"""Release observation run and append-only signed sample contracts."""

from __future__ import annotations

import importlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ReleaseObservationSchemaTest(unittest.TestCase):
    def test_run_binds_runtime_deployments_snapshots_and_finalizer(self) -> None:
        module = importlib.import_module("app.models.release_observation")
        columns = set(module.ReleaseObservationRun.__table__.columns.keys())
        required = {
            "id", "release_activation_id", "manifest_sha256", "runtime_bundle_id",
            "api_deployment_id", "worker_deployment_id", "worker_image_digest",
            "current_snapshot_hash", "target_snapshot_hash", "state", "started_at",
            "deadline_at", "cleanup_cycle_sha256", "version", "finalizer", "finalized_at",
        }
        self.assertTrue(required <= columns, sorted(required - columns))

    def test_sample_is_signed_append_only_and_unique_per_bucket(self) -> None:
        module = importlib.import_module("app.models.release_observation")
        columns = set(module.ReleaseObservationSample.__table__.columns.keys())
        required = {
            "id", "observation_run_id", "bucket_started_at", "sample_sha256",
            "signature", "metrics_json", "created_at",
        }
        self.assertTrue(required <= columns, sorted(required - columns))
        path = ROOT / "backend" / "alembic" / "versions" / "20260710_0013_ops_feature_flags.py"
        source = path.read_text(encoding="utf-8")
        self.assertIn("uq_release_observation_sample_bucket", source)
        self.assertIn("prevent_control_plane_mutation", source)
        self.assertIn("invalid release observation transition", source)
        self.assertIn("release_observation_runs", source)
        self.assertIn("_no_delete BEFORE DELETE", source)


if __name__ == "__main__":
    unittest.main()
