"""API error response contract tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.error_response import normalize_error_detail  # noqa: E402


class ErrorResponseTest(unittest.TestCase):
    def test_string_detail_is_normalized(self) -> None:
        payload = normalize_error_detail("Missing user image", 400, "req_123")

        self.assertEqual(payload["error"], "bad_request")
        self.assertEqual(payload["message"], "Missing user image")
        self.assertEqual(payload["request_id"], "req_123")
        self.assertIn("action", payload)

    def test_structured_detail_preserves_error_and_message(self) -> None:
        payload = normalize_error_detail(
            {
                "error": "gatekeeper_reject",
                "message": "The photo is too blurry.",
                "reasons": ["too_blurry"],
            },
            422,
            "req_456",
        )

        self.assertEqual(payload["error"], "gatekeeper_reject")
        self.assertEqual(payload["message"], "The photo is too blurry.")
        self.assertEqual(payload["request_id"], "req_456")
        self.assertEqual(payload["reasons"], ["too_blurry"])

    def test_advice_can_supply_message(self) -> None:
        payload = normalize_error_detail(
            {"error": "gatekeeper_reject", "advice": ["Please upload a sharper portrait."]},
            422,
            "req_789",
        )

        self.assertEqual(payload["message"], "Please upload a sharper portrait.")


if __name__ == "__main__":
    unittest.main()
