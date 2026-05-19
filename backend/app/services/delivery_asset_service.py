"""Customer-facing delivery asset selection helpers."""

from __future__ import annotations

from typing import Any


MASTER_IMAGE_KEY = "image_1"
DEFAULT_OUTPUT_ASPECT_RATIO = "portrait_3_4"
DEFAULT_OUTPUT_ASPECT_RATIO_LABEL = "3:4 portrait"

DELIVERY_VARIANT_SUFFIXES = (
    "portrait_2x3",
    "print_3x2",
    "xhs_3x4",
    "portrait_4x5",
    "wallpaper_9x16",
    "square_1x1",
)

DELIVERY_VARIANT_LABELS = {
    "portrait_2x3": "2:3 portrait crop",
    "print_3x2": "3:2 print crop",
    "xhs_3x4": "3:4 social crop",
    "portrait_4x5": "4:5 portrait crop",
    "wallpaper_9x16": "9:16 wallpaper crop",
    "square_1x1": "1:1 square crop",
}


def normalize_url_map(urls: Any) -> dict[str, str]:
    if not isinstance(urls, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in urls.items():
        clean_key = str(key or "").strip()
        clean_value = str(value or "").strip()
        if clean_key and clean_value:
            normalized[clean_key] = clean_value
    return normalized


def is_delivery_variant_key(key: str) -> bool:
    clean_key = str(key or "")
    return any(suffix in clean_key for suffix in DELIVERY_VARIANT_SUFFIXES)


def pick_master_image_url(urls: Any) -> str | None:
    normalized = normalize_url_map(urls)
    if not normalized:
        return None
    if normalized.get(MASTER_IMAGE_KEY):
        return normalized[MASTER_IMAGE_KEY]
    for key, value in normalized.items():
        if not is_delivery_variant_key(key):
            return value
    return next(iter(normalized.values()), None)


def build_download_variants(urls: Any) -> list[dict[str, str]]:
    normalized = normalize_url_map(urls)
    variants: list[dict[str, str]] = []
    for key, url in normalized.items():
        if key == MASTER_IMAGE_KEY:
            continue
        matched = next((suffix for suffix in DELIVERY_VARIANT_SUFFIXES if suffix in key), "")
        if not matched:
            continue
        variants.append(
            {
                "key": key,
                "url": url,
                "label": DELIVERY_VARIANT_LABELS.get(matched, matched),
                "type": "download_crop",
            }
        )
    return variants
