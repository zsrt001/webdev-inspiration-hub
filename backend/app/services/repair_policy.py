"""Image-edit repair and candidate-selection policy."""

from __future__ import annotations

from typing import Any

from app.services.identity_control import classify_identity_qa, identity_qa_requires_forced_repair


CANDIDATE_SELECTION_POLICY = "qa_score_v1"

IDENTITY_HARD_GATE_REASONS = {
    "identity_mismatch",
    "identity_similarity_low",
    "identity_margin_low",
    "identity_averaging",
    "identity_face_missing",
    "identity_embedding_unavailable",
    "identity_swap",
    "face_distortion",
    "fused_faces",
    "subject_missing",
    "headless",
}

COMMERCIAL_HARD_GATE_REASONS = {
    "face_too_small",
    "subject_too_small",
    "background_dominates",
    "excessive_headroom",
    "awkward_crop",
    "dress_cropped",
    "poor_subject_separation",
    "background_over_blurred",
    "flat_centered_pose",
    "weak_couple_interaction",
    "harsh_backlight",
    "poor_studio_quality",
    "face_underexposed",
    "flat_lighting",
    "no_catchlights",
    "dress_highlights_blown",
    "mixed_color_temperature",
    "background_brighter_than_face",
}

LIGHTING_ONLY_REPAIR_REASONS = {
    "face_underexposed",
    "flat_lighting",
    "no_catchlights",
    "oily_skin_highlight",
    "dress_highlights_blown",
    "mixed_color_temperature",
    "poor_subject_separation",
    "background_brighter_than_face",
    "background_over_blurred",
    "harsh_backlight",
}

FINAL_POLISH_ONLY_REASONS = LIGHTING_ONLY_REPAIR_REASONS | {"poor_studio_quality"}
FINAL_DELIVERY_REPAIR_REASONS = FINAL_POLISH_ONLY_REASONS | {
    "identity_mismatch",
    "identity_similarity_low",
    "identity_margin_low",
    "identity_averaging",
    "identity_face_missing",
    "identity_swap",
    "subject_missing",
    "bad_hands",
    "extra_limbs",
    "body_fusion",
    "cropped_face",
    "awkward_crop",
    "dress_cropped",
    "subject_too_small",
    "face_too_small",
    "background_dominates",
    "excessive_headroom",
}

CANDIDATE_REASON_PENALTIES = {
    "identity_mismatch": 100,
    "identity_similarity_low": 100,
    "identity_margin_low": 90,
    "identity_averaging": 100,
    "identity_face_missing": 100,
    "identity_embedding_unavailable": 100,
    "identity_swap": 100,
    "face_distortion": 90,
    "fused_faces": 90,
    "subject_missing": 90,
    "headless": 90,
    "body_fusion": 85,
    "extra_limbs": 80,
    "bad_hands": 45,
    "face_too_small": 70,
    "subject_too_small": 60,
    "background_dominates": 50,
    "excessive_headroom": 35,
    "awkward_crop": 55,
    "dress_cropped": 55,
    "poor_subject_separation": 35,
    "background_over_blurred": 35,
    "flat_centered_pose": 30,
    "weak_couple_interaction": 30,
    "harsh_backlight": 45,
    "poor_studio_quality": 45,
    "face_underexposed": 50,
    "flat_lighting": 40,
    "no_catchlights": 35,
    "oily_skin_highlight": 35,
    "dress_highlights_blown": 45,
    "mixed_color_temperature": 40,
    "background_brighter_than_face": 45,
    "dress_exposure_error": 80,
    "black_or_blank": 100,
    "watermark_or_text": 70,
    "nsfw": 100,
    "severe_artifacts": 90,
    "vision_error": 100,
    "other": 12,
}

IMAGE_EDIT_REPAIR_SKIP_PREVIOUS_REASONS = {
    "identity_mismatch",
    "identity_similarity_low",
    "identity_margin_low",
    "identity_averaging",
    "identity_face_missing",
    "identity_embedding_unavailable",
    "identity_swap",
    "subject_missing",
    "face_distortion",
    "fused_faces",
    "body_fusion",
    "headless",
    "subject_too_small",
    "face_too_small",
    "background_dominates",
    "excessive_headroom",
    "awkward_crop",
    "dress_cropped",
    "flat_centered_pose",
    "weak_couple_interaction",
    "vision_error",
}

FINAL_ROUND_LOCAL_PHOTOMETRIC_REPAIR_REASONS = {
    "oily_skin_highlight",
    "mixed_color_temperature",
}


def image_edit_round_stage(round_number: int) -> str:
    if int(round_number) <= 1:
        return "primary_generation"
    if int(round_number) == 2:
        return "targeted_repair"
    return "final_polish"


