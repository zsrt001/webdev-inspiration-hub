from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "production_inventory_rls.py"


def _module():
    spec = importlib.util.spec_from_file_location("production_inventory_rls", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("production inventory RLS module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductionInventoryRlsTest(unittest.TestCase):
    def test_identifier_quoting_is_total_and_policy_name_is_fixed(self) -> None:
        module = _module()
        self.assertEqual(module._quote_identifier('odd"table'), '"odd""table"')
        self.assertEqual(module.INVENTORY_POLICY_NAME, "vowpic_inventory_select")

    def test_policy_proof_requires_exact_nobypassrls_coverage(self) -> None:
        module = _module()
        valid = {
            "authenticated_role_name": "vowpic_inventory_login",
            "role_name": "vowpic_inventory_login",
            "role_bypass_rls": False,
            "role_membership_count": 0,
            "owned_object_count": 0,
            "inventory_table_count": 3,
            "readable_inventory_table_count": 3,
            "inventory_sequence_count": 1,
            "readable_inventory_sequence_count": 1,
            "rls_table_count": 2,
            "inventory_select_policy_count": 2,
            "invalid_inventory_policy_count": 0,
        }
        module.validate_inventory_policy_proof(valid)
        for field, value in (
            ("role_bypass_rls", True),
            ("role_membership_count", 1),
            ("owned_object_count", 1),
            ("readable_inventory_table_count", 2),
            ("readable_inventory_sequence_count", 0),
            ("inventory_select_policy_count", 1),
            ("invalid_inventory_policy_count", 1),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                module.validate_inventory_policy_proof({**valid, field: value})


if __name__ == "__main__":
    unittest.main()
