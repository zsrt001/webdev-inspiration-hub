from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from app.services.template_service import get_template_by_id


@dataclass(frozen=True)
class LocalStudioReco:
    id: str
    name: str
    city: str
    service_cities: list[str]
    tags: list[str]
    highlight: str | None
    cta_label: str
    cta_type: str
    cta_value: str | None
    service_modes: list[str]
    recommended_for: list[str]
    style_families: list[str]
    priority: int
    rush_available: bool
    match_reason: str | None = None
    ranking_factors: list[str] = field(default_factory=list)
    score: int = 0
    lead_count: int = 0


_CACHE: list[LocalStudioReco] | None = None
_CITY_SUFFIXES = (
    "特别行政区",
    "维吾尔自治区",
    "壮族自治区",
    "回族自治区",
    "自治区",
    "省",
    "市",
)
_NATIONWIDE_TOKENS = {"全国", "全网", "线上", "全国/线上", "nationwide", "remote"}


def _data_file() -> Path:
    backend_dir = Path(__file__).resolve().parents[2]
    return backend_dir / "data" / "local_studios.json"


def _normalize_city(city: str) -> str:
    text = (city or "").strip()
    for suffix in _CITY_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip()


def _normalize_token(text: str) -> str:
    return (text or "").strip().lower().replace("-", "_").replace(" ", "_")


def _parse_wedding_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except Exception:
        return None


def _template_tokens(template_id: str | None) -> set[str]:
    tokens: set[str] = set()
    template_key = _normalize_token(template_id or "")
    if template_key:
        tokens.add(template_key)

    template = get_template_by_id(template_id or "")
    if not template:
        return tokens

    for raw in (template.style_family, template.recommended_for, template.category):
        token = _normalize_token(raw or "")
        if token:
            tokens.add(token)

    for raw in template.tags or []:
        token = _normalize_token(str(raw))
        if token:
            tokens.add(token)

    if template_key.startswith("solo_"):
        tokens.add(template_key.removeprefix("solo_"))

    return tokens


def _load_all() -> list[LocalStudioReco]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    file_path = _data_file()
    if not file_path.exists():
        _CACHE = []
        return _CACHE

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        _CACHE = []
        return _CACHE

    recos: list[LocalStudioReco] = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            recos.append(
                LocalStudioReco(
                    id=str(row.get("id") or "").strip(),
                    name=str(row.get("name") or "").strip(),
                    city=str(row.get("city") or "").strip(),
                    service_cities=[str(x).strip() for x in (row.get("service_cities") or []) if str(x).strip()],
                    tags=[str(x).strip() for x in (row.get("tags") or []) if str(x).strip()],
                    highlight=(str(row.get("highlight")).strip() if row.get("highlight") else None),
                    cta_label=str(row.get("cta_label") or "Contact").strip() or "Contact",
                    cta_type=str(row.get("cta_type") or "copy").strip() or "copy",
                    cta_value=(str(row.get("cta_value")).strip() if row.get("cta_value") else None),
                    service_modes=[
                        _normalize_token(str(x))
                        for x in (row.get("service_modes") or [])
                        if _normalize_token(str(x))
                    ],
                    recommended_for=[
                        _normalize_token(str(x))
                        for x in (row.get("recommended_for") or [])
                        if _normalize_token(str(x))
                    ],
                    style_families=[
                        _normalize_token(str(x))
                        for x in (row.get("style_families") or [])
                        if _normalize_token(str(x))
                    ],
                    priority=max(0, min(100, int(row.get("priority") or 0))),
                    rush_available=bool(row.get("rush_available")),
                )
            )
        except Exception:
            continue

    _CACHE = [item for item in recos if item.id and item.name]
    return _CACHE


