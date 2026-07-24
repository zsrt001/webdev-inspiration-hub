"""Preview Commercial callback alias and host-isolation contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend/scripts/configure_preview_commercial_callback.py"


def _module():
    spec = importlib.util.spec_from_file_location("preview_commercial_callback", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreviewCommercialCallbackTest(unittest.TestCase):
    def activation(self) -> dict[str, object]:
        return {
            "report_version": "vowpic.preview-commercial-activation.v1",
            "passed": True,
            "activation_id": "00000000-0000-0000-0000-000000000059",
            "kind": "PREVIEW_COMMERCIAL",
            "phase": "COMPLETED",
            "source_sha": "1" * 40,
            "runtime_bundle_id": "rtb_" + "2" * 64,
            "api_deployment_id": "dpl_preview59",
            "api_deployment_url": "https://vowpic-preview-59.vercel.app",
            "workflow_run_id": "5900",
            "workflow_attempt": 1,
        }

    def test_fixed_test_callback_is_exact_and_deployment_bound(self) -> None:
        module = _module()
        state = module.build_callback_state(
            self.activation(),
            callback_host="vowpic-creem-test.vercel.app",
            now=datetime(2026, 7, 23, tzinfo=timezone.utc),
        )
        self.assertEqual(
            state["webhook_url"],
            "https://vowpic-creem-test.vercel.app/api/v1/payments/webhook/creem",
        )
        self.assertEqual(state["api_deployment_id"], "dpl_preview59")
        self.assertEqual(state["previous_alias_state"], "ABSENT")
        module._validate_state(state)

    def test_rejects_random_or_deployment_callback_hosts(self) -> None:
        module = _module()
        for host in (
            "preview.example.com",
            "vowpic-provider-abc.vercel.app",
            "vowpic-preview-59.vercel.app",
        ):
            with self.subTest(host=host), self.assertRaises(ValueError):
                module.build_callback_state(self.activation(), callback_host=host)

    def test_application_callback_host_exposes_only_post_webhook(self) -> None:
        from app import main

        original = main.settings.creem_callback_host
        main.settings.creem_callback_host = "vowpic-creem-test.vercel.app"
        try:
            self.assertTrue(
                main._creem_callback_request_is_allowed(
                    host_header="vowpic-creem-test.vercel.app",
                    method="POST",
                    path="/api/v1/payments/webhook/creem",
                )
            )
            for method, path in (
                ("GET", "/api/v1/payments/webhook/creem"),
                ("GET", "/version"),
                ("POST", "/api/v1/orders/create"),
            ):
                with self.subTest(method=method, path=path):
                    self.assertFalse(
                        main._creem_callback_request_is_allowed(
                            host_header="vowpic-creem-test.vercel.app",
                            method=method,
                            path=path,
                        )
                    )
            self.assertTrue(
                main._creem_callback_request_is_allowed(
                    host_header="vowpic-provider-run.vercel.app",
                    method="GET",
                    path="/",
                )
            )
        finally:
            main.settings.creem_callback_host = original

    def test_evolink_callback_host_exposes_only_signed_callback_and_probe(self) -> None:
        from app import main

        original_origin = main.settings.evolink_callback_base_url
        original_probe = main.settings.provider_grant_probe_secret
        main.settings.evolink_callback_base_url = (
            "https://vowpic-evolink-aaaaaaaaaaaa-5900-1.vercel.app"
        )
        main.settings.provider_grant_probe_secret = "provider-probe-secret-" + "x" * 32
        host = "vowpic-evolink-aaaaaaaaaaaa-5900-1.vercel.app"
        callback = (
            "/api/v1/provider-callbacks/evolink/"
            "00000000-0000-4000-8000-000000000074/"
            + "a" * 64
        )
        try:
            self.assertTrue(
                main._evolink_callback_request_is_allowed(
                    host_header=host,
                    method="POST",
                    path=callback,
                )
            )
            self.assertTrue(
                main._evolink_callback_request_is_allowed(
                    host_header=host,
                    method="GET",
                    path="/api/v1/version",
                    probe_secret=main.settings.provider_grant_probe_secret,
                )
            )
            for method, path, supplied in (
                ("GET", "/api/v1/version", ""),
                ("POST", "/api/v1/orders", ""),
                ("GET", callback, ""),
                ("POST", callback + "/extra", ""),
            ):
                with self.subTest(method=method, path=path):
                    self.assertFalse(
                        main._evolink_callback_request_is_allowed(
                            host_header=host,
                            method=method,
                            path=path,
                            probe_secret=supplied,
                        )
                    )
        finally:
            main.settings.evolink_callback_base_url = original_origin
            main.settings.provider_grant_probe_secret = original_probe


if __name__ == "__main__":
    unittest.main()
