from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "collect_runtime_ddl_audit.py"
SPEC = importlib.util.spec_from_file_location("collect_runtime_ddl_audit", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class _Response:
    status_code = 503


class _Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def get(self, path):
        if path != "/api/v1/ops/readiness":
            raise AssertionError(path)
        return _Response()


class RuntimeDdlAuditCollectorTest(unittest.TestCase):
    def test_collector_requires_positive_runtime_delta_and_signs_exact_coordinates(self) -> None:
        http_evidence = {
            "guarded_routes": [{"name": "auth", "status": 503}],
            "retired_routes": [{"name": "legacy", "status": 410}],
            "invalid_webhook_status": 401,
            "logout_status": 404,
            "cleanup_status": 503,
        }
        now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
        with (
            mock.patch.object(
                collector,
                "_database_audit_counts",
                side_effect=[(12, 0), (19, 0)],
            ),
            mock.patch.object(collector, "_verify_http", return_value=http_evidence),
            mock.patch.object(collector.httpx, "Client", _Client),
        ):
            report = collector.collect_runtime_ddl_audit(
                base_url="https://deployment.example",
                protected_headers={"x-bypass": "redacted"},
                cleanup_token="cleanup-token",
                database_url="postgresql://migration:redacted@db.example/postgres",
                source_sha="a" * 40,
                runtime_bundle_id="rtb_" + "b" * 64,
                deployment_id="dpl_example",
                workflow_run_id="12345",
                workflow_attempt=2,
                hmac_key=b"h" * 32,
                now=now,
            )
        self.assertTrue(report["passed"])
        self.assertEqual(report["statement_count"], 7)
        self.assertEqual(report["ddl_statement_count"], 0)
        self.assertEqual(report["readiness_status"], 503)
        self.assertEqual(set(report["coverage"]), collector.DDL_AUDIT_COVERAGE)
        self.assertEqual(len(report["signature_hmac_sha256"]), 64)

    def test_collector_rejects_any_prior_or_new_runtime_ddl(self) -> None:
        with mock.patch.object(collector, "_database_audit_counts", return_value=(1, 1)):
            with self.assertRaisesRegex(ValueError, "already has recorded DDL"):
                collector.collect_runtime_ddl_audit(
                    base_url="https://deployment.example",
                    protected_headers={},
                    cleanup_token="cleanup",
                    database_url="postgresql://migration:redacted@db.example/postgres",
                    source_sha="a" * 40,
                    runtime_bundle_id="rtb_" + "b" * 64,
                    deployment_id="dpl_example",
                    workflow_run_id="12345",
                    workflow_attempt=2,
                    hmac_key=b"h" * 32,
                )

    def test_bootstrap_audit_function_is_security_definer_and_not_public(self) -> None:
        source = (
            ROOT / "scripts" / "release" / "bootstrap_production_database_roles.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("vowpic_runtime_statement_audit", source)
        self.assertIn("SECURITY DEFINER", source)
        self.assertIn("SET search_path = pg_catalog", source)
        self.assertIn(
            "REVOKE ALL ON FUNCTION public.vowpic_runtime_statement_audit() FROM PUBLIC",
            source,
        )
        self.assertIn("pg_stat_statements", source)


if __name__ == "__main__":
    unittest.main()
