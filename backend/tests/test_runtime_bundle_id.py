"""Role-discriminated canonical runtime bundle identity contracts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
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
        self.assertEqual(contract["schema_revision"], "20260712_0014")
        self.assertEqual(contract["seed_contract"]["state"], "OFF")
        self.assertEqual(len(contract["capabilities"]), 7)
        forbidden = {"worker", "provider", "model", "catalog", "activation_plan", "deployment_id"}
        self.assertFalse(forbidden & set(contract))

    def test_safe_baseline_is_deterministic_and_uses_only_allowlisted_inputs(self) -> None:
        module = _module()
        payload = {
            "source_sha": "a" * 40,
            "schema_revision": "20260712_0014",
            "migration_checksums": [
                {"revision": "20260710_0013", "sha256": "b" * 64},
                {"revision": "20260712_0014", "sha256": "d" * 64},
            ],
            "contract_hashes": {"safe_baseline": "c" * 64},
            "builder_contract_version": "safe-baseline.v2",
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
                "schema_revision": "20260712_0014",
                "migration_checksums": [
                    {"revision": "20260710_0013", "sha256": "e" * 64},
                    {"revision": "20260712_0014", "sha256": "a" * 64},
                ],
                "contract_hashes": {"safe_baseline": "f" * 64},
                "builder_contract_version": "safe-baseline.v2",
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
                "policy": "a" * 64,
                "runtime": "b" * 64,
                "gate": "c" * 64,
            },
            "tool_version": "vowpic-release-tools.v1",
        }
        for role in ("PREVIEW_COMMERCIAL", "COMMERCIAL_7A", "CONTRACT_7B"):
            role_payload = dict(base)
            role_payload["contract_hashes"] = dict(base["contract_hashes"])
            if role == "PREVIEW_COMMERCIAL":
                role_payload["contract_hashes"]["preview"] = "d" * 64
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

    def test_preview_and_production_domains_differ_with_identical_source_and_worker_digest(self) -> None:
        module = _module()
        base = {
            "source_sha": "1" * 40,
            "schema_revision": "20260710_0020",
            "migration_checksums": [{"revision": "20260710_0020", "sha256": "2" * 64}],
            "contract_hashes": {
                "payload": "3" * 64,
                "provider": "4" * 64,
                "model": "5" * 64,
                "catalog": "6" * 64,
                "flag": "7" * 64,
                "activation": "8" * 64,
                "policy": "a" * 64,
                "runtime": "b" * 64,
                "gate": "c" * 64,
                "preview": "d" * 64,
            },
            "tool_version": "vowpic-release-tools.v1",
            "worker_image_digest": "sha256:" + "9" * 64,
        }
        preview = module.compute_runtime_bundle_id("PREVIEW_COMMERCIAL", base)
        production = module.compute_runtime_bundle_id(
            "COMMERCIAL_7A",
            {
                **base,
                "contract_hashes": {
                    key: value for key, value in base["contract_hashes"].items() if key != "preview"
                },
                "builder_contract_version": "commercial-7a.v1",
            },
        )
        self.assertNotEqual(preview, production)

    def test_preview_commercial_cli_hashes_the_exact_planned_contract_files(self) -> None:
        catalog = ROOT / "release" / "catalog" / "catalog-2026-07-10.json"
        command = [
            str(ROOT / ".venv" / "Scripts" / "python.exe"),
            str(SCRIPT),
            "--release-role", "PREVIEW_COMMERCIAL",
            "--source-sha", "a" * 40,
            "--schema", "20260710_0020",
            "--worker-image-digest", "sha256:" + "b" * 64,
            "--runtime-contract", str(ROOT / "release" / "runtime-contracts.json"),
            "--preview-contract", str(ROOT / "release" / "preview-runtime-contract.json"),
            "--provider-contract", str(ROOT / "release" / "provider-contracts.json"),
            "--catalog-contract", str(catalog),
            "--flag-contract", str(ROOT / "release" / "gates.json"),
            "--activation-plan", str(ROOT / "release" / "activation-plan.json"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runtime-id.txt"
            first = subprocess.run(
                [*command, "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_id = output.read_text(encoding="utf-8").strip()
            self.assertRegex(first_id, r"^rtb_[0-9a-f]{64}$")

            second = subprocess.run(
                [*command, "--output", str(Path(directory) / "runtime-id-2.txt")],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout.strip(), second.stdout.strip())

    def test_runtime_contract_rejects_a_stale_source_digest(self) -> None:
        module = _module()
        contract_path = ROOT / "release" / "runtime-contracts.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["source_sha256"]["backend/app/core/config.py"] = "0" * 64
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="runtime-contracts-stale-",
            dir=contract_path.parent,
            delete=False,
        ) as handle:
            json.dump(contract, handle)
            stale_path = Path(handle.name)
        try:
            with self.assertRaisesRegex(ValueError, "source digest is stale"):
                module._runtime_contract_hashes(
                    str(stale_path),
                    schema_revision="20260710_0020",
                )
        finally:
            stale_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
