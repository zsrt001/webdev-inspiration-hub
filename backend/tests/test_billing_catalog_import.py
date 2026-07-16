"""Protected Provider product mapping validation tests."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.import_provider_catalog import CatalogMappingError, validate_mapping


def _mapping() -> dict:
    products = {
        code: f"prod_{index:02d}_real"
        for index, code in enumerate(
            (
                "pack_50",
                "pack_120",
                "pack_300",
                "starter_monthly",
                "creator_monthly",
                "studio_monthly",
            ),
            start=1,
        )
    }
    return {
        "schema_version": "provider-product-mapping.v1",
        "environment": "preview",
        "catalog_version": "2026-07-10",
        "provider": "creem",
        "release_sha": "a" * 40,
        "approver_audit_id": "approval-123",
        "products": products,
    }


class BillingCatalogImportTest(unittest.TestCase):
    def test_exact_mapping_is_accepted_and_output_hashes_ids(self) -> None:
        validated = validate_mapping(_mapping(), expected_environment="preview")
        self.assertEqual(len(validated.products), 6)
        self.assertTrue(all(len(item.provider_id_sha256) == 64 for item in validated.products))
        self.assertFalse(any("prod_" in item.provider_id_sha256 for item in validated.products))

    def test_missing_extra_duplicate_or_environment_mismatch_fails_closed(self) -> None:
        cases = []
        missing = _mapping()
        missing["products"].pop("pack_50")
        cases.append(missing)
        extra = _mapping()
        extra["products"]["unknown"] = "prod_unknown"
        cases.append(extra)
        duplicate = _mapping()
        duplicate["products"]["pack_120"] = duplicate["products"]["pack_50"]
        cases.append(duplicate)
        wrong_env = _mapping()
        wrong_env["environment"] = "production"
        cases.append(wrong_env)
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(CatalogMappingError):
                validate_mapping(payload, expected_environment="preview")


if __name__ == "__main__":
    unittest.main()
