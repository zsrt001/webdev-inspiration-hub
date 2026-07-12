"""Role-discriminated canonical runtime bundle identity contracts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "build_runtime_bundle_id.py"


def _module():
    if not SCRIPT.exists():
        raise AssertionError("runtime bundle builder is missing")
    spec = importlib.util.spec_from_file_location("build_runtime_bundle_id", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeBundleIdTest(unittest.TestCase):
    def test_safe_baseline_contract_contains_only_bootstrap_all_off_builder_facts(self) -> None:
        import json

        contract = json.loads((ROOT / "release" / "safe-baseline-contract.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_revision"], "20260710_0013")
        self.assertEqual(contract["seed_contract"]["state"], "OFF")
        self.assertEqual(len(contract["capabilities"]), 7)
        forbidden = {"worker", "provider", "model", "catalog", "activation_plan", "deployment_id"}
        self.assertFalse(forbidden & set(contract))

    def test_safe_baseline_is_deterministic_and_uses_only_allowlisted_inputs(self) -> None:
        module = _module()
        payload = {
            "source_sha": "a" * 40,
            "schema_revision": "20260710_0013",
            "migration_checksums": [{"revision": "20260710_0013", "sha256": "b" * 64}],
            "contract_hashes": {"safe_baseline": "c" * 64},
            "builder_contract_version": "safe-baseline.v1",
            "tool_version": "vowpic-release-tools.v1",
        }
        first = module.compute_runtime_bundle_id("SAFE_BASELINE", payload)
        second = module.compute_runtime_bundle_id("SAFE_BASELINE", dict(reversed(list(payload.items()))))
        self.assertEqual(first, second)
        self.assertRegex(first, r"^rtb_[0-9a-f]{64}$")

        for forbidden in (
            "worker_image_digest", "deployment_id", "api_prebuilt_checksum",
            "manifest_sha256", "evidence_sha256", "current_snapshot_hash",
            "provider_contract_hash", "catalog_contract_hash", "activation_plan_hash",
        ):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                module.compute_runtime_bundle_id("SAFE_BASELINE", {**payload, forbidden: "x"})

    def test_preview_identity_forbids_worker_and_role_changes_domain(self) -> None:
        module = _module()
        payload = {
            "source_sha": "d" * 40,
            "schema_revision": "20260710_0013",
            "migration_checksums": [{"revision": "20260710_0013", "sha256": "e" * 64}],
            "contract_hashes": {"identity_session_flag_preview": "f" * 64},
            "api_version": "api.v1",
            "tool_version": "vowpic-release-tools.v1",
        }
        preview_id = module.compute_runtime_bundle_id("PREVIEW_IDENTITY", payload)
        self.assertNotEqual(
            preview_id,
            module.compute_runtime_bundle_id("SAFE_BASELINE", {
                "source_sha": payload["source_sha"],
                "schema_revision": payload["schema_revision"],
                "migration_checksums": payload["migration_checksums"],
                "contract_hashes": {"safe_baseline": "f" * 64},
                "builder_contract_version": "safe-baseline.v1",
                "tool_version": payload["tool_version"],
            }),
        )
        with self.assertRaises(ValueError):
            module.compute_runtime_bundle_id(
                "PREVIEW_IDENTITY", {**payload, "worker_image_digest": "sha256:" + "1" * 64}
            )

    def test_worker_roles_require_digest_and_reject_live_coordinates(self) -> None:
        module = _module()
        base = {
            "source_sha": "1" * 40,
            "schema_revision": "20260710_0013",
            "migration_checksums": [{"revision": "20260710_0013", "sha256": "2" * 64}],
            "contract_hashes": {
                "payload": "3" * 64,
                "provider": "4" * 64,
                "model": "5" * 64,
                "catalog": "6" * 64,
                "flag": "7" * 64,
                "activation": "8" * 64,
            },
            "tool_version": "vowpic-release-tools.v1",
        }
        for role in ("PREVIEW_COMMERCIAL", "COMMERCIAL_7A", "CONTRACT_7B"):
            role_payload = dict(base)
            if role in {"COMMERCIAL_7A", "CONTRACT_7B"}:
                role_payload["builder_contract_version"] = role.lower() + ".v1"
            if role == "CONTRACT_7B":
                role_payload.update({
                    "schema_before": "20260710_0020",
                    "schema_target": "20260710_0021",
                    "contract_migration_sha256": "a" * 64,
                    "compatibility_version": "contract-7b.pre0021.v1",
                })
            with self.subTest(role=role), self.assertRaises(ValueError):
                module.compute_runtime_bundle_id(role, role_payload)
            result = module.compute_runtime_bundle_id(
                role, {**role_payload, "worker_image_digest": "sha256:" + "9" * 64}
            )
            self.assertRegex(result, r"^rtb_[0-9a-f]{64}$")
        with self.assertRaises(ValueError):
            module.compute_runtime_bundle_id(
                "COMMERCIAL_7A",
                {
                    **base,
                    "builder_contract_version": "commercial-7a.v1",
                    "worker_image_digest": "sha256:" + "9" * 64,
                    "deployment_id": "dpl",
                },
            )


if __name__ == "__main__":
    unittest.main()
