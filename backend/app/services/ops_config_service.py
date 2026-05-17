from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.schemas.template import Template

settings = get_settings()

DEFAULT_OPS_CONFIG: dict[str, Any] = {
    "template_overrides": {},
    "pricing": {
        "credit_packages": [],
    },
    "placements": {
        "home_banner": {
            "enabled": True,
            "title": "AI Wedding Studio",
            "subtitle": "Premium wedding portraits in minutes",
            "cta_label": "Start Now",
            "secondary_cta_label": "Browse Collection",
            "image_url": "/legacy_promo_banner.jpg",
            "legacy_enabled": True,
            "portal_enabled": True,
        }
    },
    "feature_flags": {
        "live_portrait": bool(settings.live_portrait_enabled),
        "remote_join": bool(settings.remote_join_enabled),
        "local_recommendations": True,
        "director_mode": True,
    },
    "recommendations": {
        "lead_lookback_days": 90,
        "lead_boost_per_conversion": 8,
        "lead_boost_cap": 48,
        "manual_boosts": {},
    },
    "crm": {
        "enabled": False,
        "webhook_url": "",
        "auth_header_name": "",
        "auth_header_value": "",
        "batch_size": 100,
    },
}


def _data_file() -> Path:
    backend_dir = Path(__file__).resolve().parents[2]
    return backend_dir / "data" / "ops_config.json"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return copy.deepcopy(DEFAULT_OPS_CONFIG)

    merged = _deep_merge(DEFAULT_OPS_CONFIG, raw)
    template_overrides = merged.get("template_overrides")
    if not isinstance(template_overrides, dict):
        merged["template_overrides"] = {}

    pricing = merged.get("pricing")
    if not isinstance(pricing, dict):
        merged["pricing"] = {"credit_packages": []}
    elif not isinstance(pricing.get("credit_packages"), list):
        pricing["credit_packages"] = []

    placements = merged.get("placements")
    if not isinstance(placements, dict):
        merged["placements"] = copy.deepcopy(DEFAULT_OPS_CONFIG["placements"])
    elif not isinstance(placements.get("home_banner"), dict):
        placements["home_banner"] = copy.deepcopy(DEFAULT_OPS_CONFIG["placements"]["home_banner"])
    else:
        placements["home_banner"] = _deep_merge(
            DEFAULT_OPS_CONFIG["placements"]["home_banner"],
            placements["home_banner"],
        )

    feature_flags = merged.get("feature_flags")
    if not isinstance(feature_flags, dict):
        merged["feature_flags"] = copy.deepcopy(DEFAULT_OPS_CONFIG["feature_flags"])
    else:
        merged["feature_flags"] = _deep_merge(DEFAULT_OPS_CONFIG["feature_flags"], feature_flags)

    recommendations = merged.get("recommendations")
    if not isinstance(recommendations, dict):
        merged["recommendations"] = copy.deepcopy(DEFAULT_OPS_CONFIG["recommendations"])
    else:
        merged["recommendations"] = _deep_merge(DEFAULT_OPS_CONFIG["recommendations"], recommendations)
        try:
            merged["recommendations"]["lead_lookback_days"] = max(
                7,
                min(365, int(merged["recommendations"].get("lead_lookback_days") or 90)),
            )
        except Exception:
            merged["recommendations"]["lead_lookback_days"] = DEFAULT_OPS_CONFIG["recommendations"]["lead_lookback_days"]
        try:
            merged["recommendations"]["lead_boost_per_conversion"] = max(
                0,
                min(50, int(merged["recommendations"].get("lead_boost_per_conversion") or 0)),
            )
        except Exception:
            merged["recommendations"]["lead_boost_per_conversion"] = DEFAULT_OPS_CONFIG["recommendations"]["lead_boost_per_conversion"]
        try:
            merged["recommendations"]["lead_boost_cap"] = max(
                0,
                min(500, int(merged["recommendations"].get("lead_boost_cap") or 0)),
            )
        except Exception:
            merged["recommendations"]["lead_boost_cap"] = DEFAULT_OPS_CONFIG["recommendations"]["lead_boost_cap"]
        manual_boosts = merged["recommendations"].get("manual_boosts")
        if not isinstance(manual_boosts, dict):
            merged["recommendations"]["manual_boosts"] = {}
        else:
            normalized_manual_boosts: dict[str, int] = {}
            for key, value in manual_boosts.items():
                studio_id = str(key or "").strip()
                if not studio_id:
                    continue
                try:
                    normalized_manual_boosts[studio_id] = max(-100, min(100, int(value or 0)))
                except Exception:
                    continue
            merged["recommendations"]["manual_boosts"] = normalized_manual_boosts

    crm = merged.get("crm")
    if not isinstance(crm, dict):
        merged["crm"] = copy.deepcopy(DEFAULT_OPS_CONFIG["crm"])
    else:
        merged["crm"] = _deep_merge(DEFAULT_OPS_CONFIG["crm"], crm)
        try:
            merged["crm"]["batch_size"] = max(1, min(500, int(merged["crm"].get("batch_size") or 100)))
        except Exception:
            merged["crm"]["batch_size"] = DEFAULT_OPS_CONFIG["crm"]["batch_size"]

    return merged


