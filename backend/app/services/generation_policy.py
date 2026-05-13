"""Provider-neutral generation policy for commercial wedding portraits."""

from __future__ import annotations

from typing import Any

from app.services.prompt_brain import build_prompt, get_negative_prompt


DEFAULT_ASPECT_RATIO = "3:4"
LEGACY_RATIO_UPGRADES = {"4:5", "3:2"}

QA_MAX_ATTEMPTS = 2
QA_RETRY_REASONS = {
    "bad_hands",
    "extra_limbs",
    "face_distortion",
    "cropped_face",
    "fused_faces",
    "body_fusion",
    "severe_artifacts",
    "dress_exposure_error",
    "poor_studio_quality",
    "identity_mismatch",
    "identity_swap",
    "subject_missing",
    "headless",
    "other",
}

COUPLE_PROMPT_GUARDRAILS = (
    "Balanced couple blocking, equal prominence for both subjects, natural hand placement, "
    "readable silhouettes, clear arm separation, symmetric spacing between bride and groom, "
    "no merged shoulders, no shared torso, both outfits fully visible, bride identity anchored to reference image 1, "
    "groom identity anchored to reference image 2, no identity averaging or face replacement"
)

COUPLE_NEGATIVE_PROMPT = (
    "fused faces, merged heads, duplicate bride, duplicate groom, shared torso, conjoined shoulders, "
    "extra bouquet, overlapping limbs, swapped identity, asymmetric couple framing, identity averaging, "
    "generic bride face, generic groom face"
)


def normalize_ratio_text(value: str | None) -> str:
    return str(value or "").strip().lower().replace("x", ":").replace("/", ":")


def resolve_generation_aspect_ratio(configured: str | None, *, is_couple: bool) -> str:
    """Resolve provider aspect ratio with product-level portrait defaults.

    Both single and couple generations are normalized to the same studio-friendly
    3:4 frame when older or landscape-oriented values are encountered.
    """
    normalized = normalize_ratio_text(configured)
    if not normalized or normalized in LEGACY_RATIO_UPGRADES:
        return DEFAULT_ASPECT_RATIO
    return str(configured or "").strip() or DEFAULT_ASPECT_RATIO


def has_couple_subjects(subject_count: int | None = None, user_images: list[str] | None = None) -> bool:
    if subject_count is not None:
        try:
            return int(subject_count) >= 2
        except Exception:
            pass
    return len([url for url in (user_images or []) if str(url or "").strip()]) >= 2


def build_studio_generation_prompt(
    *,
    template: Any,
    prompt_override: str | None,
    global_style_text: str | None,
    scene_text: str | None,
    outfit_text: str | None,
    is_couple: bool,
) -> str:
    """Build the base prompt without letting free-text override studio guardrails."""
    legacy_override = (prompt_override or "").strip()
    normalized_global_style = (global_style_text or "").strip() or None
    normalized_scene_text = (scene_text or "").strip() or None
    normalized_outfit_text = (outfit_text or "").strip() or None
    user_text = normalized_global_style or legacy_override or None

    prompt_text = build_prompt(
        template,
        user_text=user_text,
        scene_text=normalized_scene_text,
        clothing_text=normalized_outfit_text,
        is_couple=is_couple,
    )
    if is_couple:
        prompt_text = f"{prompt_text.rstrip('.')}. {COUPLE_PROMPT_GUARDRAILS}."
    return prompt_text


def build_generation_negative_prompt(*, is_couple: bool) -> str:
    negative = get_negative_prompt()
    if not is_couple:
        return negative
    return f"{negative}, {COUPLE_NEGATIVE_PROMPT}"


def qa_retry_reasons_for_mode(*, is_couple: bool) -> set[str]:
    if is_couple:
        return set(QA_RETRY_REASONS)
    return set(QA_RETRY_REASONS) - {"fused_faces", "body_fusion", "identity_swap", "subject_missing"}


def should_retry_qa(reasons: list[str], attempt: int, *, max_attempts: int = QA_MAX_ATTEMPTS) -> bool:
    if int(attempt or 0) >= int(max_attempts or 0):
        return False
    normalized = {str(reason or "").strip() for reason in reasons if str(reason or "").strip()}
    if not normalized:
        return True
    return bool(normalized & QA_RETRY_REASONS)
