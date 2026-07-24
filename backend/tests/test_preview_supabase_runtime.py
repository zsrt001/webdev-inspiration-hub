"""Preview Supabase public browser-runtime resolution contracts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import httpx


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "resolve_preview_supabase_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "resolve_preview_supabase_runtime",
        SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("Preview Supabase runtime resolver is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreviewSupabaseRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_publishable_key_is_preferred_without_exposing_it_in_report(self) -> None:
        public_key = "sb_publishable_" + ("a" * 40)
        selected = self.module.select_public_key(
            [
                {
                    "id": "legacy-anon",
                    "type": "legacy",
                    "name": "anon",
                    "api_key": "eyJ" + ("b" * 80),
                },
                {
                    "id": "public-1",
                    "type": "publishable",
                    "name": "default",
                    "prefix": "sb_publishable_",
                    "api_key": public_key,
                },
                {
                    "id": "secret-1",
                    "type": "secret",
                    "name": "backend",
                    "api_key": "sb_secret_" + ("c" * 40),
                },
            ]
        )
        self.assertEqual(selected["api_key"], public_key)
        report = self.module.build_report(
            "zyrxfcdqszfmkkkicgqq",
            "https://zyrxfcdqszfmkkkicgqq.supabase.co",
            selected,
        )
        self.assertNotIn(public_key, json.dumps(report))
        self.assertEqual(report["public_key_type"], "publishable")
        self.assertRegex(report["public_key_sha256"], r"^[0-9a-f]{64}$")

    def test_legacy_anon_is_the_only_allowed_fallback(self) -> None:
        anon = "eyJ" + ("d" * 80)
        selected = self.module.select_public_key(
            [
                {
                    "id": "legacy-anon",
                    "type": "legacy",
                    "name": "anon",
                    "api_key": anon,
                },
                {
                    "id": "legacy-service",
                    "type": "legacy",
                    "name": "service_role",
                    "api_key": "eyJ" + ("e" * 80),
                },
            ]
        )
        self.assertEqual(selected["api_key"], anon)
        self.assertEqual(selected["name"], "anon")

    def test_ambiguous_or_secret_only_inventory_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.module.select_public_key(
                [
                    {
                        "id": "secret-1",
                        "type": "secret",
                        "name": "backend",
                        "api_key": "sb_secret_" + ("f" * 40),
                    }
                ]
            )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.module.select_public_key(
                [
                    {
                        "id": "public-1",
                        "type": "publishable",
                        "name": "one",
                        "api_key": "sb_publishable_" + ("1" * 40),
                    },
                    {
                        "id": "public-2",
                        "type": "publishable",
                        "name": "two",
                        "api_key": "sb_publishable_" + ("2" * 40),
                    },
                ]
            )

    def test_management_read_is_exactly_scoped_and_nonmutating(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["authorization"] = request.headers.get("authorization")
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "public-1",
                        "type": "publishable",
                        "name": "default",
                        "api_key": "sb_publishable_" + ("a" * 40),
                    }
                ],
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            url, selected = self.module.read_public_runtime(
                "zyrxfcdqszfmkkkicgqq",
                token="management-token",
                client=client,
            )
        self.assertEqual(seen["method"], "GET")
        self.assertEqual(
            seen["url"],
            "https://api.supabase.com/v1/projects/zyrxfcdqszfmkkkicgqq/api-keys?reveal=true",
        )
        self.assertEqual(seen["authorization"], "Bearer management-token")
        self.assertEqual(url, "https://zyrxfcdqszfmkkkicgqq.supabase.co")
        self.assertEqual(selected["type"], "publishable")

    def test_job_environment_receives_only_the_public_runtime_values(self) -> None:
        public_key = "sb_publishable_" + ("9" * 40)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "github-env")
            self.module.write_job_env(
                path,
                supabase_url="https://zyrxfcdqszfmkkkicgqq.supabase.co",
                api_key=public_key,
            )
            value = path.read_text(encoding="utf-8")
        self.assertEqual(
            value,
            "PREVIEW_SUPABASE_URL=https://zyrxfcdqszfmkkkicgqq.supabase.co\n"
            f"PREVIEW_SUPABASE_PUBLISHABLE_KEY={public_key}\n",
        )
        self.assertNotIn("service_role", value)
        self.assertNotIn("sb_secret_", value)


if __name__ == "__main__":
    unittest.main()