def get_ops_config() -> dict[str, Any]:
    file_path = _data_file()
    if not file_path.exists():
        return copy.deepcopy(DEFAULT_OPS_CONFIG)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(DEFAULT_OPS_CONFIG)
    return _normalize_config(raw)


def save_ops_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = _normalize_config(payload)
    file_path = _data_file()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def get_template_override(template_id: str) -> dict[str, Any]:
    config = get_ops_config()
    overrides = config.get("template_overrides") or {}
    override = overrides.get(template_id) if isinstance(overrides, dict) else None
    return override if isinstance(override, dict) else {}


def apply_template_overrides(templates: list[Template]) -> list[Template]:
    config = get_ops_config()
    overrides = config.get("template_overrides") or {}
    if not isinstance(overrides, dict):
        return list(templates)

    decorated: list[tuple[int, int, Template]] = []
    for index, template in enumerate(templates):
        override = overrides.get(template.id)
        if not isinstance(override, dict):
            decorated.append((index, index, template))
            continue
        if bool(override.get("hidden")):
            continue

        updates: dict[str, Any] = {}
        for field in (
            "title",
            "image_url",
            "marketing_title",
            "marketing_subtitle",
            "recommended_for",
            "clothing_ref_image_url",
            "scene_ref_image_url",
            "default_background_prompt",
            "clothing_prompt",
            "stability",
        ):
            value = override.get(field)
            if value is not None:
                updates[field] = value

        tags = override.get("tags")
        if isinstance(tags, list):
            updates["tags"] = [str(item).strip() for item in tags if str(item).strip()]

        sort_order_raw = override.get("sort_order")
        try:
            sort_order = int(sort_order_raw)
        except Exception:
            sort_order = index

        decorated.append((sort_order, index, template.model_copy(update=updates)))

    decorated.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in decorated]


def get_credit_package_overrides() -> list[dict[str, Any]] | None:
    config = get_ops_config()
    pricing = config.get("pricing")
    if not isinstance(pricing, dict):
        return None
    packages = pricing.get("credit_packages")
    if not isinstance(packages, list) or not packages:
        return None

    normalized: list[dict[str, Any]] = []
    for item in packages:
        if not isinstance(item, dict):
            continue
        package_id = str(item.get("id") or "").strip()
        if not package_id:
            continue
        normalized.append(
            {
                "id": package_id,
                "credits": max(1, int(item.get("credits") or 0)),
                "price": max(0.0, float(item.get("price") or 0)),
                "currency": str(item.get("currency") or "USD").strip().upper() or "USD",
                "label": str(item.get("label") or package_id).strip() or package_id,
                "popular": bool(item.get("popular")),
            }
        )
    return normalized or None


