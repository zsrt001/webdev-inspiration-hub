"""QA pipeline helpers shared by local, vision, and generation services."""

from __future__ import annotations

from app.services.identity_control import classify_identity_qa
from app.services.qa_rules import normalize_qa_reason


def attach_identity_grade(payload: dict, *, is_couple: bool = False) -> dict:
    """Attach identity QA grade to a verdict-like payload."""

    payload["identity_grade"] = classify_identity_qa(
        payload.get("reasons") if isinstance(payload.get("reasons"), list) else [],
        payload.get("issues") if isinstance(payload.get("issues"), list) else [],
        is_couple=is_couple,
    )
    return payload


def blocking_vision_reasons(reasons: list[str]) -> list[str]:
    """Return every normalized vision QA failure; ambiguity fails closed."""

    blocking: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        normalized = normalize_qa_reason(reason)
        if normalized not in seen:
            seen.add(normalized)
            blocking.append(normalized)
    return blocking
