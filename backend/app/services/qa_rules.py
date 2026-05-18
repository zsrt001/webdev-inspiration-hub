"""Local QA rules for generated outputs."""

from __future__ import annotations

import re
from typing import Any

try:
    from PIL import Image, ImageFilter, ImageStat
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]


ALLOWED_QA_REASONS = {
    "headless",
    "cropped_face",
    "face_distortion",
    "fused_faces",
    "body_fusion",
    "subject_missing",
    "identity_swap",
    "identity_mismatch",
    "identity_similarity_low",
    "identity_margin_low",
    "identity_averaging",
    "identity_face_missing",
    "identity_embedding_unavailable",
    "extra_limbs",
    "bad_hands",
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
    "black_or_blank",
    "watermark_or_text",
    "nsfw",
    "severe_artifacts",
    "other",
    # Local-only operational reasons
    "low_resolution",
    "too_dark",
    "overexposed",
    "too_blurry",
    "low_contrast_or_blank",
    "vision_error",
}

SPECIFIC_LIGHTING_QA_REASONS = {
    "face_underexposed",
    "flat_lighting",
    "no_catchlights",
    "oily_skin_highlight",
    "dress_highlights_blown",
    "mixed_color_temperature",
    "poor_subject_separation",
    "background_brighter_than_face",
    "background_over_blurred",
}

_SPECIFIC_LIGHTING_TEXT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "face_underexposed",
        (
            r"\bunderexposed face\b",
            r"\bface (?:is |looks |appears )?(?:too )?dark\b",
            r"\bface (?:is |looks |appears )?(?:hidden )?in shadow\b",
            r"\bshadowed face\b",
            r"\bno soft frontal fill\b",
            r"\bmissing frontal fill\b",
        ),
    ),
    (
        "flat_lighting",
        (
            r"\bflat lighting\b",
            r"\bflat light\b",
            r"\bno directional light(?:ing)?\b",
            r"\bno key light\b",
            r"\bno key/fill/rim\b",
            r"\bshadowless\b",
            r"\b(?:lighting|light) lacks direction\b",
        ),
    ),
    (
        "no_catchlights",
        (
            r"\bno catchlights?\b",
            r"\bmissing catchlights?\b",
            r"\bdead eyes\b",
            r"\beyes? (?:have|has) no (?:visible )?catchlights?\b",
        ),
    ),
    (
        "oily_skin_highlight",
        (
            r"\boily skin\b",
            r"\bgreasy skin\b",
            r"\bglossy skin\b",
            r"\bwet glossy\b",
            r"\bwet skin\b",
            r"\bplastic shine\b",
            r"\bover[- ]?shiny\b",
            r"\bshiny (?:forehead|nose|cheeks|chin)\b",
            r"\bspecular (?:skin|face) highlights?\b",
        ),
    ),
    (
        "dress_highlights_blown",
        (
            r"\bdress highlights? (?:are )?blown\b",
            r"\bblown[- ]out (?:dress|gown|veil|lace|satin|sky|window)s?\b",
            r"\b(?:dress|gown|veil|lace|satin|sky|window) (?:is |looks |appears )?overexposed\b",
            r"\bclipped (?:dress|gown|veil|lace|satin|sky|window) highlights?\b",
            r"\bwhite dress .*?(?:loses|lost|lacks?) detail\b",
        ),
    ),
    (
        "mixed_color_temperature",
        (
            r"\bmixed color temp(?:erature)?\b",
            r"\bmixed lighting\b",
            r"\bcolor temperature (?:is )?(?:mixed|incoherent|mismatched)\b",
            r"\bgreen/orange\b",
            r"\borange/green\b",
            r"\bgreen cast\b",
            r"\borange cast\b",
            r"\bphone[- ]?flash color\b",
        ),
    ),
    (
        "poor_subject_separation",
        (
            r"\bpoor subject separation\b",
            r"\bweak subject separation\b",
            r"\bno subject separation\b",
            r"\bsubjects? blend(?:s)? into (?:the )?background\b",
            r"\bsubject(?:s)? (?:is |are )?not separated\b",
            r"\bno rim light\b",
            r"\bbackground and subject(?:s)? merge\b",
        ),
    ),
    (
        "background_brighter_than_face",
        (
            r"\bbackground (?:is |looks |appears )?brighter than (?:the )?face\b",
            r"\bwindow (?:is |looks |appears )?brighter than (?:the )?face\b",
            r"\bsky (?:is |looks |appears )?brighter than (?:the )?face\b",
            r"\bface (?:is |looks |appears )?darker than (?:the )?(?:background|window|sky)\b",
            r"\bbackground outshines (?:the )?face\b",
            r"\bexposure prioritizes (?:the )?(?:background|window|sky)\b",
        ),
    ),
    (
        "background_over_blurred",
        (
            r"\bbackground (?:is |looks |appears )?(?:too |over[- ]?)?blur(?:red|ry)\b",
            r"\bover[- ]?blurred background\b",
            r"\bbackground (?:is |looks |appears )?(?:smeared|mushy|melted)\b",
            r"\bvenue (?:is |looks |appears )?(?:unrecognizable|unreadable)\b",
            r"\bbackground (?:has )?no (?:readable )?(?:detail|structure)\b",
        ),
    ),
)

