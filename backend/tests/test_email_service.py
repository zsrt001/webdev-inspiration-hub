"""Email notification behavior tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.services import email_service  # noqa: E402


class EmailServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_build_order_result_url_uses_frontend_hash_route(self) -> None:
        original_get_settings = email_service.get_settings
        try:
            email_service.get_settings = lambda: Settings(
                _env_file=None,
                frontend_base_url="https://studio.example.test/",
            )

            self.assertEqual(
                email_service.build_order_result_url("order id/with space"),
                "https://studio.example.test/#/pages/preview/preview?id=order%20id/with%20space",
            )
        finally:
            email_service.get_settings = original_get_settings

    async def test_send_order_completed_includes_result_cta_when_url_is_available(self) -> None:
        captured: dict = {}

        async def fake_send_email(**kwargs):
            captured.update(kwargs)
            return {"sent": True, "id": "email_123"}

        original_send = email_service._send_email
        original_get_settings = email_service.get_settings
        try:
            email_service._send_email = fake_send_email
            email_service.get_settings = lambda: Settings(
                _env_file=None,
                frontend_base_url="https://studio.example.test",
            )

            result = await email_service.send_order_completed(
                to="bride@example.test",
                order_id="abc123",
                preview_url="https://cdn.example.test/preview.jpg",
            )
        finally:
            email_service._send_email = original_send
            email_service.get_settings = original_get_settings

        self.assertTrue(result["sent"])
        self.assertEqual(captured["purpose"], "order_completed")
        self.assertIn("https://cdn.example.test/preview.jpg", captured["html"])
        self.assertIn("https://studio.example.test/#/pages/preview/preview?id=abc123", captured["html"])
        self.assertIn("Open your result", captured["html"])


if __name__ == "__main__":
    unittest.main()
