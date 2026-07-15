"""Photometric QA for commercial wedding image delivery.

The checks here are deliberately measurable. Vision QA can still judge taste and
semantic issues, but this gate catches common AI-studio lighting failures before
delivery: dark faces, backgrounds stealing exposure priority, plastic skin
shine, blown white dress/window detail, flat light, harsh backlight, and mixed
color temperature.
"""

from __future__ import annotations

import base64
from io import BytesIO
from statistics import mean, pstdev
from typing import Any

import httpx

try:
    from PIL import Image, ImageStat
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]

from app.core.config import get_settings
from app.services.qa_rules import build_structured_qa_issues

settings = get_settings()


def _mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def _std(values: list[float]) -> float:
    return pstdev(values) if len(values) > 1 else 0.0


def _box(width: int, height: int, left: float, top: float, right: float, bottom: float) -> tuple[int, int, int, int]:
    x1 = max(0, min(width - 1, round(width * left)))
    y1 = max(0, min(height - 1, round(height * top)))
    x2 = max(x1 + 1, min(width, round(width * right)))
    y2 = max(y1 + 1, min(height, round(height * bottom)))
    return x1, y1, x2, y2


def _luma(pixel: tuple[int, int, int]) -> float:
    r, g, b = pixel
    return 0.2126 * float(r) + 0.7152 * float(g) + 0.0722 * float(b)


def _temperature_axis(pixel: tuple[int, int, int]) -> float:
    r, _g, b = pixel
    return float(r) - float(b)


