from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

from scripts.release import ensure_vercel_automation_bypass as bypass


SECRET = "a" * 48
ROOT = Path(__file__).resolve().parents[2]


class FakeApi:
    project_id = "prj_Example123"
    team_id = "team_Example123"

    def __init__(self, entries=None) -> None:
        self.entries = dict(entries or {})
        self.generated: list[tuple[str, str]] = []
        self.returned_team_id = self.team_id

    def project(self):
        return {
            "id": self.project_id,
            "accountId": self.returned_team_id,
            "protectionBypass": dict(self.entries),
        }

    def generate_automation_bypass(self, *, secret: str, note: str):
        self.generated.append((secret, note))
        self.entries[secret] = {"scope": "automation-bypass", "note": note}
        return {"protectionBypass": dict(self.entries)}


class VercelAutomationBypassTest(unittest.TestCase):
    def test_direct_script_entrypoint_resolves_repository_package(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/release/ensure_vercel_automation_bypass.py"),
                "--help",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--header-env", completed.stdout)

    def test_exact_header_is_parsed_without_returning_the_name(self) -> None:
        self.assertEqual(
            bypass.parse_bypass_header(f"x-vercel-protection-bypass: {SECRET}"),
            SECRET,
        )
        for invalid in (
            SECRET,
            f"authorization: {SECRET}",
            "x-vercel-protection-bypass: short",
            f"x-vercel-protection-bypass: {SECRET}\nextra",
        ):
            with self.subTest(invalid=invalid[:20]):
                with self.assertRaises(ValueError):
                    bypass.parse_bypass_header(invalid)

    def test_missing_secret_is_created_and_read_back_without_plaintext_report(self) -> None:
        api = FakeApi()
        report = bypass.ensure_automation_bypass(api, SECRET)
        self.assertEqual(api.generated, [(SECRET, bypass.NOTE)])
        self.assertEqual(report["state"], "READY")
        self.assertTrue(report["created"])
        self.assertEqual(report["automation_secret_count"], 1)
        self.assertNotIn(SECRET, str(report))

    def test_existing_exact_secret_is_idempotent(self) -> None:
        api = FakeApi({SECRET: {"scope": "automation-bypass", "note": bypass.NOTE}})
        report = bypass.ensure_automation_bypass(api, SECRET)
        self.assertFalse(report["created"])
        self.assertEqual(api.generated, [])

    def test_unrelated_automation_secret_is_preserved_when_target_is_created(self) -> None:
        unrelated = "b" * 48
        metadata = {"scope": "automation-bypass", "note": "existing monitor"}
        api = FakeApi({unrelated: metadata})
        report = bypass.ensure_automation_bypass(api, SECRET)
        self.assertEqual(api.generated, [(SECRET, bypass.NOTE)])
        self.assertEqual(api.entries[unrelated], metadata)
        self.assertEqual(report["automation_secret_count"], 2)
        self.assertEqual(report["preexisting_automation_secret_count"], 1)
        self.assertEqual(report["preserved_unrelated_secret_count"], 1)
        self.assertNotIn(SECRET, str(report))
        self.assertNotIn(unrelated, str(report))

    def test_exact_and_unrelated_secrets_are_idempotent_together(self) -> None:
        unrelated = "b" * 48
        api = FakeApi(
            {
                SECRET: {"scope": "automation-bypass", "note": bypass.NOTE},
                unrelated: {"scope": "automation-bypass", "note": "existing monitor"},
            }
        )
        report = bypass.ensure_automation_bypass(api, SECRET)
        self.assertFalse(report["created"])
        self.assertEqual(api.generated, [])
        self.assertEqual(report["automation_secret_count"], 2)
        self.assertEqual(report["preserved_unrelated_secret_count"], 1)

    def test_preexisting_metadata_change_fails_readback(self) -> None:
        unrelated = "b" * 48

        class MutatingReadbackApi(FakeApi):
            def __init__(self) -> None:
                super().__init__(
                    {
                        SECRET: {"scope": "automation-bypass", "note": bypass.NOTE},
                        unrelated: {
                            "scope": "automation-bypass",
                            "note": "existing monitor",
                        },
                    }
                )
                self.project_calls = 0

            def project(self):
                self.project_calls += 1
                if self.project_calls == 2:
                    self.entries[unrelated]["note"] = "changed concurrently"
                return super().project()

        api = MutatingReadbackApi()
        with self.assertRaisesRegex(bypass.EdgeLockdownError, "changed"):
            bypass.ensure_automation_bypass(api, SECRET)
        self.assertEqual(api.generated, [])

    def test_wrong_project_or_team_fails_before_mutation(self) -> None:
        api = FakeApi()
        api.returned_team_id = "team_Other"
        with self.assertRaisesRegex(bypass.EdgeLockdownError, "outside"):
            bypass.ensure_automation_bypass(api, SECRET)
        self.assertEqual(api.generated, [])


if __name__ == "__main__":
    unittest.main()
