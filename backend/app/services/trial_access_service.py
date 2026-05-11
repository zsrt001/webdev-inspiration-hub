"""Trial access, preview delivery, and download entitlement helpers."""

from __future__ import annotations

import io
from typing import Iterable

import httpx

from app.core.config import get_settings
from app.services.postprocess_service import postprocess_delivery_assets
from app.services.storage import storage_service

TRIAL_ACCESS_TIER = "trial_preview"
PAID_ACCESS_TIER = "paid_download"
TRIAL_ALLOWED_MAX_CREDITS = 2


def _trial_welcome_credits() -> int:
    return max(0, int(get_settings().trial_welcome_credits))


def _trial_daily_generation_limit() -> int:
    return max(1, int(get_settings().trial_daily_generation_limit))


def _trial_preview_max_size() -> tuple[int, int]:
    s = get_settings()
    return max(320, int(s.trial_preview_max_width)), max(320, int(s.trial_preview_max_height))


def _trial_watermark_text() -> str:
    return (get_settings().trial_watermark_text or "AI WEDDING STUDIO PREVIEW").strip()


def access_tier_for_order(*, has_paid_credits: bool) -> str:
    return PAID_ACCESS_TIER if has_paid_credits else TRIAL_ACCESS_TIER


def trial_generation_allowed(
    *,
    template_category: str | None,
    is_remote_join: bool = False,
    image_count: int = 1,
    director_mode: bool = False,
    credits_cost: int = 0,
) -> bool:
    """Free starter credits are limited to one base single-subject generation."""
    if int(credits_cost or 0) > TRIAL_ALLOWED_MAX_CREDITS:
        return False
    if str(template_category or "").strip().lower() == "vintage":
        return False
    if bool(is_remote_join):
        return False
    if int(image_count or 0) >= 2:
        return False
    if bool(director_mode):
        return False
    return True


def can_download_order(generation_params: dict | None, *, has_paid_credits: bool) -> bool:
    params = generation_params if isinstance(generation_params, dict) else {}
    tier = str(params.get("access_tier") or "").strip()
    if tier == PAID_ACCESS_TIER:
        return True
    return bool(has_paid_credits)


def is_trial_order(generation_params: dict | None) -> bool:
    params = generation_params if isinstance(generation_params, dict) else {}
    return str(params.get("access_tier") or "").strip() == TRIAL_ACCESS_TIER


async def prepare_delivered_image_urls(delivered_urls: Iterable[str], *, trial_preview: bool) -> tuple[dict, dict, dict]:
    """Return preview/final URL dicts plus metadata for generated assets."""
    final_urls, postprocess_meta = await postprocess_delivery_assets(delivered_urls)
    if not trial_preview:
        return final_urls, final_urls, {"preview_policy": "paid_original", **postprocess_meta}

    preview_urls: dict[str, str] = {}
    failures: list[str] = []
    for key, url in final_urls.items():
        try:
            preview_urls[key] = await _create_watermarked_preview(url, key=key)
        except Exception as exc:
            preview_urls[key] = url
            failures.append(f"{key}:{type(exc).__name__}")

    return preview_urls, final_urls, {
        "preview_policy": "trial_watermarked_lowres",
        "preview_watermark": _trial_watermark_text(),
        "preview_failures": failures,
        **postprocess_meta,
    }


async def _create_watermarked_preview(image_url: str, *, key: str) -> str:
    from PIL import Image, ImageDraw, ImageFont

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(image_url)
        response.raise_for_status()
        image_bytes = response.content

    with Image.open(io.BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        image.thumbnail(_trial_preview_max_size())
        overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        font = _load_watermark_font(image)
        text = _trial_watermark_text()
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = max(1, bbox[2] - bbox[0])
        text_height = max(1, bbox[3] - bbox[1])
        step_x = max(text_width + 90, image.width // 2)
        step_y = max(text_height + 90, image.height // 4)

        for y in range(-step_y, image.height + step_y, step_y):
            for x in range(-step_x, image.width + step_x, step_x):
                draw.text((x, y), text, fill=(255, 255, 255, 82), font=font)

        watermarked = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        output = io.BytesIO()
        watermarked.save(output, format="JPEG", quality=82, optimize=True)
        output.seek(0)

    return storage_service.upload_file(
        output,
        filename=f"{key}_preview.jpg",
        content_type="image/jpeg",
        folder="previews",
    )


def _load_watermark_font(image) -> object:
    from PIL import ImageFont

    size = max(18, min(34, image.width // 22))
    for font_name in ("arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(font_name, size=size)
        except Exception:
            continue
    return ImageFont.load_default()
