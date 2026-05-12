"""Gatekeeper degradation contract tests."""

from io import BytesIO
from pathlib import Path
import asyncio
import sys
import unittest

from PIL import Image, ImageDraw


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import gatekeeper_service  # noqa: E402


def _sharp_test_image_bytes() -> bytes:
    image = Image.new("RGB", (800, 800), (175, 175, 175))
    draw = ImageDraw.Draw(image)
    for pos in range(0, 800, 20):
        draw.line((pos, 0, pos, 799), fill=(35, 35, 35), width=2)
        draw.line((0, pos, 799, pos), fill=(35, 35, 35), width=2)
    draw.ellipse((280, 120, 520, 360), fill=(225, 178, 145), outline=(40, 40, 40), width=4)
    draw.rectangle((240, 360, 560, 720), fill=(245, 245, 245), outline=(45, 45, 45), width=4)
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    image_bytes = _sharp_test_image_bytes()

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get(self, url: str) -> _FakeResponse:
        return _FakeResponse(self.image_bytes)


class GatekeeperServiceTest(unittest.TestCase):
    def _run_with_patches(self, analyze, ocr=None):
        original_configured = gatekeeper_service.llm_service.is_vision_provider_configured
        original_analyze = gatekeeper_service.llm_service.analyze_face_quality
        original_ocr = gatekeeper_service.llm_service.detect_sensitive_document_ocr
        original_client = gatekeeper_service.httpx.AsyncClient
        gatekeeper_service.llm_service.is_vision_provider_configured = lambda: True
        gatekeeper_service.llm_service.analyze_face_quality = analyze
        if ocr is not None:
            gatekeeper_service.llm_service.detect_sensitive_document_ocr = ocr
        gatekeeper_service.httpx.AsyncClient = _FakeAsyncClient
        try:
            return asyncio.run(gatekeeper_service.check_image_quality("https://example.com/upload.jpg"))
        finally:
            gatekeeper_service.llm_service.is_vision_provider_configured = original_configured
            gatekeeper_service.llm_service.analyze_face_quality = original_analyze
            gatekeeper_service.llm_service.detect_sensitive_document_ocr = original_ocr
            gatekeeper_service.httpx.AsyncClient = original_client

    def test_vision_outage_falls_back_to_local_quality_checks(self) -> None:
        async def analyze(_url: str) -> dict:
            raise RuntimeError("vision timeout")

        result = self._run_with_patches(analyze)

        self.assertTrue(result.passed)
        self.assertEqual([], result.reasons)
        self.assertEqual(1.0, result.metrics.get("vision_degraded"))

    def test_ocr_empty_failure_falls_back_to_local_quality_checks(self) -> None:
        async def analyze(_url: str) -> dict:
            return {"passed": True, "risk_flags": []}

        async def ocr(_url: str) -> dict:
            return {"passed": False, "risk_flags": [], "detected_text": [], "matched_patterns": []}

        result = self._run_with_patches(analyze, ocr)

        self.assertTrue(result.passed)
        self.assertEqual([], result.reasons)
        self.assertEqual(1.0, result.metrics.get("ocr_degraded"))

    def test_ocr_text_signal_still_rejects(self) -> None:
        async def analyze(_url: str) -> dict:
            return {"passed": True, "risk_flags": []}

        async def ocr(_url: str) -> dict:
            return {
                "passed": False,
                "risk_flags": [],
                "detected_text": ["Identity card 123456789012345678"],
                "matched_patterns": ["id_number_pattern"],
            }

        result = self._run_with_patches(analyze, ocr)

        self.assertFalse(result.passed)
        self.assertEqual(["ocr_reject"], result.reasons)


if __name__ == "__main__":
    unittest.main()
