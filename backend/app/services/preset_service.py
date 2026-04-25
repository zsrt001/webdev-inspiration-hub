"""Preset Service - curated scene/outfit reference library for Director Mode."""

from __future__ import annotations

import random
from typing import TypedDict

from app.core.config import get_settings

settings = get_settings()


class DirectorPreset(TypedDict):
    id: str
    title: str
    image_url: str  # usually a /static/... path


_OUTFIT_PRESETS: list[DirectorPreset] = [
    {"id": "outfit_classic_bw", "title": "Classic B&W", "image_url": "/static/styles/classic_bw.jpg"},
    {"id": "outfit_gothic_black", "title": "Gothic Black", "image_url": "/static/styles/gothic_romance.jpg"},
    {"id": "outfit_korean_minimal", "title": "Korean Minimal", "image_url": "/static/styles/kor_minimal.jpg"},
    {"id": "outfit_chinese_xiuhe", "title": "Chinese Xiuhe", "image_url": "/static/styles/chn_xiuhe.jpg"},
    {"id": "outfit_old_money", "title": "Old Money", "image_url": "/static/styles/old_money.jpg"},
    {"id": "outfit_cyber_wedding", "title": "Cyber Wedding", "image_url": "/static/styles/cyber_city.jpg"},
]

_SCENE_PRESETS: list[DirectorPreset] = [
    {"id": "scene_royal_castle", "title": "Royal Castle", "image_url": "/static/styles/royal_castle.jpg"},
    {"id": "scene_beach_sunset", "title": "Beach Sunset", "image_url": "/static/styles/beach_sunset.jpg"},
    {"id": "scene_twilight_forest", "title": "Twilight Forest", "image_url": "/static/styles/twilight_forest.jpg"},
    {"id": "scene_hk_retro", "title": "Hong Kong Retro", "image_url": "/static/styles/hk_retro.jpg"},
    {"id": "scene_school_days", "title": "School Days", "image_url": "/static/styles/school_days.jpg"},
    {"id": "scene_chinese_courtyard", "title": "Chinese Courtyard", "image_url": "/static/styles/golden_chinese_courtyard.jpg"},
]


def list_director_presets() -> dict[str, list[DirectorPreset]]:
    return {"outfits": list(_OUTFIT_PRESETS), "scenes": list(_SCENE_PRESETS)}


def get_outfit_preset(preset_id: str) -> DirectorPreset | None:
    return next((p for p in _OUTFIT_PRESETS if p["id"] == preset_id), None)


def get_scene_preset(preset_id: str) -> DirectorPreset | None:
    return next((p for p in _SCENE_PRESETS if p["id"] == preset_id), None)


def random_outfit_preset() -> DirectorPreset:
    return random.choice(_OUTFIT_PRESETS)


def random_scene_preset() -> DirectorPreset:
    return random.choice(_SCENE_PRESETS)


def to_public_url(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    base = settings.effective_webhook_base_url.rstrip("/")
    if not url.startswith("/"):
        url = "/" + url
    return f"{base}{url}"