def should_include_previous_edit_result(reasons: list[str]) -> bool:
    normalized = {str(reason or "").strip() for reason in reasons if str(reason or "").strip()}
    return not bool(normalized & IMAGE_EDIT_REPAIR_SKIP_PREVIOUS_REASONS)


def is_lighting_only_repair(reasons: list[str], *, round_number: int) -> bool:
    if int(round_number or 0) != 2:
        return False
    normalized = {str(reason or "").strip() for reason in reasons if str(reason or "").strip()}
    return bool(normalized) and normalized <= LIGHTING_ONLY_REPAIR_REASONS


def can_enter_final_polish_round(reasons: list[str]) -> bool:
    normalized = {str(reason or "").strip() for reason in reasons if str(reason or "").strip()}
    return not normalized or normalized <= FINAL_DELIVERY_REPAIR_REASONS


def image_edit_repair_mode(*, round_number: int, qa_reasons: list[str]) -> str:
    if is_lighting_only_repair(qa_reasons, round_number=round_number):
        return "relight_edit_only"
    return image_edit_round_stage(round_number)


def candidate_hard_gate_reasons(reasons: list[str], issues: list[dict[str, Any]] | None) -> list[str]:
    gate_reasons = IDENTITY_HARD_GATE_REASONS | COMMERCIAL_HARD_GATE_REASONS
    hard_gate = {
        str(reason or "").strip()
        for reason in reasons
        if str(reason or "").strip() in gate_reasons
    }
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "").strip()
        if code in gate_reasons:
            hard_gate.add(code)
    return sorted(hard_gate)


def score_candidate_verdict(
    verdict: dict[str, Any],
    *,
    round_number: int,
    candidate_index: int,
) -> dict[str, Any]:
    reasons = [str(reason) for reason in (verdict.get("reasons") or []) if str(reason or "").strip()]
    issues = [issue for issue in (verdict.get("issues") or []) if isinstance(issue, dict)]
    identity_grade = str(verdict.get("identity_grade") or "").strip() or classify_identity_qa(reasons, issues)
    hard_gate_reasons = candidate_hard_gate_reasons(reasons, issues)
    if identity_qa_requires_forced_repair(identity_grade):
        hard_gate_reasons = sorted({
            *hard_gate_reasons,
            "identity_swap" if identity_grade == "role_swap" else "identity_mismatch",
        })
    remaining_reasons = {str(reason or "").strip() for reason in reasons if str(reason or "").strip()}
    final_round_photometric_delivery = (
        int(round_number or 0) >= 3
        and bool(remaining_reasons)
        and remaining_reasons <= FINAL_ROUND_LOCAL_PHOTOMETRIC_REPAIR_REASONS
    )
    if final_round_photometric_delivery and hard_gate_reasons:
        hard_gate_reasons = [
            reason
            for reason in hard_gate_reasons
            if reason not in FINAL_ROUND_LOCAL_PHOTOMETRIC_REPAIR_REASONS
        ]
    delivery_repair = None
    if final_round_photometric_delivery:
        delivery_repair = (
            "local_oily_skin_highlight_reduction"
            if remaining_reasons == {"oily_skin_highlight"}
            else "local_photometric_finish"
        )

    score = 100.0
    if not bool(verdict.get("passed")):
        score -= 20.0
    for reason in reasons:
        score -= float(CANDIDATE_REASON_PENALTIES.get(reason, CANDIDATE_REASON_PENALTIES["other"]))
    for issue in issues:
        severity = str(issue.get("severity") or "").lower()
        if severity == "critical":
            score -= 15.0
        elif severity == "major":
            score -= 8.0
        elif severity == "minor":
            score -= 3.0
    if hard_gate_reasons:
        score -= 25.0
    if identity_grade == "minor_drift":
        score -= 18.0
    elif identity_grade in {"major_mismatch", "role_swap"}:
        score -= 100.0
    score += max(0, int(round_number) - 1) * 1.5
    score -= max(0, int(candidate_index)) * 0.01

    return {
        "policy": CANDIDATE_SELECTION_POLICY,
        "score": round(max(0.0, min(100.0, score)), 2),
        "passed": (bool(verdict.get("passed")) or final_round_photometric_delivery) and not hard_gate_reasons,
        "identity_grade": identity_grade,
        "identity_blocking": identity_qa_requires_forced_repair(identity_grade),
        "hard_gate_reasons": hard_gate_reasons,
        "reasons": reasons,
        "issue_count": len(issues),
        "delivery_repair": delivery_repair,
    }
