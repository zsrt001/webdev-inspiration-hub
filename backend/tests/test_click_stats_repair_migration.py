from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
REPAIR_MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260712_0014_repair_click_stats_values.py"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class ClickStatsRepairMigrationContractTest(unittest.TestCase):
    def test_repair_is_idempotent_forward_only_and_fails_on_wrong_existing_types(self) -> None:
        self.assertTrue(REPAIR_MIGRATION.exists(), "the click_stats repair migration is missing")
        source = REPAIR_MIGRATION.read_text(encoding="utf-8")

        self.assertIn('revision = "20260712_0014"', source)
        self.assertIn('down_revision = "20260710_0013"', source)
        self.assertIn("ADD COLUMN IF NOT EXISTS value_sum INTEGER", source)
        self.assertIn("ADD COLUMN IF NOT EXISTS value_count INTEGER", source)
        self.assertIn("data_type <> 'integer'", source)
        self.assertIn("UPDATE click_stats", source)
        self.assertIn("ALTER COLUMN value_sum SET DEFAULT 0", source)
        self.assertIn("ALTER COLUMN value_sum SET NOT NULL", source)
        self.assertIn("ALTER COLUMN value_count SET DEFAULT 0", source)
        self.assertIn("ALTER COLUMN value_count SET NOT NULL", source)
        self.assertNotIn("drop_column", source)
        self.assertNotIn("DROP COLUMN", source)

    def test_safe_baseline_targets_and_hashes_both_forward_migrations(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        register = _read("scripts/release/register_safe_baseline.py")
        builder = _read("scripts/release/build_runtime_bundle_id.py")
        contract = _read("release/safe-baseline-contract.json")

        self.assertIn("--schema 20260712_0014", workflow)
        self.assertIn("20260710_0013_ops_feature_flags.py", workflow)
        self.assertIn("20260712_0014_repair_click_stats_values.py", workflow)
        self.assertIn("safe-baseline.v2", workflow)
        self.assertIn('TARGET_SCHEMA = "20260712_0014"', register)
        self.assertIn('schema_revision != "20260712_0014"', builder)
        self.assertIn("SAFE_BASELINE requires ordered 0013 and 0014 migration checksums", builder)
        self.assertIn('"schema_revision": "20260712_0014"', contract)

    def test_runtime_readiness_requires_the_repaired_click_stats_shape(self) -> None:
        source = _read("backend/app/services/schema_guard_service.py")

        self.assertIn('_MINIMUM_SCHEMA_REVISION = "20260712_0014"', source)
        self.assertIn("_REQUIRED_CLICK_STATS_COLUMNS", source)
        self.assertIn("click_stats missing columns", source)


if __name__ == "__main__":
    unittest.main()
