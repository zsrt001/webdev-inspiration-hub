"""Preview Provider owner resolution contracts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "resolve_preview_provider_owner.py"


def load_module():
    spec = importlib.util.spec_from_file_location("resolve_preview_provider_owner", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Preview Provider owner resolver is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreviewProviderOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_primary_google_subject_is_exact_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "subjects.json")
            path.write_text(
                json.dumps(
                    [
                        {"provider": "google", "subject": "primary-subject"},
                        "secondary-subject",
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                self.module.primary_google_subject(path),
                "primary-subject",
            )

    def test_subject_inventory_fails_closed(self) -> None:
        for payload in (
            ["only-one"],
            ["duplicate", "duplicate"],
            [{"provider": "github", "subject": "one"}, "two"],
            [{"subject": "one"}, "two"],
        ):
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory, "subjects.json")
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        self.module.primary_google_subject(path)

    def test_report_hashes_owner_and_preserves_exact_coordinates(self) -> None:
        owner = uuid.uuid4()
        activation = SimpleNamespace(
            id=uuid.uuid4(),
            source_sha="a" * 40,
            workflow_run_id="12345",
            workflow_attempt=2,
            api_deployment_id="dpl_identity",
        )
        report = self.module.build_report(activation, owner)
        serialized = json.dumps(report)
        self.assertNotIn(str(owner), serialized)
        self.assertEqual(report["identity_deployment_id"], "dpl_identity")
        self.assertEqual(report["state"], "RESOLVED_FROM_CONSUMED_BINDING")
        self.assertRegex(report["owner_user_id_sha256"], r"^[0-9a-f]{64}$")

        prior_report = self.module.build_report(
            activation,
            owner,
            selection_mode="latest-cleaned",
        )
        self.assertEqual(
            prior_report["state"],
            "RESOLVED_FROM_PRIOR_CLEANED_BINDING",
        )
        with self.assertRaises(ValueError):
            self.module.build_report(activation, owner, selection_mode="unknown")

    def test_cleanup_identity_count_contract_is_zero_or_two_only(self) -> None:
        cleanup_script = ROOT / "backend" / "scripts" / "cleanup_preview_commercial.py"
        spec = importlib.util.spec_from_file_location(
            "cleanup_preview_commercial",
            cleanup_script,
        )
        if spec is None or spec.loader is None:
            raise AssertionError("Preview Commercial cleanup is missing")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module._validate_expected_identity_count(0), 0)
        self.assertEqual(module._validate_expected_identity_count(2), 2)
        for invalid in (-1, 1, 3):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                module._validate_expected_identity_count(invalid)

    def test_job_environment_receives_only_the_resolved_uuid(self) -> None:
        owner = uuid.uuid4()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "github-env")
            self.module.write_job_env(path, owner_user_id=owner)
            value = path.read_text(encoding="utf-8")
        self.assertEqual(value, f"PREVIEW_PROVIDER_OWNER_USER_ID={owner}\n")

    def test_source_coordinates_reject_nonexact_values(self) -> None:
        self.assertEqual(
            self.module.validate_source_coordinates(
                source_sha="b" * 40,
                workflow_run_id="9876",
                workflow_attempt=1,
            ),
            ("b" * 40, "9876", 1),
        )
        for kwargs in (
            {"source_sha": "main", "workflow_run_id": "1", "workflow_attempt": 1},
            {"source_sha": "b" * 40, "workflow_run_id": "0", "workflow_attempt": 1},
            {"source_sha": "b" * 40, "workflow_run_id": "1", "workflow_attempt": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.module.validate_source_coordinates(**kwargs)


if __name__ == "__main__":
    unittest.main()