QA_REASON_SYNONYMS: dict[str, str] = {
    "cropped_head": "cropped_face",
    "face_crop": "cropped_face",
    "no_head": "headless",
    "distorted_face": "face_distortion",
    "merged_faces": "fused_faces",
    "merged_bodies": "body_fusion",
    "fused_bodies": "body_fusion",
    "shared_torso": "body_fusion",
    "overlapping_limbs": "body_fusion",
    "conjoined_bodies": "body_fusion",
    "missing_subject": "subject_missing",
    "missing_person": "subject_missing",
    "single_subject_only": "subject_missing",
    "one_subject_only": "subject_missing",
    "identity_swapped": "identity_swap",
    "swapped_identity": "identity_swap",
    "role_swap": "identity_swap",
    "roles_swapped": "identity_swap",
    "face_not_like_source": "identity_mismatch",
    "face_mismatch": "identity_mismatch",
    "identity_not_preserved": "identity_mismatch",
    "major_mismatch": "identity_mismatch",
    "major_identity_mismatch": "identity_mismatch",
    "minor_drift": "identity_mismatch",
    "minor_identity_drift": "identity_mismatch",
    "low_identity_similarity": "identity_similarity_low",
    "face_similarity_low": "identity_similarity_low",
    "embedding_similarity_low": "identity_similarity_low",
    "identity_match_margin_low": "identity_margin_low",
    "role_margin_low": "identity_margin_low",
    "same_ai_face": "identity_averaging",
    "averaged_identity": "identity_averaging",
    "identity_average": "identity_averaging",
    "source_face_missing": "identity_face_missing",
    "generated_face_missing": "identity_face_missing",
    "identity_embedding_error": "identity_embedding_unavailable",
    "embedding_unavailable": "identity_embedding_unavailable",
    "extra_arms": "extra_limbs",
    "hands_distorted": "bad_hands",
    "extra_fingers": "bad_hands",
    "bad_fingers": "bad_hands",
    "exposed_chest": "dress_exposure_error",
    "wardrobe_malfunction": "dress_exposure_error",
    "wedding_dress_exposure": "dress_exposure_error",
    "studio_quality_fail": "poor_studio_quality",
    "not_studio_quality": "poor_studio_quality",
    "low_studio_quality": "poor_studio_quality",
    "poor_quality": "poor_studio_quality",
    "ai_look": "poor_studio_quality",
    "waxy_skin": "poor_studio_quality",
    "underexposed_face": "face_underexposed",
    "face_in_shadow": "face_underexposed",
    "dark_face": "face_underexposed",
    "face_too_dark": "face_underexposed",
    "shadowed_face": "face_underexposed",
    "flat_light": "flat_lighting",
    "no_directional_light": "flat_lighting",
    "no_light_direction": "flat_lighting",
    "dead_flat_lighting": "flat_lighting",
    "missing_catchlights": "no_catchlights",
    "no_catchlight": "no_catchlights",
    "dead_eyes": "no_catchlights",
    "eyes_no_catchlight": "no_catchlights",
    "oily_skin": "oily_skin_highlight",
    "greasy_skin": "oily_skin_highlight",
    "glossy_skin": "oily_skin_highlight",
    "wet_skin": "oily_skin_highlight",
    "over_shiny_skin": "oily_skin_highlight",
    "specular_skin": "oily_skin_highlight",
    "shiny_forehead": "oily_skin_highlight",
    "shiny_nose": "oily_skin_highlight",
    "shiny_cheeks": "oily_skin_highlight",
    "blown_dress": "dress_highlights_blown",
    "blown_out_dress": "dress_highlights_blown",
    "dress_overexposed": "dress_highlights_blown",
    "white_dress_overexposed": "dress_highlights_blown",
    "wedding_dress_blown": "dress_highlights_blown",
    "gown_highlights_blown": "dress_highlights_blown",
    "mixed_color_temp": "mixed_color_temperature",
    "mixed_lighting": "mixed_color_temperature",
    "bad_color_temperature": "mixed_color_temperature",
    "color_temperature_mismatch": "mixed_color_temperature",
    "orange_green_cast": "mixed_color_temperature",
    "green_orange_cast": "mixed_color_temperature",
    "tiny_subject": "subject_too_small",
    "person_too_small": "subject_too_small",
    "subject_lost": "subject_too_small",
    "tiny_face": "face_too_small",
    "small_face": "face_too_small",
    "face_unreadable": "face_too_small",
    "background_overpowering_subject": "background_dominates",
    "background_overpowers_subject": "background_dominates",
    "excessive_sky": "excessive_headroom",
    "too_much_headroom": "excessive_headroom",
    "bad_crop": "awkward_crop",
    "bad_cropping": "awkward_crop",
    "cropped_limb": "awkward_crop",
    "joint_crop": "awkward_crop",
    "dress_cutoff": "dress_cropped",
    "cropped_dress": "dress_cropped",
    "gown_cutoff": "dress_cropped",
    "train_cutoff": "dress_cropped",
    "subject_separation_poor": "poor_subject_separation",
    "weak_subject_separation": "poor_subject_separation",
    "no_subject_separation": "poor_subject_separation",
    "subject_blends_into_background": "poor_subject_separation",
    "no_rim_light": "poor_subject_separation",
    "background_brighter": "background_brighter_than_face",
    "bright_background": "background_brighter_than_face",
    "background_too_bright": "background_brighter_than_face",
    "face_darker_than_background": "background_brighter_than_face",
    "background_outshines_face": "background_brighter_than_face",
    "background_too_blurry": "background_over_blurred",
    "over_blurred_background": "background_over_blurred",
    "overblurred_background": "background_over_blurred",
    "blurred_background": "background_over_blurred",
    "background_smeared": "background_over_blurred",
    "mushy_background": "background_over_blurred",
    "melted_background": "background_over_blurred",
    "unrecognizable_venue": "background_over_blurred",
    "centered_pose": "flat_centered_pose",
    "stiff_centered_pose": "flat_centered_pose",
    "tourist_pose": "flat_centered_pose",
    "weak_interaction": "weak_couple_interaction",
    "no_couple_interaction": "weak_couple_interaction",
    "harsh_backlit": "harsh_backlight",
    "strong_backlight": "harsh_backlight",
    "generic_model_face": "identity_mismatch",
    "different_person": "identity_mismatch",
    "changed_face": "identity_mismatch",
    "face_identity_changed": "identity_mismatch",
    "identity_drift": "identity_mismatch",
    "bad_face": "face_distortion",
    "bad_faces": "face_distortion",
    "deformed_face": "face_distortion",
    "deformed_faces": "face_distortion",
    "blank": "black_or_blank",
    "black_image": "black_or_blank",
    "watermark": "watermark_or_text",
    "text_overlay": "watermark_or_text",
    "artifact": "severe_artifacts",
    "invalid_llm_content": "vision_error",
}

