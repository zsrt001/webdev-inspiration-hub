"""Strict private-asset gatekeeper contract tests."""

from __future__ import annotations

from io import BytesIO
import inspect
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import ANY, AsyncMock, patch

from PIL import Image, ImageDraw

from app.routers import gatekeeper as gatekeeper_router
from app.services import gatekeeper_service


def _sharp_test_image_bytes(*, size: int = 800) -> bytes:
    image = Image.new("RGB", (size, size), (175, 175, 175))
    draw = ImageDraw.Draw(image)
    step = max(10, size // 40)
    for pos in range(0, size, step):
        draw.line((pos, 0, pos, size - 1), fill=(35, 35, 35), width=2)
        draw.line((0, pos, size - 1, pos), fill=(35, 35, 35), width=2)
    draw.ellipse(
        (int(size * 0.35), int(size * 0.15), int(size * 0.65), int(size * 0.45)),
        fill=(225, 178, 145),
        outline=(40, 40, 40),
        width=4,
    )
    draw.rectangle(
        (int(size * 0.3), int(size * 0.45), int(size * 0.7), int(size * 0.9)),
        fill=(245, 245, 245),
        outline=(45, 45, 45),
        width=4,
    )
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _vision_pass() -> dict:
    return {
        "passed": True,
        "reject_reason": None,
        "gender": "f",
        "risk_flags": [],
    }


def _ocr_pass() -> dict:
    return {
        "passed": True,
        "risk_flags": [],
        "detected_text": [],
        "matched_patterns": [],
        "notes": "checked",
    }


class GatekeeperServiceTest(unittest.IsolatedAsyncioTestCase):
    async def _run(
        self,
        *,
        vision_result: object = None,
        ocr_result: object = None,
        vision_error: Exception | None = None,
        image_bytes: bytes | None = None,
        configured: bool = True,
    ):
        owner_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        asset = SimpleNamespace(id=asset_id)
        private = SimpleNamespace(
            asset=asset,
            content=image_bytes or _sharp_test_image_bytes(),
            mime_type="image/jpeg",
        )
        grant = SimpleNamespace(
            grant=SimpleNamespace(id=uuid.uuid4()),
            read_url="https://api.example.com/api/v1/media/grants/secret-token",
        )
        analyze = AsyncMock(
            side_effect=vision_error,
            return_value=_vision_pass() if vision_result is None else vision_result,
        )
        ocr = AsyncMock(return_value=_ocr_pass() if ocr_result is None else ocr_result)
        revoke = AsyncMock()
        create = AsyncMock(return_value=grant)
        load = AsyncMock(return_value=private)
        with (
            patch.object(
                gatekeeper_service.llm_service,
                "is_vision_provider_configured",
                return_value=configured,
            ),
            patch.object(gatekeeper_service.llm_service, "analyze_face_quality", analyze),
            patch.object(gatekeeper_service.llm_service, "detect_sensitive_document_ocr", ocr),
            patch.object(gatekeeper_service, "load_owned_asset_bytes", load),
            patch.object(gatekeeper_service, "create_provider_grant", create),
            patch.object(gatekeeper_service, "revoke_provider_grant", revoke),
        ):
            verdict = await gatekeeper_service.check_image_quality(
                object(),
                owner_user_id=owner_id,
                asset_id=asset_id,
            )
        return verdict, analyze, ocr, create, revoke, load, grant

    async def test_provider_outage_is_blocking_and_grant_is_revoked(self) -> None:
        verdict, _analyze, ocr, _create, revoke, _load, grant = await self._run(
            vision_error=RuntimeError("provider timeout")
        )

        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reasons, ["vision_unavailable"])
        ocr.assert_not_awaited()
        revoke.assert_awaited_once_with(ANY, grant.grant)

    async def test_invalid_provider_schema_is_blocking(self) -> None:
        verdict, *_ = await self._run(
            vision_result={"passed": "yes", "risk_flags": []},
        )

        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reasons, ["vision_schema_invalid"])

    async def test_ocr_outage_cannot_pass_through_local_fallback(self) -> None:
        broken_ocr = {
            "passed": False,
            "risk_flags": [],
            "detected_text": [],
            "matched_patterns": [],
            "notes": "ocr_error:TimeoutError",
        }
        verdict, *_ = await self._run(ocr_result=broken_ocr)

        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reasons, ["safety_check_unavailable"])

    async def test_sensitive_flag_blocks_and_never_returns_pass(self) -> None:
        vision = _vision_pass()
        vision.update({"passed": False, "reject_reason": "document", "risk_flags": ["passport"]})
        verdict, *_ = await self._run(vision_result=vision)

        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reasons, ["sensitive_document_upload"])
        self.assertEqual(verdict.risk_flags, ["passport"])

    async def test_owner_checked_bytes_and_short_lived_grant_are_the_only_inputs(self) -> None:
        verdict, analyze, ocr, create, revoke, load, grant = await self._run()

        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.reasons, [])
        analyze.assert_awaited_once_with(grant.read_url)
        ocr.assert_awaited_once_with(grant.read_url)
        create.assert_awaited_once()
        revoke.assert_awaited_once()
        load.assert_awaited_once()
        self.assertNotIn("image_url", inspect.signature(gatekeeper_service.check_image_quality).parameters)
        self.assertIn("asset_id", gatekeeper_router.GatekeeperRequest.model_fields)
        self.assertNotIn("image_url", gatekeeper_router.GatekeeperRequest.model_fields)


if __name__ == "__main__":
    unittest.main()