class PhotometricQAService:
    """Local, deterministic lighting and exposure gate."""

    async def _fetch_image_bytes(self, image_url: str) -> bytes:
        raw = str(image_url or "").strip()
        if not raw:
            raise ValueError("photometric_empty_image_url")
        if raw.startswith("data:image/") and ";base64," in raw:
            return base64.b64decode(raw.split(",", 1)[1])
        if not raw.startswith(("http://", "https://")):
            raise ValueError("photometric_requires_remote_or_data_url")
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
            response = await client.get(raw)
            response.raise_for_status()
            return response.content

    async def verify_lighting(self, image_url: str, *, is_couple: bool = False) -> dict[str, Any]:
        if Image is None or ImageStat is None:
            reasons = ["vision_error"]
            return {
                "passed": False,
                "reasons": reasons,
                "issues": build_structured_qa_issues(
                    reasons,
                    source="photometric",
                    notes="photometric_checker_unavailable",
                ),
                "metrics": {},
                "source": "photometric",
                "notes": "photometric_checker_unavailable",
            }
        try:
            content = await self._fetch_image_bytes(image_url)
            with Image.open(BytesIO(content)) as image:
                rgb = image.convert("RGB")
            reasons, metrics = self.evaluate_image(rgb, is_couple=is_couple)
        except Exception as exc:
            reasons = ["vision_error"]
            return {
                "passed": False,
                "reasons": reasons,
                "issues": build_structured_qa_issues(
                    reasons,
                    source="photometric",
                    notes=f"photometric_error:{type(exc).__name__}",
                ),
                "metrics": {},
                "source": "photometric",
                "notes": f"photometric_error:{type(exc).__name__}",
            }

        return {
            "passed": not reasons,
            "reasons": reasons,
            "issues": build_structured_qa_issues(
                reasons,
                source="photometric",
                notes="photometric_threshold_failed",
                metrics=metrics,
            ),
            "metrics": metrics,
            "source": "photometric",
            "notes": "passed" if not reasons else "photometric_threshold_failed",
        }

    def evaluate_image(self, image: Any, *, is_couple: bool = False) -> tuple[list[str], dict[str, float]]:
        width, height = image.size
        sample = image.resize((min(420, width), min(560, height))).convert("RGB")
        width, height = sample.size
        face_box = _box(width, height, 0.18 if is_couple else 0.31, 0.12, 0.82 if is_couple else 0.69, 0.43)
        dress_box = _box(width, height, 0.16 if is_couple else 0.24, 0.43, 0.84 if is_couple else 0.76, 0.95)

        face_pixels = self._pixels(sample, face_box)
        dress_pixels = self._pixels(sample, dress_box)
        background_pixels = self._background_pixels(sample, face_box, dress_box)
        skin_pixels = [pixel for pixel in face_pixels if self._is_skin(pixel)]
        face_basis = skin_pixels if len(skin_pixels) >= max(40, len(face_pixels) // 18) else face_pixels
        face_lumas = [_luma(pixel) for pixel in face_basis]
        face_luma = _mean(face_lumas)
        face_contrast = _std(face_lumas)
        background_luma = _mean([_luma(pixel) for pixel in background_pixels])
        face_left_luma, face_right_luma = self._face_half_lumas(sample, face_box)
        face_half_delta = abs(face_left_luma - face_right_luma)
        white_pixels = [pixel for pixel in dress_pixels if self._is_white_fabric_candidate(pixel)]
        skin_highlights = [pixel for pixel in skin_pixels if _luma(pixel) >= 198 and max(pixel) - min(pixel) <= 96]
        clipped_white = [pixel for pixel in white_pixels if max(pixel) >= 248]
        background_highlights = [pixel for pixel in background_pixels if _luma(pixel) >= 188]

        skin_highlight_ratio = len(skin_highlights) / float(max(1, len(skin_pixels)))
        dress_clip_ratio = len(clipped_white) / float(max(1, len(white_pixels)))
        background_face_delta = background_luma - face_luma
        color_temp_delta = self._color_temp_delta(face_basis, background_highlights or white_pixels)

        reasons: list[str] = []
        if face_luma < float(settings.qa_photometric_face_luma_min):
            reasons.append("face_underexposed")
        if face_luma > float(settings.qa_photometric_face_luma_max) and skin_highlight_ratio > 0.04:
            reasons.append("oily_skin_highlight")
        if background_face_delta > float(settings.qa_photometric_background_face_delta_max):
            reasons.append("background_brighter_than_face")
        if skin_pixels and skin_highlight_ratio > float(settings.qa_photometric_skin_highlight_ratio_max):
            reasons.append("oily_skin_highlight")
        if len(white_pixels) >= max(50, len(dress_pixels) // 25) and dress_clip_ratio > float(settings.qa_photometric_dress_clip_ratio_max):
            reasons.append("dress_highlights_blown")
        if (
            face_half_delta < float(settings.qa_photometric_flat_face_delta_min)
            and face_contrast < float(settings.qa_photometric_flat_face_contrast_max)
        ):
            reasons.append("flat_lighting")
        if face_half_delta > float(settings.qa_photometric_harsh_face_delta_max) and face_luma < 132:
            reasons.append("harsh_backlight")
        if color_temp_delta > float(settings.qa_photometric_color_temp_delta_max):
            reasons.append("mixed_color_temperature")

        ordered: list[str] = []
        seen: set[str] = set()
        for reason in reasons:
            if reason not in seen:
                seen.add(reason)
                ordered.append(reason)

        metrics = {
            "width": float(width),
            "height": float(height),
            "face_luma": round(face_luma, 3),
            "face_contrast": round(face_contrast, 3),
            "background_luma": round(background_luma, 3),
            "background_face_luma_delta": round(background_face_delta, 3),
            "face_half_luma_delta": round(face_half_delta, 3),
            "skin_pixel_count": float(len(skin_pixels)),
            "skin_highlight_ratio": round(skin_highlight_ratio, 5),
            "white_fabric_pixel_count": float(len(white_pixels)),
            "dress_clip_ratio": round(dress_clip_ratio, 5),
            "color_temp_delta": round(color_temp_delta, 3),
        }
        return ordered, metrics

    @staticmethod
    def _pixels(image: Any, box: tuple[int, int, int, int]) -> list[tuple[int, int, int]]:
        return [tuple(pixel) for pixel in image.crop(box).get_flattened_data()]

    @staticmethod
    def _background_pixels(
        image: Any,
        face_box: tuple[int, int, int, int],
        dress_box: tuple[int, int, int, int],
    ) -> list[tuple[int, int, int]]:
        width, height = image.size
        fx1, fy1, fx2, fy2 = face_box
        dx1, dy1, dx2, dy2 = dress_box
        pixels = image.load()
        selected: list[tuple[int, int, int]] = []
        stride = max(1, min(width, height) // 180)
        for y in range(0, height, stride):
            for x in range(0, width, stride):
                in_face = fx1 <= x < fx2 and fy1 <= y < fy2
                in_dress = dx1 <= x < dx2 and dy1 <= y < dy2
                central_subject = width * 0.18 <= x <= width * 0.82 and height * 0.08 <= y <= height * 0.96
                if in_face or in_dress or central_subject:
                    continue
                selected.append(tuple(pixels[x, y]))
        if selected:
            return selected
        return [tuple(pixel) for pixel in image.get_flattened_data()]

    @staticmethod
    def _is_skin(pixel: tuple[int, int, int]) -> bool:
        r, g, b = pixel
        y = 0.299 * r + 0.587 * g + 0.114 * b
        cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
        cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
        return (
            58 <= y <= 245
            and 76 <= cb <= 138
            and 126 <= cr <= 188
            and cr - cb >= 8
            and r >= b + 12
            and r >= g - 12
        )

    @staticmethod
    def _is_white_fabric_candidate(pixel: tuple[int, int, int]) -> bool:
        r, g, b = pixel
        lum = _luma(pixel)
        chroma = max(pixel) - min(pixel)
        return lum >= 174 and chroma <= 44 and r >= 166 and g >= 166 and b >= 166

    def _face_half_lumas(self, image: Any, face_box: tuple[int, int, int, int]) -> tuple[float, float]:
        x1, y1, x2, y2 = face_box
        mid = max(x1 + 1, min(x2 - 1, (x1 + x2) // 2))
        left_pixels = self._pixels(image, (x1, y1, mid, y2))
        right_pixels = self._pixels(image, (mid, y1, x2, y2))
        return _mean([_luma(pixel) for pixel in left_pixels]), _mean([_luma(pixel) for pixel in right_pixels])

    @staticmethod
    def _color_temp_delta(
        face_pixels: list[tuple[int, int, int]],
        comparison_pixels: list[tuple[int, int, int]],
    ) -> float:
        if len(face_pixels) < 20 or len(comparison_pixels) < 20:
            return 0.0
        face_temp = _mean([_temperature_axis(pixel) for pixel in face_pixels])
        comparison_temp = _mean([_temperature_axis(pixel) for pixel in comparison_pixels])
        return abs(face_temp - comparison_temp)


photometric_qa_service = PhotometricQAService()