QA_REASON_DETAILS: dict[str, dict[str, str | bool]] = {
    "headless": {
        "category": "face",
        "target": "head",
        "severity": "critical",
        "blocking": True,
        "repair_action": "regenerate_complete_head",
        "repair_stage": "targeted_repair",
        "repair_hint": "Restore a complete visible head and face from the identity references.",
    },
    "cropped_face": {
        "category": "composition",
        "target": "face_crop",
        "severity": "critical",
        "blocking": True,
        "repair_action": "expand_framing_restore_face",
        "repair_stage": "targeted_repair",
        "repair_hint": "Expand or repair framing so the full face remains visible and readable.",
    },
    "face_distortion": {
        "category": "face",
        "target": "face_geometry",
        "severity": "critical",
        "blocking": True,
        "repair_action": "repair_face_geometry_from_identity",
        "repair_stage": "targeted_repair",
        "repair_hint": "Repair distorted facial geometry using the original face references as the authority.",
    },
    "fused_faces": {
        "category": "face",
        "target": "multiple_faces",
        "severity": "critical",
        "blocking": True,
        "repair_action": "separate_faces",
        "repair_stage": "targeted_repair",
        "repair_hint": "Separate fused or merged faces and keep each identity independent.",
    },
    "body_fusion": {
        "category": "anatomy",
        "target": "body_separation",
        "severity": "critical",
        "blocking": True,
        "repair_action": "separate_bodies",
        "repair_stage": "targeted_repair",
        "repair_hint": "Separate bodies, shoulders, torsos, and limbs without changing identity.",
    },
    "subject_missing": {
        "category": "composition",
        "target": "subject_count",
        "severity": "critical",
        "blocking": True,
        "repair_action": "restore_missing_subject",
        "repair_stage": "targeted_repair",
        "repair_hint": "Restore the missing primary subject and preserve the requested subject count.",
    },
    "identity_swap": {
        "category": "identity",
        "target": "role_order",
        "severity": "critical",
        "blocking": True,
        "repair_action": "regenerate_from_role_locked_identity_refs",
        "repair_stage": "targeted_repair",
        "repair_hint": "Restore person A/person B role order and never swap faces or roles.",
    },
    "identity_mismatch": {
        "category": "identity",
        "target": "face_identity",
        "severity": "critical",
        "blocking": True,
        "repair_action": "regenerate_from_identity_refs",
        "repair_stage": "targeted_repair",
        "repair_hint": "Regenerate or repair from the original identity references; do not keep the wrong generated face.",
    },
    "identity_similarity_low": {
        "category": "identity",
        "target": "face_embedding_similarity",
        "severity": "critical",
        "blocking": True,
        "repair_action": "regenerate_from_identity_refs",
        "repair_stage": "targeted_repair",
        "repair_hint": "Regenerate from the original identity references because face embedding similarity is below the commercial threshold.",
    },
    "identity_margin_low": {
        "category": "identity",
        "target": "face_embedding_match_margin",
        "severity": "major",
        "blocking": True,
        "repair_action": "regenerate_from_role_locked_identity_refs",
        "repair_stage": "targeted_repair",
        "repair_hint": "Regenerate with stronger role-locked identity anchors because the correct identity match is not separated enough from alternatives.",
    },
    "identity_averaging": {
        "category": "identity",
        "target": "separate_subject_identities",
        "severity": "critical",
        "blocking": True,
        "repair_action": "regenerate_from_role_locked_identity_refs",
        "repair_stage": "targeted_repair",
        "repair_hint": "Regenerate because the output appears to average, duplicate, or collapse separate identities into one AI face.",
    },
    "identity_face_missing": {
        "category": "identity",
        "target": "face_detection",
        "severity": "critical",
        "blocking": True,
        "repair_action": "regenerate_from_identity_refs",
        "repair_stage": "targeted_repair",
        "repair_hint": "Regenerate because a required source or generated face could not be detected for identity verification.",
    },
    "identity_embedding_unavailable": {
        "category": "qa_provider",
        "target": "identity_embedding_service",
        "severity": "operational",
        "blocking": True,
        "repair_action": "defer_or_manual_review",
        "repair_stage": "manual_review",
        "repair_hint": "Identity embedding QA is required but unavailable; do not deliver automatically until the identity gate can run.",
    },
    "extra_limbs": {
        "category": "anatomy",
        "target": "limbs",
        "severity": "critical",
        "blocking": True,
        "repair_action": "repair_body_anatomy",
        "repair_stage": "targeted_repair",
        "repair_hint": "Repair severe anatomy defects and remove impossible extra limbs.",
    },
    "bad_hands": {
        "category": "anatomy",
        "target": "hands",
        "severity": "major",
        "blocking": True,
        "repair_action": "repair_hands_only",
        "repair_stage": "targeted_repair",
        "repair_hint": "Repair severe hand or finger defects without changing face, identity, or composition.",
    },
    "dress_exposure_error": {
        "category": "wardrobe",
        "target": "wedding_dress",
        "severity": "critical",
        "blocking": True,
        "repair_action": "repair_dress_coverage",
        "repair_stage": "targeted_repair",
        "repair_hint": "Repair dress coverage, fabric structure, and avoid unintended exposure.",
    },
    "poor_studio_quality": {
        "category": "photography_quality",
        "target": "lighting_and_finish",
        "severity": "major",
        "blocking": True,
        "repair_action": "upgrade_studio_lighting_and_retouch",
        "repair_stage": "final_polish",
        "repair_hint": "Upgrade broad commercial finish problems with controlled studio lighting, realistic skin texture, fabric detail, and professional color grading.",
    },
    "face_underexposed": {
        "category": "photography_quality",
        "target": "face_exposure",
        "severity": "major",
        "blocking": True,
        "repair_action": "raise_face_exposure_with_soft_fill",
        "repair_stage": "final_polish",
        "repair_hint": "Add soft frontal fill or a controlled key light so the face is correctly exposed and not hidden in shadow.",
    },
    "flat_lighting": {
        "category": "photography_quality",
        "target": "lighting_direction",
        "severity": "major",
        "blocking": True,
        "repair_action": "add_directional_key_fill_and_rim_light",
        "repair_stage": "final_polish",
        "repair_hint": "Create dimensional portrait lighting with a directional key light, weak fill, and subtle rim separation.",
    },
    "no_catchlights": {
        "category": "photography_quality",
        "target": "eye_catchlights",
        "severity": "major",
        "blocking": True,
        "repair_action": "restore_natural_eye_catchlights",
        "repair_stage": "final_polish",
        "repair_hint": "Add natural catchlights from the key or fill light while keeping eye identity and expression intact.",
    },
    "oily_skin_highlight": {
        "category": "photography_quality",
        "target": "skin_highlights",
        "severity": "major",
        "blocking": True,
        "repair_action": "reduce_specular_skin_highlights",
        "repair_stage": "final_polish",
        "repair_hint": "Remove oily, wet, or plastic facial shine and restore natural semi-matte skin texture.",
    },
    "dress_highlights_blown": {
        "category": "photography_quality",
        "target": "white_dress_highlights",
        "severity": "major",
        "blocking": True,
        "repair_action": "recover_white_dress_highlight_detail",
        "repair_stage": "final_polish",
        "repair_hint": "Recover lace, satin, veil, and gown detail so the white dress is luminous but not clipped.",
    },
    "mixed_color_temperature": {
        "category": "photography_quality",
        "target": "color_temperature",
        "severity": "major",
        "blocking": True,
        "repair_action": "unify_lighting_color_temperature",
        "repair_stage": "final_polish",
        "repair_hint": "Unify key, fill, rim, and ambient light color temperature; remove green/orange mixed-light casts.",
    },
    "subject_too_small": {
        "category": "composition",
        "target": "subject_canvas_proportion",
        "severity": "major",
        "blocking": True,
        "repair_action": "reframe_subject_larger",
        "repair_stage": "targeted_repair",
        "repair_hint": "Reframe so the person fills the commercial wedding portrait range while keeping gown and feet complete.",
    },
    "face_too_small": {
        "category": "composition",
        "target": "face_readability",
        "severity": "major",
        "blocking": True,
        "repair_action": "increase_face_readability",
        "repair_stage": "targeted_repair",
        "repair_hint": "Bring the face closer and sharper while preserving full wedding composition and identity.",
    },
    "background_dominates": {
        "category": "composition",
        "target": "subject_hierarchy",
        "severity": "major",
        "blocking": True,
        "repair_action": "reduce_background_dominance",
        "repair_stage": "targeted_repair",
        "repair_hint": "Make the couple or subject the clear visual focus; keep background supportive and less dominant.",
    },
    "excessive_headroom": {
        "category": "composition",
        "target": "headroom",
        "severity": "major",
        "blocking": True,
        "repair_action": "tighten_headroom",
        "repair_stage": "targeted_repair",
        "repair_hint": "Reduce empty space above the head to intentional commercial portrait headroom.",
    },
    "awkward_crop": {
        "category": "composition",
        "target": "crop_boundaries",
        "severity": "major",
        "blocking": True,
        "repair_action": "repair_crop_boundaries",
        "repair_stage": "targeted_repair",
        "repair_hint": "Avoid cropping at joints, fingertips, ankles, knees, wrists, face, or body edges.",
    },
    "dress_cropped": {
        "category": "wardrobe",
        "target": "gown_hem_and_train",
        "severity": "major",
        "blocking": True,
        "repair_action": "restore_full_gown_and_train",
        "repair_stage": "targeted_repair",
        "repair_hint": "Restore full gown hem, veil, and dress train with enough bottom breathing room.",
    },
    "poor_subject_separation": {
        "category": "photography_quality",
        "target": "subject_background_separation",
        "severity": "major",
        "blocking": True,
        "repair_action": "improve_subject_separation",
        "repair_stage": "final_polish",
        "repair_hint": "Use professional lighting, depth, and background control so subjects stand out clearly.",
    },
    "background_brighter_than_face": {
        "category": "photography_quality",
        "target": "face_background_exposure_balance",
        "severity": "major",
        "blocking": True,
        "repair_action": "darken_background_and_prioritize_face",
        "repair_stage": "final_polish",
        "repair_hint": "Make the face the exposure priority and keep the background slightly darker than the face.",
    },
    "background_over_blurred": {
        "category": "photography_quality",
        "target": "venue_detail_readability",
        "severity": "major",
        "blocking": True,
        "repair_action": "restore_recognizable_venue_detail",
        "repair_stage": "final_polish",
        "repair_hint": "Keep natural depth of field but restore readable architecture, studio-set, garden, floor, window, or drapery detail so the venue remains recognizable.",
    },
    "flat_centered_pose": {
        "category": "composition",
        "target": "pose_direction",
        "severity": "major",
        "blocking": True,
        "repair_action": "improve_editorial_pose",
        "repair_stage": "targeted_repair",
        "repair_hint": "Replace stiff centered tourist-photo blocking with directed commercial wedding posing.",
    },
    "weak_couple_interaction": {
        "category": "composition",
        "target": "couple_relationship",
        "severity": "major",
        "blocking": True,
        "repair_action": "improve_couple_interaction",
        "repair_stage": "targeted_repair",
        "repair_hint": "Add subtle staggered couple posing, eye-line relationship, or gentle interaction without obscuring faces.",
    },
    "harsh_backlight": {
        "category": "photography_quality",
        "target": "outdoor_lighting",
        "severity": "major",
        "blocking": True,
        "repair_action": "replace_harsh_backlight_with_controlled_fill",
        "repair_stage": "final_polish",
        "repair_hint": "Use balanced outdoor fill, correct facial exposure, catchlights, and preserved sky/dress highlights.",
    },
    "black_or_blank": {
        "category": "output_integrity",
        "target": "image_content",
        "severity": "critical",
        "blocking": True,
        "repair_action": "regenerate_clean_output",
        "repair_stage": "targeted_repair",
        "repair_hint": "Regenerate a non-blank usable image.",
    },
    "watermark_or_text": {
        "category": "output_integrity",
        "target": "watermark_text",
        "severity": "major",
        "blocking": True,
        "repair_action": "remove_text_and_watermark",
        "repair_stage": "targeted_repair",
        "repair_hint": "Remove visible watermark or unwanted text.",
    },
    "nsfw": {
        "category": "safety",
        "target": "content_safety",
        "severity": "critical",
        "blocking": True,
        "repair_action": "regenerate_safe_wedding_portrait",
        "repair_stage": "targeted_repair",
        "repair_hint": "Regenerate as a safe wedding portrait.",
    },
    "severe_artifacts": {
        "category": "output_integrity",
        "target": "artifacts",
        "severity": "critical",
        "blocking": True,
        "repair_action": "regenerate_clean_output",
        "repair_stage": "targeted_repair",
        "repair_hint": "Remove severe AI artifacts and regenerate a clean result.",
    },
    "low_resolution": {
        "category": "technical_quality",
        "target": "resolution",
        "severity": "major",
        "blocking": True,
        "repair_action": "regenerate_or_upscale",
        "repair_stage": "targeted_repair",
        "repair_hint": "Regenerate or upscale to the required delivery resolution.",
    },
    "too_dark": {
        "category": "photography_quality",
        "target": "exposure",
        "severity": "major",
        "blocking": True,
        "repair_action": "fix_exposure",
        "repair_stage": "final_polish",
        "repair_hint": "Raise facial exposure and recover readable portrait lighting.",
    },
    "overexposed": {
        "category": "photography_quality",
        "target": "highlights",
        "severity": "major",
        "blocking": True,
        "repair_action": "recover_highlights",
        "repair_stage": "final_polish",
        "repair_hint": "Recover blown highlights in face, dress, sky, or windows.",
    },
    "too_blurry": {
        "category": "technical_quality",
        "target": "sharpness",
        "severity": "major",
        "blocking": True,
        "repair_action": "restore_sharpness",
        "repair_stage": "targeted_repair",
        "repair_hint": "Restore facial and fabric sharpness without changing identity.",
    },
    "low_contrast_or_blank": {
        "category": "technical_quality",
        "target": "contrast",
        "severity": "major",
        "blocking": True,
        "repair_action": "restore_contrast",
        "repair_stage": "final_polish",
        "repair_hint": "Restore normal contrast and image readability.",
    },
    "vision_error": {
        "category": "qa_provider",
        "target": "vision_service",
        "severity": "operational",
        "blocking": False,
        "repair_action": "defer_or_manual_review",
        "repair_stage": "manual_review",
        "repair_hint": "Vision QA was unavailable; use local QA and mark for manual review if needed.",
    },
    "other": {
        "category": "unknown",
        "target": "unknown",
        "severity": "review",
        "blocking": False,
        "repair_action": "manual_review",
        "repair_stage": "manual_review",
        "repair_hint": "Review manually because the QA reason was too vague.",
    },
}


