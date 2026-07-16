"""Trial access, preview delivery, and download entitlement helpers."""

from __future__ import annotations

import io
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable
import uuid

from app.core.config import get_settings
from app.services.postprocess_service import ValidatedPrivateImage

class TrialWatermarkError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class WatermarkedTrialImage:
    parent_asset_id: uuid.UUID
    image_bytes: bytes
    mime_type: str
    sha256: str
    width: int
    height: int

    def __post_init__(self) -> None:
        payload = bytes(self.image_bytes)
        if not payload or self.mime_type != "image/jpeg":
            raise ValueError("invalid watermarked trial image payload")
        if sha256(payload).hexdigest() != self.sha256:
            raise ValueError("watermarked trial image checksum mismatch")
        if self.width <= 0 or self.height <= 0 or self.width * 4 != self.height * 3:
            raise ValueError("watermarked trial image must be exactly 3:4")
        if self.width > 900 or self.height > 1125:
            raise ValueError("watermarked trial image exceeds preview bounds")
        object.__setattr__(self, "image_bytes", payload)


def _decode_trial_candidate(payload: bytes):
    from PIL import Image

    with Image.open(io.BytesIO(payload)) as decoded:
        decoded.load()
        return decoded.convert("RGB")


def _resize_trial_candidate(image):
    from PIL import Image, ImageOps

    configured_width, configured_height = _trial_preview_max_size()
    max_width = min(900, configured_width)
    max_height = min(1125, configured_height)
    ratio_unit = min(
        max_width // 3,
        max_height // 4,
        image.width // 3,
        image.height // 4,
    )
    if ratio_unit < 1:
        raise ValueError("candidate is too small for a 3:4 preview")
    target = (ratio_unit * 3, ratio_unit * 4)
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    return ImageOps.fit(image.convert("RGB"), target, method=resample, centering=(0.5, 0.44))


def _load_trial_watermark_font(image):
    from PIL import ImageFont

    size = max(18, min(36, image.width // 18))
    for font_name in ("arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(font_name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_trial_watermark(image, font, text: str):
    from PIL import Image, ImageDraw

    if not text:
        raise ValueError("trial watermark text is empty")
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = max(1, bbox[2] - bbox[0])
    text_height = max(1, bbox[3] - bbox[1])
    step_x = max(text_width + 48, image.width // 2)
    step_y = max(text_height + 52, image.height // 5)
    for y in range(-step_y, image.height + step_y, step_y):
        for x in range(-step_x, image.width + step_x, step_x):
            draw.text((x, y), text, fill=(255, 255, 255, 104), font=font)
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _encode_trial_watermark(image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=82, optimize=True)
    return output.getvalue()


def _trial_watermark_technical_qa(payload: bytes) -> bool:
    from PIL import Image

    with Image.open(io.BytesIO(payload)) as image:
        image.verify()
        width, height = image.size
        return (
            image.format == "JPEG"
            and width > 0
            and height > 0
            and width * 4 == height * 3
            and width <= 900
            and height <= 1125
        )


def build_trial_watermark_bytes(
    candidate: ValidatedPrivateImage,
    *,
    decoder: Callable[[bytes], object] = _decode_trial_candidate,
    resizer: Callable[[object], object] = _resize_trial_candidate,
    font_loader: Callable[[object], object] = _load_trial_watermark_font,
    renderer: Callable[[object, object, str], object] = _render_trial_watermark,
    encoder: Callable[[object], bytes] = _encode_trial_watermark,
    technical_qa: Callable[[bytes], bool] = _trial_watermark_technical_qa,
) -> WatermarkedTrialImage:
    """Create a new bounded preview; no failure path returns candidate bytes."""

    if not isinstance(candidate, ValidatedPrivateImage):
        raise TypeError("candidate must be validated private image bytes")
    try:
        image = decoder(candidate.image_bytes)
    except Exception as exc:
        raise TrialWatermarkError("watermark_decode_failed") from exc
    try:
        resized = resizer(image)
    except Exception as exc:
        raise TrialWatermarkError("watermark_resize_failed") from exc
    try:
        font = font_loader(resized)
    except Exception as exc:
        raise TrialWatermarkError("watermark_font_failed") from exc
    try:
        rendered = renderer(resized, font, _trial_watermark_text())
    except Exception as exc:
        raise TrialWatermarkError("watermark_render_failed") from exc
    try:
        encoded = bytes(encoder(rendered))
    except Exception as exc:
        raise TrialWatermarkError("watermark_encode_failed") from exc
    if not encoded or sha256(encoded).hexdigest() == candidate.sha256:
        raise TrialWatermarkError("watermark_encode_failed")
    try:
        qa_passed = technical_qa(encoded)
    except Exception as exc:
        raise TrialWatermarkError("watermark_post_qa_failed") from exc
    if qa_passed is not True:
        raise TrialWatermarkError("watermark_post_qa_failed")
    try:
        with _decode_trial_candidate(encoded) as decoded:
            width, height = decoded.size
    except TypeError:
        decoded = _decode_trial_candidate(encoded)
        width, height = decoded.size
    except Exception as exc:
        raise TrialWatermarkError("watermark_post_qa_failed") from exc
    return WatermarkedTrialImage(
        parent_asset_id=candidate.asset_id,
        image_bytes=encoded,
        mime_type="image/jpeg",
        sha256=sha256(encoded).hexdigest(),
        width=width,
        height=height,
    )


def _trial_preview_max_size() -> tuple[int, int]:
    s = get_settings()
    return max(320, int(s.trial_preview_max_width)), max(320, int(s.trial_preview_max_height))


def _trial_watermark_text() -> str:
    return (get_settings().trial_watermark_text or "AI WEDDING STUDIO PREVIEW").strip()
