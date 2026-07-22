"""Protected linked-acceptance bundle extraction contracts."""

from __future__ import annotations

from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tarfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
EXTRACTOR = ROOT / "scripts" / "release" / "extract_linked_acceptance_bundle.py"
TMP = ROOT / ".tmp" / "linked-acceptance-bundle"


def _module():
    spec = importlib.util.spec_from_file_location(
        "extract_linked_acceptance_bundle", EXTRACTOR
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LinkedAcceptanceBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(TMP, ignore_errors=True)
        if TMP.parent.exists() and not any(TMP.parent.iterdir()):
            TMP.parent.rmdir()

    @staticmethod
    def _action_bytes(phase: str) -> bytes:
        return (
            json.dumps(
                {
                    "schema": "vowpic.linked-production-action.v1",
                    "phase": phase,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )

    def _archive(
        self,
        name: str,
        *,
        extra_member: tarfile.TarInfo | None = None,
        omit: str | None = None,
    ) -> Path:
        module = _module()
        path = TMP / name
        with tarfile.open(path, "w:gz") as bundle:
            for phase in module.PHASES:
                if phase == omit:
                    continue
                raw = self._action_bytes(phase)
                member = tarfile.TarInfo(f"{phase}.json")
                member.size = len(raw)
                bundle.addfile(member, BytesIO(raw))
            if extra_member is not None:
                raw = b"blocked\n"
                extra_member.size = len(raw)
                bundle.addfile(extra_member, BytesIO(raw))
        return path

    def test_extracts_only_fixed_phase_actions_and_private_assets(self) -> None:
        module = _module()
        self.assertEqual(module.PHASES, ("quality", "account_finalize"))
        archive = self._archive("valid.tar.gz")
        destination = TMP / "actions"
        with patch.dict(os.environ, {"RUNNER_TEMP": str(TMP)}):
            report = module.extract_bundle(archive, destination)
        self.assertTrue(report["passed"])
        self.assertEqual(set(report["action_input_sha256"]), set(module.PHASES))
        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {f"{phase}.json" for phase in module.PHASES},
        )

    def test_rejects_missing_action_or_unexpected_root_file(self) -> None:
        module = _module()
        with patch.dict(os.environ, {"RUNNER_TEMP": str(TMP)}):
            with self.assertRaisesRegex(ValueError, "missing required"):
                module.extract_bundle(
                    self._archive("missing.tar.gz", omit="quality"),
                    TMP / "missing",
                )
            with self.assertRaisesRegex(ValueError, "unexpected root file"):
                module.extract_bundle(
                    self._archive(
                        "unexpected.tar.gz",
                        extra_member=tarfile.TarInfo("arbitrary-request.json"),
                    ),
                    TMP / "unexpected",
                )

    def test_rejects_traversal_links_and_wrong_action_identity(self) -> None:
        module = _module()
        with patch.dict(os.environ, {"RUNNER_TEMP": str(TMP)}):
            with self.assertRaisesRegex(ValueError, "escaped"):
                module.extract_bundle(
                    self._archive(
                        "traversal.tar.gz",
                        extra_member=tarfile.TarInfo("../outside.json"),
                    ),
                    TMP / "traversal",
                )

            link = tarfile.TarInfo("assets/link.jpg")
            link.type = tarfile.SYMTYPE
            link.linkname = "../commercial.json"
            with self.assertRaisesRegex(ValueError, "non-regular"):
                module.extract_bundle(
                    self._archive("link.tar.gz", extra_member=link),
                    TMP / "link",
                )

            archive = self._archive("wrong-identity.tar.gz")
            destination = TMP / "wrong-identity"
            with tarfile.open(archive, "r:gz") as source:
                members = [
                    (member, source.extractfile(member).read())
                    for member in source.getmembers()
                    if member.isfile()
                ]
            with tarfile.open(archive, "w:gz") as target:
                for member, raw in members:
                    if member.name == "quality.json":
                        raw = self._action_bytes("commercial")
                    member.size = len(raw)
                    target.addfile(member, BytesIO(raw))
            with self.assertRaisesRegex(ValueError, "identity is invalid"):
                module.extract_bundle(archive, destination)


if __name__ == "__main__":
    unittest.main()