def get_public_ops_config() -> dict[str, Any]:
    config = get_ops_config()
    placements = config.get("placements") if isinstance(config.get("placements"), dict) else {}
    feature_flags = config.get("feature_flags") if isinstance(config.get("feature_flags"), dict) else {}
    home_banner = placements.get("home_banner") if isinstance(placements.get("home_banner"), dict) else {}
    image_url = str(home_banner.get("image_url") or DEFAULT_OPS_CONFIG["placements"]["home_banner"]["image_url"]).strip()
    legacy_home_images = {
        "/hero_banner.jpg",
        "/static/hero_banner.jpg",
        "/style-previews/hero_banner.jpg",
        "/static/style-previews/hero_banner.jpg",
        "/style-previews/royal_castle.jpg",
        "/static/style-previews/royal_castle.jpg",
        "/style-previews/solo_royal_castle.jpg",
        "/static/style-previews/solo_royal_castle.jpg",
        "/style-previews/couple_royal_castle.jpg",
        "/static/style-previews/couple_royal_castle.jpg",
    }
    if image_url in legacy_home_images:
        image_url = DEFAULT_OPS_CONFIG["placements"]["home_banner"]["image_url"]
    return {
        "placements": {
            "home_banner": {
                "enabled": bool(home_banner.get("enabled", True)),
                "title": str(home_banner.get("title") or DEFAULT_OPS_CONFIG["placements"]["home_banner"]["title"]).strip(),
                "subtitle": str(home_banner.get("subtitle") or DEFAULT_OPS_CONFIG["placements"]["home_banner"]["subtitle"]).strip(),
                "cta_label": str(home_banner.get("cta_label") or DEFAULT_OPS_CONFIG["placements"]["home_banner"]["cta_label"]).strip(),
                "secondary_cta_label": str(
                    home_banner.get("secondary_cta_label")
                    or DEFAULT_OPS_CONFIG["placements"]["home_banner"]["secondary_cta_label"]
                ).strip(),
                "image_url": image_url,
                "legacy_enabled": bool(home_banner.get("legacy_enabled", True)),
                "portal_enabled": bool(home_banner.get("portal_enabled", True)),
            }
        },
        "feature_flags": {
            "live_portrait": bool(
                settings.live_portrait_enabled and feature_flags.get("live_portrait", settings.live_portrait_enabled)
            ),
            "remote_join": bool(
                settings.remote_join_enabled and feature_flags.get("remote_join", settings.remote_join_enabled)
            ),
            "local_recommendations": bool(feature_flags.get("local_recommendations", True)),
            "director_mode": bool(feature_flags.get("director_mode", True)),
        },
        "auth": {
            "google_oauth_enabled": settings.supabase_oauth_enabled,
        },
    }


def get_recommendation_config() -> dict[str, Any]:
    config = get_ops_config()
    recommendations = config.get("recommendations")
    if not isinstance(recommendations, dict):
        return copy.deepcopy(DEFAULT_OPS_CONFIG["recommendations"])
    normalized = _deep_merge(DEFAULT_OPS_CONFIG["recommendations"], recommendations)
    try:
        normalized["lead_lookback_days"] = max(7, min(365, int(normalized.get("lead_lookback_days") or 90)))
    except Exception:
        normalized["lead_lookback_days"] = DEFAULT_OPS_CONFIG["recommendations"]["lead_lookback_days"]
    try:
        normalized["lead_boost_per_conversion"] = max(0, min(50, int(normalized.get("lead_boost_per_conversion") or 0)))
    except Exception:
        normalized["lead_boost_per_conversion"] = DEFAULT_OPS_CONFIG["recommendations"]["lead_boost_per_conversion"]
    try:
        normalized["lead_boost_cap"] = max(0, min(500, int(normalized.get("lead_boost_cap") or 0)))
    except Exception:
        normalized["lead_boost_cap"] = DEFAULT_OPS_CONFIG["recommendations"]["lead_boost_cap"]
    manual_boosts = normalized.get("manual_boosts")
    if not isinstance(manual_boosts, dict):
        normalized["manual_boosts"] = {}
    return normalized


def get_crm_config() -> dict[str, Any]:
    config = get_ops_config()
    crm = config.get("crm")
    if not isinstance(crm, dict):
        return copy.deepcopy(DEFAULT_OPS_CONFIG["crm"])
    normalized = _deep_merge(DEFAULT_OPS_CONFIG["crm"], crm)
    try:
        normalized["batch_size"] = max(1, min(500, int(normalized.get("batch_size") or 100)))
    except Exception:
        normalized["batch_size"] = DEFAULT_OPS_CONFIG["crm"]["batch_size"]
    return normalized