def recommend_local_studios(
    *,
    city: str | None,
    wedding_date: str | None = None,
    template_id: str | None = None,
    limit: int = 3,
    lead_counts: dict[str, int] | None = None,
    lead_boost_per_conversion: int = 0,
    lead_boost_cap: int = 0,
    manual_boosts: dict[str, int] | None = None,
) -> list[LocalStudioReco]:
    limit = max(1, min(10, int(limit)))
    studios = _load_all()
    if not studios:
        return []

    city_key = _normalize_city(city or "")
    template_tokens = _template_tokens(template_id)
    wedding = _parse_wedding_date(wedding_date)
    days_to_wedding = (wedding - date.today()).days if wedding else None
    normalized_lead_counts = {
        str(studio_id): max(0, int(count or 0))
        for studio_id, count in (lead_counts or {}).items()
        if str(studio_id).strip()
    }
    normalized_manual_boosts = {
        str(studio_id): int(score or 0)
        for studio_id, score in (manual_boosts or {}).items()
        if str(studio_id).strip()
    }

    ranked: list[tuple[int, LocalStudioReco]] = []
    for studio in studios:
        score = int(studio.priority or 0)
        reasons: list[str] = []
        lead_count = normalized_lead_counts.get(studio.id, 0)

        studio_city = _normalize_city(studio.city)
        service_cities = {_normalize_city(item) for item in (studio.service_cities or [])}
        is_national = studio_city in _NATIONWIDE_TOKENS

        if city_key:
            if studio_city == city_key:
                score += 120
                reasons.append("same_city")
            elif city_key in service_cities:
                score += 85
                reasons.append("service_city")
            elif is_national:
                score += 35
                reasons.append("nationwide")
            else:
                score -= 12
        elif is_national:
            score += 20
            reasons.append("nationwide")

        style_tokens = set(studio.style_families or [])
        use_case_tokens = set(studio.recommended_for or [])
        if template_tokens:
            if template_tokens & style_tokens:
                score += 28
                reasons.append("style_match")
            elif template_tokens & use_case_tokens:
                score += 20
                reasons.append("use_case_match")
            elif any(
                studio_token and template_token and (
                    studio_token in template_token or template_token in studio_token
                )
                for studio_token in (style_tokens | use_case_tokens)
                for template_token in template_tokens
            ):
                score += 14
                reasons.append("style_near_match")

        if days_to_wedding is not None:
            if days_to_wedding <= 21 and studio.rush_available:
                score += 18
                reasons.append("rush_ready")
            elif 0 <= days_to_wedding <= 60:
                score += 6
                reasons.append("near_term")

        if lead_count > 0 and lead_boost_per_conversion > 0:
            lead_boost = min(lead_boost_cap, lead_count * lead_boost_per_conversion) if lead_boost_cap > 0 else (lead_count * lead_boost_per_conversion)
            if lead_boost > 0:
                score += int(lead_boost)
                reasons.append("lead_conversion")

        manual_boost = normalized_manual_boosts.get(studio.id, 0)
        if manual_boost:
            score += int(manual_boost)
            reasons.append("manual_boost")

        match_reason = None
        if "same_city" in reasons:
            match_reason = "Same city match"
        elif "service_city" in reasons:
            match_reason = "Covers your city"
        elif "lead_conversion" in reasons:
            match_reason = "High lead conversion"
        elif "style_match" in reasons:
            match_reason = "Best style match"
        elif "use_case_match" in reasons:
            match_reason = "Best use-case match"
        elif "rush_ready" in reasons:
            match_reason = "Fast delivery option"
        elif "nationwide" in reasons:
            match_reason = "Nationwide fallback"

        ranked.append(
            (
                score,
                LocalStudioReco(
                    id=studio.id,
                    name=studio.name,
                    city=studio.city,
                    service_cities=list(studio.service_cities or []),
                    tags=list(studio.tags or []),
                    highlight=studio.highlight,
                    cta_label=studio.cta_label,
                    cta_type=studio.cta_type,
                    cta_value=studio.cta_value,
                    service_modes=list(studio.service_modes or []),
                    recommended_for=list(studio.recommended_for or []),
                    style_families=list(studio.style_families or []),
                    priority=studio.priority,
                    rush_available=studio.rush_available,
                    match_reason=match_reason,
                    ranking_factors=list(reasons),
                    score=score,
                    lead_count=lead_count,
                ),
            )
        )

    ranked.sort(key=lambda item: (-item[0], -item[1].priority, item[1].id))

    deduped: list[LocalStudioReco] = []
    seen_ids: set[str] = set()
    seen_contacts: set[str] = set()
    for _score, studio in ranked:
        if studio.id in seen_ids:
            continue
        contact_key = _normalize_token(studio.cta_value or "")
        if contact_key and contact_key in seen_contacts:
            continue
        seen_ids.add(studio.id)
        if contact_key:
            seen_contacts.add(contact_key)
        deduped.append(studio)
        if len(deduped) >= limit:
            break

    return deduped
