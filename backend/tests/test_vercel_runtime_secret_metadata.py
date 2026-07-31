"""Vercel Sensitive metadata proof for the EvoLink runtime key."""

from __future__ import annotations

from io import BytesIO
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_vercel_runtime_secret.py"


def _module():
    spec = importlib.util.spec_from_file_location("verify_vercel_runtime_secret_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class VercelRuntimeSecretMetadataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _module()

    @staticmethod
    def _opener(payload: bytes):
        def open_request(request, *, timeout):
            assert request.full_url.startswith("https://api.vercel.com/v10/projects/prj_project/env?")
            assert timeout == 30
            return _Response(payload)

        return open_request

    def test_accepts_unreadable_sensitive_production_metadata(self) -> None:
        payload = (
            b'{"envs":[{"key":"EVOLINK_API_KEY","type":"sensitive",'
            b'"target":["preview","production"],"decrypted":false}]}'
        )
        report = self.module.verify_runtime_secret(
            token="vercel-token",
            project_id="prj_project",
            team_id="team_owner",
            secret_name="EVOLINK_API_KEY",
            source_sha="a" * 40,
            opener=self._opener(payload),
        )

        self.assertTrue(report["passed"])
        self.assertFalse(report["vercel_value_readable"])
        self.assertEqual(report["vercel_target"], ["preview", "production"])

    def test_rejects_non_sensitive_or_non_production_metadata(self) -> None:
        for payload in (
            b'{"envs":[{"key":"EVOLINK_API_KEY","type":"encrypted","target":["production"]}]}',
            b'{"envs":[{"key":"EVOLINK_API_KEY","type":"sensitive","target":["preview"]}]}',
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                ValueError, "unreadable Production Sensitive"
            ):
                self.module.verify_runtime_secret(
                    token="vercel-token",
                    project_id="prj_project",
                    team_id="team_owner",
                    secret_name="EVOLINK_API_KEY",
                    source_sha="a" * 40,
                    opener=self._opener(payload),
                )


if __name__ == "__main__":
    unittest.main()
