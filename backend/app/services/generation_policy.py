"""Provider-neutral generation policy for commercial wedding portraits."""

from __future__ import annotations

from typing import Any

from app.services.prompt_brain import build_prompt, get_negative_prompt
from app.services.shot_library_service import build_shot_library_prompt, commercial_shot_library_standard


DEFAULT_ASPECT_RATIO = "3:4"
LEGACY_RATIO_UPGRADES = {"4:5", "3:2"}
COMMERCIAL_STANDARD_VERSION = "commercial_wedding_v3"

COMMERCIAL_WEDDING_STANDARD = {
    "version": COMMERCIAL_STANDARD_VERSION,
    "single": {
        "subject_height_range": [0.74, 0.84],
        "minimum_outdoor_subject_height": 0.62,
        "face_height_range": [0.09, 0.14],
        "headroom_range": [0.03, 0.06],
        "bottom_room_range": [0.05, 0.08],
    },
    "couple": {
        "group_height_range": [0.70, 0.82],
        "group_width_range": [0.54, 0.76],
        "face_height_range": [0.07, 0.12],
        "requires_equal_scale": True,
        "requires_body_separation": True,
        "requires_subtle_interaction": True,
    },
    "background": {
        "requires_print_readable_venue_detail": True,
        "requires_natural_optical_falloff": True,
        "forbid_phone_portrait_mode_blur": True,
        "clarity_profile": "commercially_readable_not_tack_sharp",
        "requires_readable_material_texture": True,
        "requires_readable_floral_and_floor_detail": True,
    },
    "lighting": {
        "face_exposure_priority_stops": [0.3, 0.7],
        "fill_under_key_stops": [1.0, 2.0],
        "requires_subtle_rim_separation": True,
        "requires_visible_catchlights": True,
        "requires_controlled_white_dress_highlights": True,
    },
    "blocking_reasons": [
        "identity_mismatch",
        "identity_similarity_low",
        "identity_margin_low",
        "identity_averaging",
        "identity_face_missing",
        "identity_embedding_unavailable",
        "unexpected_extra_subject",
        "face_too_small",
        "subject_too_small",
        "background_dominates",
        "excessive_headroom",
        "awkward_crop",
        "dress_cropped",
        "poor_subject_separation",
        "background_brighter_than_face",
        "background_over_blurred",
        "flat_centered_pose",
        "weak_couple_interaction",
        "harsh_backlight",
        "face_underexposed",
        "flat_lighting",
        "no_catchlights",
        "oily_skin_highlight",
        "dress_highlights_blown",
        "mixed_color_temperature",
    ],
    "delivery_gate": {
        "identity_required": True,
        "vision_identity_qa_required": True,
        "text_to_image_fallback_allowed_for_identity": False,
        "process_images_customer_visible": False,
    },
    "candidate_selection": {
        "enabled": True,
        "priority_order": [
            "identity",
            "face_readability",
            "commercial_canvas_proportion",
            "crop_and_gown_integrity",
            "studio_lighting",
            "couple_relationship",
            "background_supports_subject",
        ],
    },
    "shot_library": commercial_shot_library_standard(),
}

QA_MAX_ATTEMPTS = 3
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
    "face_underexposed",
    "flat_lighting",
    "no_catchlights",
    "oily_skin_highlight",
    "dress_highlights_blown",
    "mixed_color_temperature",
    "subject_too_small",
    "face_too_small",
    "background_dominates",
    "excessive_headroom",
    "awkward_crop",
    "dress_cropped",
    "poor_subject_separation",
    "background_brighter_than_face",
    "background_over_blurred",
    "flat_centered_pose",
    "weak_couple_interaction",
    "harsh_backlight",
    "identity_mismatch",
    "identity_similarity_low",
    "identity_margin_low",
    "identity_averaging",
    "identity_face_missing",
    "identity_embedding_unavailable",
    "identity_swap",
    "subject_missing",
    "unexpected_extra_subject",
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
    prompt_text = f"{prompt_text.rstrip()}\n{build_shot_library_prompt(template, is_couple=is_couple)}"
    if is_couple:
        prompt_text = f"{prompt_text.rstrip()}\nCOUPLE ROLE GUARDRAILS: {COUPLE_PROMPT_GUARDRAILS}."
    return prompt_text


def build_generation_negative_prompt(*, is_couple: bool) -> str:
    negative = get_negative_prompt()
    if not is_couple:
        return negative
    return f"{negative}, {COUPLE_NEGATIVE_PROMPT}"


def commercial_wedding_standard() -> dict[str, Any]:
    """Return the current commercial delivery standard for audits and ops."""
    return dict(COMMERCIAL_WEDDING_STANDARD)


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
