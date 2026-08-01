"""Preview runtime-authority preflight contracts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "verify_preview_runtime_authority.py"


def load_module():
    fake_database = types.ModuleType("app.core.database")
    fake_database.engine = object()
    original = sys.modules.get("app.core.database")
    sys.modules["app.core.database"] = fake_database
    try:
        spec = importlib.util.spec_from_file_location(
            "verify_preview_runtime_authority",
            SCRIPT,
        )
        if spec is None or spec.loader is None:
            raise AssertionError("Preview runtime-authority verifier is missing")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if original is None:
            sys.modules.pop("app.core.database", None)
        else:
            sys.modules["app.core.database"] = original


class PreviewRuntimeAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.capabilities = sorted(self.module.EXPECTED_CAPABILITIES)

    def test_exact_runtime_authority_facts_pass(self) -> None:
        report = self.module.validate_facts(
            {
                "current_user": "vowpic_app_runtime",
                "runtime_member": True,
                "flags_select": True,
                "flags_update": False,
                "capabilities": self.capabilities,
            }
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["preview_capability_count"], 7)

    def test_missing_privilege_or_capability_fails_closed(self) -> None:
        invalid = [
            {"flags_select": False},
            {"flags_update": True},
            {"runtime_member": False},
            {"capabilities": self.capabilities[:-1]},
        ]
        baseline = {
            "current_user": "vowpic_app_runtime",
            "runtime_member": True,
            "flags_select": True,
            "flags_update": False,
            "capabilities": self.capabilities,
        }
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.module.validate_facts({**baseline, **override})


if __name__ == "__main__":
    unittest.main()
