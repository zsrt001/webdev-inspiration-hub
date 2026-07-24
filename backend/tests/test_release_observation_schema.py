"""Release observation run and append-only signed sample contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
OBSERVE_SCRIPT = ROOT / "scripts" / "release" / "observe_release.py"
METRICS_SCRIPT = (
    ROOT / "scripts" / "release" / "collect_observation_metrics.py"
)


def _load_candidate(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_recovery_disposition_is_append_only_and_hash_bound(self) -> None:
        module = importlib.import_module("app.models.release_observation")
        columns = set(module.ReleaseObservationRecovery.__table__.columns.keys())
        required = {
            "id",
            "observation_run_id",
            "resolution_sha256",
            "worker_report_sha256",
            "api_report_sha256",
            "approval_sha256",
            "disposition",
            "recovery_report_sha256",
            "private_object_key",
            "created_at",
        }
        self.assertTrue(required <= columns, sorted(required - columns))
        source = (
            ROOT
            / "backend"
            / "alembic"
            / "versions"
            / "20260710_0020_partner_consent.py"
        ).read_text(encoding="utf-8")
        self.assertIn("uq_release_observation_recovery_run", source)
        self.assertIn("trg_release_observation_recovery_append_only", source)
        self.assertIn("release_observation_recoveries", source)
        self.assertIn("vowpic_observation_reader", source)
        self.assertIn("read_release_observation_metrics_v1", source)
        self.assertIn("identity_legacy_fallback_uses_seq", source)


class ReleaseObservationEvidenceTest(unittest.TestCase):
    @staticmethod
    def _module():
        return _load_candidate(OBSERVE_SCRIPT, "observe_release_candidate")

    @staticmethod
    def _run() -> dict[str, object]:
        started = datetime(2026, 7, 19, tzinfo=timezone.utc)
        return {
            "id": "11111111-1111-4111-8111-111111111111",
            "release_activation_id": "22222222-2222-4222-8222-222222222222",
            "source_sha": "a" * 40,
            "manifest_sha256": "b" * 64,
            "runtime_bundle_id": "runtime-1",
            "api_deployment_id": "api-1",
            "target_snapshot_hash": "d" * 64,
            "started_at": started,
            "deadline_at": started + timedelta(hours=24),
            "cleanup_cycle_sha256": "e" * 64,
        }

    @staticmethod
    def _metrics() -> dict[str, object]:
        return {
            "unresolved_p0_p1": 0,
            "unhandled_signed_webhooks": 0,
            "ledger_reconciliation_failures": 0,
            "backend_runtime_age_seconds": 10,
            "oldest_mandatory_outbox_age_seconds": 20,
            "synthetic_flow_dlq": 0,
            "acceptance_prefix_deletion_failures": 0,
            "cleanup_status": "PASS",
            "cleanup_cycle_sha256": "e" * 64,
            "rls_policy_gap_count": 0,
            "legacy_identity_fallback_count": 0,
            "flag_bundle_drift": 0,
        }

    def test_signed_sample_must_match_database_metrics_and_coordinates(self) -> None:
        module = self._module()
        run = self._run()
        metrics = self._metrics()
        bucket = run["started_at"] + timedelta(minutes=5)
        unsigned = {
            "schema": "vowpic.observation-sample.v1",
            "passed": True,
            "observation_run_id": run["id"],
            "source_sha": run["source_sha"],
            "manifest_sha256": run["manifest_sha256"],
            "runtime_bundle_id": run["runtime_bundle_id"],
            "api_deployment_id": run["api_deployment_id"],
            "target_snapshot_hash": run["target_snapshot_hash"],
            "bucket_started_at": bucket.isoformat(),
            "observed_at": bucket.isoformat(),
            "metrics": metrics,
        }
        key = b"k" * 32
        signature = hmac.new(
            key,
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        report = {**unsigned, "signature": f"hmac-sha256:{signature}"}
        row = {
            "bucket_started_at": bucket,
            "metrics_json": metrics,
            "signature": report["signature"],
        }
        observed, returned_metrics = module._validate_sample_report(
            report,
            row=row,
            run=run,
            signing_key=key,
            maximum_gap_minutes=15,
        )
        self.assertEqual(observed, bucket)
        self.assertEqual(returned_metrics, metrics)
        row["metrics_json"] = {**metrics, "unresolved_p0_p1": 1}
        with self.assertRaisesRegex(ValueError, "disagree"):
            module._validate_sample_report(
                report,
                row=row,
                run=run,
                signing_key=key,
                maximum_gap_minutes=15,
            )

    def test_observation_accepts_only_signed_backend_runtime_status(self) -> None:
        module = _load_candidate(METRICS_SCRIPT, "collect_observation_metrics_candidate")
        key = b"w" * 32
        source_sha = "a" * 40
        runtime = "rtb_" + "b" * 64
        api_deployment = "dpl_api_1"
        unsigned = {
            "schema": "vowpic.api-runtime-coordinate-report.v1",
            "passed": True,
            "source_sha": source_sha,
            "runtime_bundle_id": runtime,
            "api_deployment_id": api_deployment,
            "schema_revision": "20260710_0020",
            "release_role": "COMMERCIAL_7A",
            "liveness_response_sha256": "d" * 64,
            "readiness_response_sha256": "e" * 64,
            "version_response_sha256": "f" * 64,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        signature = hmac.new(
            key,
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        report = {**unsigned, "signature": f"hmac-sha256:{signature}"}
        age = module._validate_backend_runtime_report(
            report,
            signing_key=key,
            expected_source_sha=source_sha,
            expected_runtime_bundle_id=runtime,
            expected_api_deployment_id=api_deployment,
        )
        self.assertGreaterEqual(age, 0)
        report["release_role"] = "PREVIEW_COMMERCIAL"
        with self.assertRaisesRegex(ValueError, "runtime coordinates"):
            module._validate_backend_runtime_report(
                report,
                signing_key=key,
                expected_source_sha=source_sha,
                expected_runtime_bundle_id=runtime,
                expected_api_deployment_id=api_deployment,
            )

    def test_final_documents_recompute_24_hour_gap_and_bind_index(self) -> None:
        module = self._module()
        run = self._run()
        observed = [
            run["started_at"] + timedelta(minutes=15 * index)
            for index in range(97)
        ]
        hashes = [
            hashlib.sha256(f"sample-{index}".encode()).hexdigest()
            for index in range(len(observed))
        ]
        final = {
            "schema": "vowpic.observation-final-report.v1",
            "passed": True,
            "observation_run_id": run["id"],
            "activation_id": run["release_activation_id"],
            "source_sha": run["source_sha"],
            "manifest_sha256": run["manifest_sha256"],
            "runtime_bundle_id": run["runtime_bundle_id"],
            "api_deployment_id": run["api_deployment_id"],
            "target_snapshot_hash": run["target_snapshot_hash"],
            "window_started_at": run["started_at"].isoformat(),
            "window_deadline_at": run["deadline_at"].isoformat(),
            "sample_count": len(hashes),
            "sample_sha256": hashes,
            "sample_observed_at": [value.isoformat() for value in observed],
            "maximum_gap_seconds": 900,
            "cleanup_cycle_sha256": run["cleanup_cycle_sha256"],
            "produced_at": run["deadline_at"].isoformat(),
        }
        final_raw = (
            json.dumps(final, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        )
        index = {
            "schema": "vowpic.observation-final-index.v1",
            "passed": True,
            "observation_run_id": run["id"],
            "source_sha": run["source_sha"],
            "final_report_sha256": hashlib.sha256(final_raw).hexdigest(),
            "sample_sha256": hashes,
        }
        lease = {
            "schema": "vowpic.observation-finalization-lease.v1",
            "passed": True,
            "observation_run_id": run["id"],
            "state": "FINALIZING",
            "minimum_hours": 24,
            "maximum_gap_minutes": 15,
        }
        returned_final, returned_index = module._validate_final_documents(
            lease=lease,
            final=final,
            index=index,
            run=run,
        )
        self.assertEqual(returned_final, final_raw)
        self.assertEqual(
            hashlib.sha256(returned_final).hexdigest(),
            json.loads(returned_index)["final_report_sha256"],
        )
        final["sample_observed_at"][1] = (
            run["started_at"] + timedelta(minutes=16)
        ).isoformat()
        with self.assertRaisesRegex(ValueError, "coverage"):
            module._validate_final_documents(
                lease=lease,
                final=final,
                index=index,
                run=run,
            )

    def test_cleanup_cycle_report_is_signed_and_rejects_failed_deletion(self) -> None:
        module = importlib.import_module(
            "scripts.release.ensure_observation_cleanup_cycle"
        )
        run = {
            "id": "11111111-1111-4111-8111-111111111111",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_target",
        }
        counts = {
            "source_images": {
                "orders": 0,
                "deleted_assets": 0,
                "pending_assets": 0,
                "failed_assets": 0,
                "legacy_blocked_orders": 0,
            },
            "orders": {
                "orders": 0,
                "deleted_assets": 0,
                "pending_assets": 0,
                "failed_assets": 0,
                "legacy_blocked_orders": 0,
            },
            "deletion": {
                "rechecked": 0,
                "claimed": 0,
                "deleted": 0,
                "not_found": 0,
                "failed": 0,
                "tombstones_reconciled": 0,
            },
        }
        unsigned = {
            "schema": "vowpic.observation-cleanup-cycle.v1",
            "passed": True,
            "observation_run_id": run["id"],
            "source_sha": run["source_sha"],
            "runtime_bundle_id": run["runtime_bundle_id"],
            "api_deployment_id": run["api_deployment_id"],
            "response_sha256": "c" * 64,
            "counts": counts,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        key = b"o" * 32
        signature = hmac.new(
            key,
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        report = {**unsigned, "signature": f"hmac-sha256:{signature}"}
        module.validate_cleanup_report(report, run=run, signing_key=key)
        report["counts"]["deletion"]["failed"] = 1
        with self.assertRaisesRegex(ValueError, "blocking"):
            module.validate_cleanup_report(report, run=run, signing_key=key)

    def test_incident_metric_requires_taxonomy_and_counts_open_severity(self) -> None:
        module = _load_candidate(
            METRICS_SCRIPT, "collect_observation_metrics_candidate_incidents"
        )

        class Response:
            history: list[object] = []
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, path, params=None):
                if path.endswith("severity%3Ap0"):
                    return Response({"name": "severity:p0"})
                if path.endswith("severity%3Ap1"):
                    return Response({"name": "severity:p1"})
                return Response(
                    [
                        {
                            "number": 1,
                            "labels": [{"name": "severity:p1"}],
                        },
                        {
                            "number": 2,
                            "pull_request": {},
                            "labels": [{"name": "severity:p0"}],
                        },
                    ]
                )

        with patch.object(module.httpx, "Client", Client):
            self.assertEqual(
                module._unresolved_p0_p1(
                    repository="owner/repository",
                    token="t" * 40,
                ),
                1,
            )

        class MissingLabelClient(Client):
            def get(self, path, params=None):
                if "/labels/" in path:
                    response = Response({})
                    response.status_code = 404
                    return response
                return Response([])

        with patch.object(module.httpx, "Client", MissingLabelClient):
            with self.assertRaisesRegex(ValueError, "label contract"):
                module._unresolved_p0_p1(
                    repository="owner/repository",
                    token="t" * 40,
                )


if __name__ == "__main__":
    unittest.main()