def normalize_qa_reason(reason: str) -> str:
    key = (reason or "").strip().lower()
    if not key:
        return "other"
    normalized = QA_REASON_SYNONYMS.get(key, key)
    return normalized if normalized in ALLOWED_QA_REASONS else "other"


def infer_specific_lighting_reasons(text: str) -> list[str]:
    content = str(text or "").strip().lower()
    if not content:
        return []
    inferred: list[str] = []
    seen: set[str] = set()
    for reason, patterns in _SPECIFIC_LIGHTING_TEXT_PATTERNS:
        if reason in seen:
            continue
        if any(re.search(pattern, content, flags=re.IGNORECASE) for pattern in patterns):
            seen.add(reason)
            inferred.append(reason)
    return inferred


def expand_specific_lighting_qa_reasons(
    reasons: list[str],
    *,
    evidence_texts: list[str] | None = None,
) -> list[str]:
    """Prefer actionable lighting reasons over generic poor_studio_quality."""

    normalized: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        canonical = normalize_qa_reason(str(reason))
        if canonical and canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)

    joined_evidence = "\n".join(str(text or "") for text in (evidence_texts or []) if str(text or "").strip())
    inferred = infer_specific_lighting_reasons(joined_evidence)
    if not inferred:
        return normalized

    expanded: list[str] = []
    expanded_seen: set[str] = set()
    for reason in normalized:
        if reason == "poor_studio_quality":
            continue
        if reason not in expanded_seen:
            expanded_seen.add(reason)
            expanded.append(reason)
    for reason in inferred:
        if reason not in expanded_seen:
            expanded_seen.add(reason)
            expanded.append(reason)
    return expanded


