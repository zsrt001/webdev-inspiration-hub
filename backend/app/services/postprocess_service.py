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
    "3x2": DeliveryVariant("print_3x2", 3 / 2, "3:2 print"),
    "3x4": DeliveryVariant("xhs_3x4", 3 / 4, "3:4 social"),
    "4x5": DeliveryVariant("portrait_4x5", 4 / 5, "4:5 portrait"),
    "9x16": DeliveryVariant("wallpaper_9x16", 9 / 16, "9:16 wallpaper"),
    "1x1": DeliveryVariant("square_1x1", 1, "1:1 square"),
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
        "postprocess_policy": "commercial_hd_face_tone_grain_smart_crop",
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
    img = _apply_studio_tone_balance(img)
    img = _reduce_oily_skin_highlights(img)
    img = _apply_face_micro_retouch(img)

    smooth = img.filter(ImageFilter.SMOOTH_MORE)
    img = Image.blend(img, smooth, 0.055)
    img = ImageEnhance.Color(img).enhance(1.04)
    img = ImageEnhance.Contrast(img).enhance(1.035)
    img = ImageEnhance.Brightness(img).enhance(1.01)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.1, percent=82, threshold=3))
    return _inject_subtle_film_grain(img)


def _apply_studio_tone_balance(image):
    from PIL import Image, ImageEnhance

    # Lift the central subject area gently while compressing harsh highlights.
    lut: list[int] = []
    for value in range(256):
        if value < 72:
            mapped = value * 1.08 + 4
        elif value > 218:
            mapped = 218 + (value - 218) * 0.78
        else:
            mapped = value
        lut.append(max(0, min(255, round(mapped))))
    balanced = image.point(lut * 3)

    fill_mask = _subject_fill_mask(image.size)
    filled = ImageEnhance.Brightness(balanced).enhance(1.045)
    filled = ImageEnhance.Contrast(filled).enhance(0.985)
    return Image.composite(filled, balanced, fill_mask)


def _reduce_oily_skin_highlights(image):
    from PIL import Image, ImageEnhance

    ycbcr = image.convert("YCbCr")
    y, cb, cr = ycbcr.split()
    skin_mask = Image.new("L", image.size, 0)
    src_y = y.load()
    src_cb = cb.load()
    src_cr = cr.load()
    dst = skin_mask.load()
    width, height = image.size
    for py in range(height):
        for px in range(width):
            lum = src_y[px, py]
            blue = src_cb[px, py]
            red = src_cr[px, py]
            if 86 <= blue <= 132 and 132 <= red <= 178 and lum >= 166:
                dst[px, py] = min(155, max(0, (lum - 150) * 2))
    face_window = _face_detail_mask(image.size, opacity=180)
    skin_mask = Image.composite(skin_mask, Image.new("L", image.size, 0), face_window)
    softened = ImageEnhance.Brightness(image).enhance(0.975)
    softened = ImageEnhance.Contrast(softened).enhance(0.985)
    return Image.composite(softened, image, skin_mask)


def _apply_face_micro_retouch(image):
    from PIL import Image, ImageEnhance, ImageFilter

    face_mask = _face_detail_mask(image.size, opacity=120)
    detail = image.filter(ImageFilter.UnsharpMask(radius=0.75, percent=72, threshold=4))
    img = Image.composite(detail, image, face_mask)

    eye_mask = _eye_detail_mask(image.size)
    eye_detail = ImageEnhance.Contrast(detail).enhance(1.04)
    eye_detail = eye_detail.filter(ImageFilter.UnsharpMask(radius=0.55, percent=95, threshold=3))
    img = Image.composite(eye_detail, img, eye_mask)

    smile_mask = _smile_polish_mask(image.size)
    smile = ImageEnhance.Brightness(img).enhance(1.018)
    smile = ImageEnhance.Color(smile).enhance(0.985)
    return Image.composite(smile, img, smile_mask)


def _inject_subtle_film_grain(image):
    from PIL import Image, ImageChops, ImageEnhance

    width, height = image.size
    noise = Image.effect_noise((width, height), 5.5).convert("L")
    noise = ImageEnhance.Contrast(noise).enhance(0.32)
    neutral = Image.new("L", image.size, 128)
    grain_delta = ImageChops.subtract(noise, neutral, scale=1.0, offset=128)
    grain_rgb = Image.merge("RGB", (grain_delta, grain_delta, grain_delta))
    return Image.blend(image, grain_rgb, 0.028)


def _face_detail_mask(size: tuple[int, int], *, opacity: int = 150):
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    left = int(width * 0.30)
    top = int(height * 0.12)
    right = int(width * 0.70)
    bottom = int(height * 0.47)
    draw.ellipse((left, top, right, bottom), fill=max(0, min(255, int(opacity))))
    return mask.filter(ImageFilter.GaussianBlur(radius=max(8, int(min(width, height) * 0.025))))


def _eye_detail_mask(size: tuple[int, int]):
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    top = int(height * 0.22)
    bottom = int(height * 0.32)
    draw.ellipse((int(width * 0.34), top, int(width * 0.48), bottom), fill=135)
    draw.ellipse((int(width * 0.52), top, int(width * 0.66), bottom), fill=135)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(4, int(min(width, height) * 0.012))))


def _smile_polish_mask(size: tuple[int, int]):
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(
        (int(width * 0.39), int(height * 0.34), int(width * 0.61), int(height * 0.43)),
        fill=42,
    )
    return mask.filter(ImageFilter.GaussianBlur(radius=max(5, int(min(width, height) * 0.016))))


def _subject_fill_mask(size: tuple[int, int]):
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    left = int(width * 0.22)
    top = int(height * 0.10)
    right = int(width * 0.78)
    bottom = int(height * 0.70)
    draw.ellipse((left, top, right, bottom), fill=142)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(18, int(min(width, height) * 0.06))))


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
    return ImageOps.fit(image, target, method=resample, centering=_smart_crop_centering(variant))


def _smart_crop_centering(variant: DeliveryVariant) -> tuple[float, float]:
    ratio = float(variant.aspect_ratio or 1)
    if ratio <= 0.6:
        return (0.5, 0.42)
    if ratio < 1:
        return (0.5, 0.44)
    if ratio > 1.2:
        return (0.5, 0.47)
    return (0.5, 0.43)


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
