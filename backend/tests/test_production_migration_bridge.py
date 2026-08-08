"""Production schema bridge and signed-event replay contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductionMigrationBridgeTest(unittest.TestCase):
    def test_bridge_requires_exact_0020_release_baseline_and_tables(self) -> None:
        module = _load(
            "verify_safe_baseline_bridge",
            "scripts/release/verify_safe_baseline_bridge.py",
        )
        source = "a" * 40
        release = {
            "id": "00000000-0000-0000-0000-000000000071",
            "environment": "production",
            "kind": "COMMERCIAL_7A",
            "source_sha": source,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "manifest_sha256": "c" * 64,
            "phase": "MANIFEST_SEALED",
        }
        baseline = {
            "id": "00000000-0000-0000-0000-000000000013",
            "environment": "production",
            "kind": "SAFE_BASELINE_INSTALL",
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "api_deployment_id": "dpl_safe",
            "phase": "COMPLETED",
        }
        tables = [
            {"table_name": name, "present": True}
            for name in module.REQUIRED_TABLES
        ]
        report = module.build_bridge_report(
            source_sha=source,
            revision_rows=[{"version_num": "20260710_0021"}],
            release_rows=[release],
            safe_baseline_rows=[baseline],
            table_rows=tables,
            checked_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["required_table_count"], len(module.REQUIRED_TABLES))
        with self.assertRaisesRegex(ValueError, "tables are missing"):
            module.build_bridge_report(
                source_sha=source,
                revision_rows=[{"version_num": "20260710_0021"}],
                release_rows=[release],
                safe_baseline_rows=[baseline],
                table_rows=tables[:-1],
            )

    def test_replay_report_fails_closed_on_every_unresolved_event_state(self) -> None:
        module = _load(
            "replay_migration_window_events",
            "scripts/release/replay_migration_window_events.py",
        )
        before = {
            "RECEIVED": 2,
            "UNHANDLED": 0,
            "APPLIED": 5,
            "RECONCILIATION_REQUIRED": 0,
        }
        after = {
            "RECEIVED": 0,
            "UNHANDLED": 0,
            "APPLIED": 7,
            "RECONCILIATION_REQUIRED": 0,
        }
        report = module.build_replay_report(
            manifest_sha256="a" * 64,
            before=before,
            replayed_count=2,
            after=after,
        )
        self.assertTrue(report["passed"])
        for state in ("RECEIVED", "UNHANDLED", "RECONCILIATION_REQUIRED"):
            with self.subTest(state=state), self.assertRaisesRegex(
                ValueError, "blocking states"
            ):
                module.build_replay_report(
                    manifest_sha256="a" * 64,
                    before=before,
                    replayed_count=2,
                    after={**after, state: 1},
                )


if __name__ == "__main__":
    unittest.main()
