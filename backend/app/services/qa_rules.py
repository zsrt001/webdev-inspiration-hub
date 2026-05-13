"""Local QA rules for generated outputs."""

from __future__ import annotations

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
    "extra_limbs",
    "bad_hands",
    "dress_exposure_error",
    "poor_studio_quality",
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
    "face_not_like_source": "identity_mismatch",
    "face_mismatch": "identity_mismatch",
    "identity_not_preserved": "identity_mismatch",
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
    "generic_model_face": "identity_mismatch",
    "different_person": "identity_mismatch",
    "changed_face": "identity_mismatch",
    "face_identity_changed": "identity_mismatch",
    "identity_drift": "identity_mismatch",
    "blank": "black_or_blank",
    "black_image": "black_or_blank",
    "watermark": "watermark_or_text",
    "text_overlay": "watermark_or_text",
    "artifact": "severe_artifacts",
    "invalid_llm_content": "vision_error",
}


def normalize_qa_reason(reason: str) -> str:
    key = (reason or "").strip().lower()
    if not key:
        return "other"
    normalized = QA_REASON_SYNONYMS.get(key, key)
    return normalized if normalized in ALLOWED_QA_REASONS else "other"


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
