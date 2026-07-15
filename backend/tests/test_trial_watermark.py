"""Fail-closed trial watermark primitive tests."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import unittest
from uuid import uuid4

from PIL import Image

from app.services.postprocess_service import ValidatedPrivateImage
from app.services.trial_access_service import (
    TrialWatermarkError,
    build_trial_watermark_bytes,
)


def _candidate() -> ValidatedPrivateImage:
    image = Image.new("RGB", (1200, 1600), (82, 111, 138))
    output = BytesIO()
    image.save(output, format="JPEG", quality=94)
    payload = output.getvalue()
    return ValidatedPrivateImage(
        asset_id=uuid4(),
        image_bytes=payload,
        mime_type="image/jpeg",
        sha256=sha256(payload).hexdigest(),
    )


class TrialWatermarkTest(unittest.TestCase):
    def test_output_is_new_bounded_three_by_four_private_bytes(self) -> None:
        candidate = _candidate()

        result = build_trial_watermark_bytes(candidate)

        with Image.open(BytesIO(result.image_bytes)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.width * 4, image.height * 3)
            self.assertLessEqual(image.width, 900)
            self.assertLessEqual(image.height, 1125)
            self.assertEqual((result.width, result.height), image.size)
        self.assertNotEqual(result.sha256, candidate.sha256)
        self.assertEqual(result.parent_asset_id, candidate.asset_id)
        self.assertFalse(hasattr(result, "object_key"))
        self.assertFalse(hasattr(result, "url"))

    def test_every_watermark_stage_fails_without_returning_candidate(self) -> None:
        candidate = _candidate()

        def explode(*_args, **_kwargs):
            raise RuntimeError("injected failure")

        cases = {
            "decode": {"decoder": explode},
            "resize": {"resizer": explode},
            "font": {"font_loader": explode},
            "render": {"renderer": explode},
            "encode": {"encoder": explode},
            "post_qa": {"technical_qa": lambda _payload: False},
        }
        for stage, kwargs in cases.items():
            with self.subTest(stage=stage), self.assertRaises(TrialWatermarkError) as raised:
                build_trial_watermark_bytes(candidate, **kwargs)
            self.assertIn(stage, raised.exception.code)

if __name__ == "__main__":
    unittest.main()
