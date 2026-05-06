"""Generated image post-processing and delivery variants."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterable

import httpx

from app.core.config import get_settings
from app.services.storage import storage_service

settings = get_settings()


@dataclass(frozen=True)
class DeliveryVariant:
    suffix: str
    aspect_ratio: float | None
    label: str


VARIANT_MAP: dict[str, DeliveryVariant] = {
    "2x3": DeliveryVariant("portrait_2x3", 2 / 3, "2:3 portrait"),
    "3x4": DeliveryVariant("xhs_3x4", 3 / 4, "3:4 social"),
    "9x16": DeliveryVariant("wallpaper_9x16", 9 / 16, "9:16 wallpaper"),
}


async def postprocess_delivery_assets(delivered_urls: Iterable[str]) -> tuple[dict[str, str], dict]:
    """Create enhanced HD assets and common crop variants for delivery."""
    raw_urls = [str(url).strip() for url in delivered_urls if str(url).strip()]
    if not raw_urls:
        return {}, {"postprocess_policy": "no_outputs"}
    if not settings.postprocess_enabled:
        return {f"image_{idx + 1}": url for idx, url in enumerate(raw_urls)}, {"postprocess_policy": "disabled"}

    final_urls: dict[str, str] = {}
    failures: list[str] = []
    selected_variants = _selected_variants()

    for index, url in enumerate(raw_urls):
        base_key = f"image_{index + 1}"
        try:
            image = await _download_image(url)
            enhanced = _enhance_master(image)
            final_urls[base_key] = _upload_image(enhanced, f"{base_key}_hd.jpg")

            for variant in selected_variants:
                cropped = _crop_to_variant(enhanced, variant)
                final_urls[f"{base_key}_{variant.suffix}"] = _upload_image(
                    cropped,
                    f"{base_key}_{variant.suffix}.jpg",
                )
        except Exception as exc:
            final_urls[base_key] = url
            failures.append(f"{base_key}:{type(exc).__name__}")

    return final_urls, {
        "postprocess_policy": "hd_enhance_upscale_color_crop",
        "upscale_factor": max(1, int(settings.postprocess_upscale_factor)),
        "max_long_edge": max(900, int(settings.postprocess_max_long_edge)),
        "variants": [variant.suffix for variant in selected_variants],
        "failures": failures,
    }


async def _download_image(image_url: str):
    from PIL import Image

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(image_url)
        response.raise_for_status()
    return Image.open(io.BytesIO(response.content)).convert("RGB")


def _enhance_master(image):
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    img = image.convert("RGB")
    img = _resize_long_edge(img, _target_master_long_edge(img))

    # Gentle background softening: preserve the central subject area and blur only the outer field.
    blurred = img.filter(ImageFilter.GaussianBlur(radius=max(1.0, min(img.size) / 360)))
    mask = _foreground_mask(img.size)
    img = ImageOps.autocontrast(Image.composite(img, blurred, mask), cutoff=0.35)

    smooth = img.filter(ImageFilter.SMOOTH_MORE)
    img = Image.blend(img, smooth, 0.08)
    img = ImageEnhance.Color(img).enhance(1.04)
    img = ImageEnhance.Contrast(img).enhance(1.035)
    img = ImageEnhance.Brightness(img).enhance(1.01)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.1, percent=82, threshold=3))
    return img


def _foreground_mask(size: tuple[int, int]):
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    left = int(width * 0.12)
    top = int(height * 0.03)
    right = int(width * 0.88)
    bottom = int(height * 0.98)
    radius = int(min(width, height) * 0.28)
    draw.rounded_rectangle((left, top, right, bottom), radius=radius, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(12, int(min(width, height) * 0.045))))


def _target_master_long_edge(image) -> int:
    current = max(image.size)
    scale = max(1, int(settings.postprocess_upscale_factor))
    max_edge = max(900, int(settings.postprocess_max_long_edge))
    return min(max_edge, max(current, current * scale))


def _resize_long_edge(image, long_edge: int):
    from PIL import Image

    width, height = image.size
    current = max(width, height)
    if current == long_edge:
        return image
    ratio = long_edge / float(current)
    size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    return image.resize(size, resample)


def _crop_to_variant(image, variant: DeliveryVariant):
    from PIL import Image, ImageOps

    if not variant.aspect_ratio:
        return image.copy()
    long_edge = max(900, int(settings.postprocess_max_long_edge))
    if variant.aspect_ratio < 1:
        target = (max(1, round(long_edge * variant.aspect_ratio)), long_edge)
    else:
        target = (long_edge, max(1, round(long_edge / variant.aspect_ratio)))
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    return ImageOps.fit(image, target, method=resample, centering=(0.5, 0.44))


def _upload_image(image, filename: str) -> str:
    output = io.BytesIO()
    quality = max(75, min(96, int(settings.postprocess_jpeg_quality)))
    image.save(output, format="JPEG", quality=quality, optimize=True)
    output.seek(0)
    return storage_service.upload_file(
        output,
        filename=filename,
        content_type="image/jpeg",
        folder="finals",
    )


def _selected_variants() -> list[DeliveryVariant]:
    raw = [item.strip().lower() for item in (settings.postprocess_variants or "").split(",")]
    variants: list[DeliveryVariant] = []
    for key in raw:
        variant = VARIANT_MAP.get(key)
        if variant and variant not in variants:
            variants.append(variant)
    return variants
