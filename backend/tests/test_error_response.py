"""API error response contract tests."""

from pathlib import Path
import json
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.error_response import normalize_error_detail  # noqa: E402


class ErrorResponseTest(unittest.TestCase):
    def test_arbitrary_string_detail_is_redacted_to_fixed_shape(self) -> None:
        payload = normalize_error_detail(
            "provider failed secret=abc at C:\\private\\file.py",
            400,
            "req_123",
        )

        self.assertEqual(
            set(payload),
            {"code", "message", "request_id", "retryable", "field_errors"},
        )
        self.assertEqual(payload["code"], "bad_request")
        self.assertEqual(payload["message"], "Invalid request. Please check your inputs.")
        self.assertEqual(payload["request_id"], "req_123")
        self.assertNotIn("secret", json.dumps(payload))
        self.assertNotIn("private", json.dumps(payload))

    def test_structured_domain_error_keeps_only_public_fields(self) -> None:
        payload = normalize_error_detail(
            {
                "code": "gatekeeper_reject",
                "message": "The photo is too blurry.",
                "reasons": ["too_blurry"],
                "provider_payload": {"secret": "hidden"},
                "retryable": False,
            },
            422,
            "req_456",
        )

        self.assertEqual(payload["code"], "gatekeeper_reject")
        self.assertEqual(payload["message"], "The photo is too blurry.")
        self.assertFalse(payload["retryable"])
        self.assertNotIn("reasons", payload)
        self.assertNotIn("provider_payload", payload)

    def test_validation_fields_have_no_input_or_context_values(self) -> None:
        payload = normalize_error_detail(
            {
                "code": "validation_failed",
                "field_errors": [
                    {
                        "field": "email",
                        "code": "value_error",
                        "message": "Invalid email.",
                        "input": "private@example.com",
                    }
                ],
            },
            422,
            "req_789",
        )

        self.assertEqual(
            payload["field_errors"],
            [{"field": "email", "code": "value_error", "message": "Invalid email."}],
        )
        self.assertNotIn("private@example.com", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
