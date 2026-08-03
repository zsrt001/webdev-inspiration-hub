"""Secret-safe EvoLink credit preflight contracts."""

from __future__ import annotations

from decimal import Decimal
import importlib.util
import io
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_evolink_credits.py"


def _module():
    spec = importlib.util.spec_from_file_location("verify_evolink_credits", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(*, user: object, token: object, unlimited: object = False):
    return {
        "success": True,
        "message": "",
        "data": {
            "token": {
                "remaining_credits": token,
                "unlimited_credits": unlimited,
                "used_credits": 1,
            },
            "user": {"remaining_credits": user, "used_credits": 1},
        },
    }


class EvoLinkCreditPreflightTest(unittest.TestCase):
    def test_fetch_is_one_read_only_request_and_report_redacts_key(self) -> None:
        module = _module()
        seen: dict[str, object] = {}

        class Response(io.BytesIO):
            def getcode(self) -> int:
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        def opener(request, *, timeout):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["authorization"] = request.get_header("Authorization")
            seen["timeout"] = timeout
            return Response(json.dumps(_payload(user=100, token=100)).encode())

        payload = module.fetch_credits("secret-provider-key", opener=opener)
        report = module.evaluate_credits(payload)
        self.assertEqual(seen["url"], module.ENDPOINT)
        self.assertEqual(seen["method"], "GET")
        self.assertEqual(seen["authorization"], "Bearer secret-provider-key")
        self.assertTrue(report["passed"])
        self.assertNotIn("secret-provider-key", json.dumps(report, sort_keys=True))

    def test_sufficient_bounded_credit_is_secret_safe(self) -> None:
        report = _module().evaluate_credits(
            _payload(user="10.5", token="10"), minimum_credits=Decimal("10")
        )
        self.assertTrue(report["passed"])
        self.assertTrue(report["token_threshold_met"])
        self.assertTrue(report["user_threshold_met"])
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("10.5", serialized)
        self.assertNotIn("used_credits", serialized)

    def test_unlimited_token_still_requires_user_credit(self) -> None:
        module = _module()
        passing = module.evaluate_credits(
            _payload(user=10, token=0, unlimited=True)
        )
        failing = module.evaluate_credits(
            _payload(user="9.999", token=100, unlimited=True)
        )
        self.assertTrue(passing["passed"])
        self.assertFalse(failing["passed"])
        self.assertTrue(failing["token_threshold_met"])
        self.assertFalse(failing["user_threshold_met"])

    def test_bounded_token_below_threshold_fails(self) -> None:
        report = _module().evaluate_credits(_payload(user=100, token="9.9"))
        self.assertFalse(report["passed"])
        self.assertFalse(report["token_threshold_met"])
        self.assertTrue(report["user_threshold_met"])

    def test_invalid_financial_shapes_fail_closed(self) -> None:
        module = _module()
        for value in (None, True, "NaN", "Infinity", -1):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "token.remaining_credits"
            ):
                module.evaluate_credits(_payload(user=100, token=value))
        with self.assertRaisesRegex(ValueError, "unlimited_credits"):
            module.evaluate_credits(_payload(user=100, token=100, unlimited="true"))
        with self.assertRaisesRegex(ValueError, "response is invalid"):
            module.evaluate_credits({"success": False, "data": {}})

    def test_endpoint_is_fixed_to_official_credit_api(self) -> None:
        module = _module()
        self.assertEqual(
            module.ENDPOINT,
            "https://api.evolink.ai/v1/credits",
        )
        self.assertEqual(
            module.load_minimum_credits(
                ROOT / "release" / "provider-capabilities.json"
            ),
            Decimal("10"),
        )


if __name__ == "__main__":
    unittest.main()
