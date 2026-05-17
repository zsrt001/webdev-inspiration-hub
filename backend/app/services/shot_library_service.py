"""Commercial wedding shot library.

Templates describe style. Shot specs describe photographer direction: camera
distance, crop, pose family, hierarchy, and what must be visible. Keeping these
separate prevents every template from collapsing into the same centered sample
pose while preserving the existing style catalog.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SHOT_LIBRARY_VERSION = "commercial_shot_library_v1"


SHOT_SPECS: dict[str, dict[str, Any]] = {
    "bridal_full_gown_editorial": {
        "label": "full gown editorial",
        "camera_distance": "full-length vertical portrait",
        "subject_height_range": [0.72, 0.84],
        "face_height_range": [0.08, 0.13],
        "headroom_range": [0.03, 0.07],
        "bottom_room_range": [0.04, 0.10],
        "pose_family": "three-quarter body turn with refined waist-level hand placement",
        "must_show": ["complete gown", "hem", "train or veil", "feet or clean bottom hem"],
        "avoid": ["flat centered ID-photo stance", "cropped hem", "cropped veil", "tiny unreadable face"],
    },
    "bridal_three_quarter_beauty": {
        "label": "three-quarter beauty portrait",
        "camera_distance": "three-quarter or knee-up portrait",
        "subject_height_range": [0.76, 0.90],
        "face_height_range": [0.11, 0.17],
        "headroom_range": [0.03, 0.06],
        "bottom_room_range": [0.03, 0.08],
        "pose_family": "soft shoulder angle, chin natural, bouquet or veil touch below chest",
        "must_show": ["face identity", "upper dress structure", "hands simplified or partly covered"],
        "avoid": ["passport-like frontal pose", "hands near face", "over-beautified generic face"],
    },
    "bridal_environmental_wide": {
        "label": "environmental wedding portrait",
        "camera_distance": "wider on-location portrait",
        "subject_height_range": [0.56, 0.72],
        "face_height_range": [0.07, 0.11],
        "headroom_range": [0.04, 0.10],
        "bottom_room_range": [0.04, 0.10],
        "pose_family": "walking pause or gentle turn with scene depth behind the subject",
        "must_show": ["subject remains dominant", "face readable", "background supports but does not overpower"],
        "avoid": ["tourist landscape photo", "background brighter than face", "subject lost in scenery"],
    },
    "couple_interaction_full_length": {
        "label": "full-length couple interaction",
        "camera_distance": "full-length two-person vertical portrait",
        "group_height_range": [0.68, 0.84],
        "group_width_range": [0.52, 0.78],
        "face_height_range": [0.06, 0.12],
        "pose_family": "slight stagger, one partner half-step forward, subtle eye-line or shoulder interaction",
        "must_show": ["both faces", "both full outfits", "separate shoulders", "separate arms", "clear role order"],
        "avoid": ["flat side-by-side tourist pose", "merged shoulders", "shared torso", "role swap"],
    },
    "couple_close_connection": {
        "label": "close couple connection",
        "camera_distance": "waist-up or three-quarter couple portrait",
        "group_height_range": [0.72, 0.90],
        "group_width_range": [0.58, 0.84],
        "face_height_range": [0.09, 0.15],
        "pose_family": "faces readable with gentle proximity, shoulders offset, hands simple and below chest",
        "must_show": ["both identities readable", "natural relationship", "no face overlap"],
        "avoid": ["faces touching or fused", "one face hidden", "same AI face for both people"],
    },
    "couple_environmental_wide": {
        "label": "environmental couple portrait",
        "camera_distance": "wider full-body couple portrait",
        "group_height_range": [0.56, 0.72],
        "group_width_range": [0.48, 0.72],
        "face_height_range": [0.055, 0.095],
        "pose_family": "walking pause or staggered editorial stance with scene layers",
        "must_show": ["both faces readable", "couple remains visual priority", "scene depth"],
        "avoid": ["travel snapshot", "tiny couple", "background dominates", "harsh backlit faces"],
    },
    "golden_anniversary_respectful_three_quarter": {
        "label": "respectful anniversary portrait",
        "camera_distance": "three-quarter or full-length elder couple portrait",
        "group_height_range": [0.66, 0.84],
        "group_width_range": [0.50, 0.78],
        "face_height_range": [0.075, 0.13],
        "pose_family": "stable dignified posture, slight inward turn, hands relaxed or gently clasped",
        "must_show": ["authentic age impression", "both faces readable", "respectful posture", "complete formal styling"],
        "avoid": ["over-young beautification", "stiff ID-photo pose", "plastic skin", "cropped hands"],
    },
}


SHOT_SUITES: dict[str, dict[str, Any]] = {
    "single_bridal": {
        "primary": "bridal_full_gown_editorial",
        "candidate_sequence": [
            "bridal_full_gown_editorial",
            "bridal_three_quarter_beauty",
            "bridal_environmental_wide",
        ],
    },
    "single_minimal": {
        "primary": "bridal_three_quarter_beauty",
        "candidate_sequence": [
            "bridal_three_quarter_beauty",
            "bridal_full_gown_editorial",
            "bridal_environmental_wide",
        ],
    },
    "couple_bridal": {
        "primary": "couple_interaction_full_length",
        "candidate_sequence": [
            "couple_interaction_full_length",
            "couple_close_connection",
            "couple_environmental_wide",
        ],
    },
    "golden_anniversary": {
        "primary": "golden_anniversary_respectful_three_quarter",
        "candidate_sequence": [
            "golden_anniversary_respectful_three_quarter",
            "couple_close_connection",
            "couple_environmental_wide",
        ],
    },
}


def _template_key(template: Any) -> str:
    return str(getattr(template, "id", "") or "").strip().lower()


def _style_key(template: Any) -> str:
    return str(getattr(template, "style_family", "") or "").strip().lower()


def resolve_shot_suite(template: Any, *, is_couple: bool) -> dict[str, Any]:
    """Return the commercial shot suite for a template and subject mode."""

    template_key = _template_key(template)
    style_key = _style_key(template)
    if "golden" in template_key or "golden" in style_key:
        suite_name = "golden_anniversary"
    elif is_couple:
        suite_name = "couple_bridal"
    elif "minimal" in template_key or "minimal" in style_key or "classic_bw" in style_key:
        suite_name = "single_minimal"
    else:
        suite_name = "single_bridal"

    suite = deepcopy(SHOT_SUITES[suite_name])
    suite["name"] = suite_name
    suite["version"] = SHOT_LIBRARY_VERSION
    suite["primary_spec"] = deepcopy(SHOT_SPECS[suite["primary"]])
    suite["candidate_specs"] = [deepcopy(SHOT_SPECS[key]) for key in suite["candidate_sequence"]]
    return suite


def commercial_shot_library_standard() -> dict[str, Any]:
    """Return the full shot library for audit and ops surfaces."""

    return {
        "version": SHOT_LIBRARY_VERSION,
        "suites": deepcopy(SHOT_SUITES),
        "specs": deepcopy(SHOT_SPECS),
    }


def _range_text(values: list[float] | None) -> str:
    if not values or len(values) < 2:
        return ""
    return f"{values[0]:.2f}-{values[1]:.2f}"


def _spec_prompt(spec: dict[str, Any], *, prefix: str) -> str:
    if "subject_height_range" in spec:
        scale = (
            f"subject height {_range_text(spec.get('subject_height_range'))} of canvas, "
            f"face height {_range_text(spec.get('face_height_range'))}, "
            f"headroom {_range_text(spec.get('headroom_range'))}, "
            f"bottom room {_range_text(spec.get('bottom_room_range'))}"
        )
    else:
        scale = (
            f"group height {_range_text(spec.get('group_height_range'))} of canvas, "
            f"group width {_range_text(spec.get('group_width_range'))}, "
            f"each face height {_range_text(spec.get('face_height_range'))}"
        )
    return (
        f"{prefix} {spec['label']}: {spec['camera_distance']}; {scale}; "
        f"pose family: {spec['pose_family']}; must show: {', '.join(spec.get('must_show') or [])}; "
        f"avoid: {', '.join(spec.get('avoid') or [])}."
    )


def build_shot_library_prompt(template: Any, *, is_couple: bool) -> str:
    """Build a prompt contract that turns a style template into a shot-directed image."""

    suite = resolve_shot_suite(template, is_couple=is_couple)
    primary = suite["primary_spec"]
    candidate_specs = suite["candidate_specs"]
    candidate_text = " ".join(
        _spec_prompt(spec, prefix=f"Candidate {index + 1}")
        for index, spec in enumerate(candidate_specs[:3])
    )
    return (
        f"SHOT LIBRARY: {SHOT_LIBRARY_VERSION}, suite {suite['name']}. "
        f"{_spec_prompt(primary, prefix='PRIMARY SHOT')} "
        "Commercial direction: use the primary shot for the final deliverable unless a candidate count greater "
        "than one is requested; in multi-candidate generation, vary candidates by shot type from the sequence "
        "below while keeping identity, role order, wardrobe concept, lighting plan, and scene style consistent. "
        f"CANDIDATE SHOT SEQUENCE: {candidate_text} "
        "Composition gate: reject or repair any output that falls outside the stated subject/group scale, makes "
        "the face unreadable, crops gown hem/train/feet/hands at awkward boundaries, or collapses into a flat "
        "centered sample pose."
    )