def structured_qa_issue(
    reason: str,
    *,
    source: str,
    notes: str | None = None,
    metrics: dict[str, float] | None = None,
) -> dict[str, Any]:
    canonical = normalize_qa_reason(reason)
    details = dict(QA_REASON_DETAILS.get(canonical) or QA_REASON_DETAILS["other"])
    issue: dict[str, Any] = {
        "code": canonical,
        "source": str(source or "unknown"),
        "category": str(details.get("category") or "unknown"),
        "target": str(details.get("target") or "unknown"),
        "severity": str(details.get("severity") or "review"),
        "blocking": bool(details.get("blocking")),
        "repair_action": str(details.get("repair_action") or "manual_review"),
        "repair_stage": str(details.get("repair_stage") or "manual_review"),
        "repair_hint": str(details.get("repair_hint") or ""),
    }
    if notes:
        issue["notes"] = str(notes)[:240]
    if metrics:
        issue["metrics"] = metrics
    return issue


def build_structured_qa_issues(
    reasons: list[str],
    *,
    source: str,
    notes: str | None = None,
    metrics: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reason in reasons:
        canonical = normalize_qa_reason(str(reason))
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        issues.append(
            structured_qa_issue(
                canonical,
                source=source,
                notes=notes,
                metrics=metrics if source in {"local", "identity_embedding", "photometric"} else None,
            )
        )
    return issues


def _mean(values: list[float]) -> float:
    return sum(values) / max(1, len(values))


def _std(values: list[float], mean_value: float) -> float:
    return (sum((v - mean_value) ** 2 for v in values) / max(1, len(values))) ** 0.5


def _compute_colorfulness(image: Any) -> float:
    sample = image.resize((96, 96)).convert("RGB")
    pixels = list(sample.getdata())
    if not pixels:
        return 0.0

    rg_values: list[float] = []
    yb_values: list[float] = []
    for r, g, b in pixels:
        rg_values.append(abs(float(r) - float(g)))
        yb_values.append(abs(0.5 * (float(r) + float(g)) - float(b)))

    rg_mean = _mean(rg_values)
    yb_mean = _mean(yb_values)
    rg_std = _std(rg_values, rg_mean)
    yb_std = _std(yb_values, yb_mean)
    return (rg_std**2 + yb_std**2) ** 0.5 + 0.3 * ((rg_mean**2 + yb_mean**2) ** 0.5)


def _compute_skin_ratio(image: Any) -> float:
    sample = image.resize((128, 128)).convert("YCbCr")
    pixels = list(sample.getdata())
    if not pixels:
        return 0.0
    skin = 0
    for y, cb, cr in pixels:
        if 70 <= y <= 255 and 77 <= cb <= 127 and 133 <= cr <= 173:
            skin += 1
    return skin / float(len(pixels))


def run_local_qa_rules(image: Any) -> tuple[list[str], dict[str, float]]:
    """
    Rule-library for local QA.

    Returns:
      (reasons, metrics)
    """
    if Image is None or ImageStat is None or ImageFilter is None:
        return ["qa_local_checker_unavailable"], {}

    width, height = image.size
    gray = image.convert("L")
    gray_stat = ImageStat.Stat(gray)
    brightness = float(gray_stat.mean[0]) if gray_stat.mean else 0.0
    contrast = float(gray_stat.stddev[0]) if gray_stat.stddev else 0.0

    gray_hist = gray.histogram()
    dynamic_min = next((idx for idx, count in enumerate(gray_hist) if count), 0)
    dynamic_max = next((idx for idx in range(255, -1, -1) if gray_hist[idx]), 255)
    dynamic_range = float(dynamic_max - dynamic_min)

    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_stat = ImageStat.Stat(edges)
    edge_mean = float(edge_stat.mean[0]) if edge_stat.mean else 0.0
    edge_hist = edges.histogram()
    edge_density = float(sum(edge_hist[52:])) / float(max(1, width * height))

    hsv = image.convert("HSV")
    sat_mean = float(ImageStat.Stat(hsv.split()[1]).mean[0])
    colorfulness = float(_compute_colorfulness(image))
    skin_ratio = float(_compute_skin_ratio(image))

    reasons: list[str] = []
    if min(width, height) < 640:
        reasons.append("low_resolution")

    if brightness < 12 or (dynamic_range < 18 and contrast < 8):
        reasons.append("black_or_blank")
    elif brightness < 18:
        reasons.append("too_dark")

    if brightness > 247 and contrast < 14:
        reasons.append("overexposed")

    if contrast < 7:
        reasons.append("low_contrast_or_blank")

    if edge_mean < 5.2 and contrast < 16:
        reasons.append("too_blurry")

    # Extreme close-up / cropped head proxy:
    # image is dominated by skin-tones with weak structural edges.
    if skin_ratio > 0.72 and edge_density < 0.045 and contrast < 24:
        reasons.append("cropped_face")

    # Near-blank output proxy: tiny dynamic range with almost no color/texture.
    if dynamic_range < 20 and edge_density < 0.02 and colorfulness < 8:
        reasons.append("black_or_blank")

    # Severe artifact heuristic: very low color/texture with almost no skin-like area.
    if colorfulness < 9 and sat_mean < 14 and edge_density < 0.03 and skin_ratio < 0.01:
        reasons.append("severe_artifacts")

    # Normalize + de-duplicate while preserving order.
    normalized: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        canonical = normalize_qa_reason(reason)
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)

    metrics = {
        "width": float(width),
        "height": float(height),
        "brightness": brightness,
        "contrast": contrast,
        "dynamic_range": dynamic_range,
        "edge_mean": edge_mean,
        "edge_density": edge_density,
        "saturation_mean": sat_mean,
        "colorfulness": colorfulness,
        "skin_ratio": skin_ratio,
    }
    return normalized, metrics
