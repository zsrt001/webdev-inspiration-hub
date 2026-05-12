"""Gatekeeper service for upload safety and basic portrait quality checks."""

from __future__ import annotations

from io import BytesIO
from typing import Dict, List

import httpx
from pydantic import BaseModel

from app.core.config import get_settings
from app.services import llm_service

try:
    from PIL import Image, ImageFilter, ImageStat
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]

settings = get_settings()

_BLOCKED_FLAG_RESPONSES: dict[str, tuple[str, str]] = {
    "id_document": ("sensitive_document_upload", "Please upload a regular portrait photo, not an ID or document image."),
    "passport": ("sensitive_document_upload", "Please upload a regular portrait photo, not a passport image."),
    "driver_license": ("sensitive_document_upload", "Please upload a regular portrait photo, not a driver license image."),
    "bank_card": ("sensitive_document_upload", "Please upload a regular portrait photo, not a bank card image."),
    "payment_qr": ("payment_code_upload", "Please upload a portrait photo, not a payment or QR-code image."),
    "explicit_nudity": ("unsafe_image_content", "Please upload a safe portrait photo that follows the platform policy."),
    "minor": ("minor_related_image", "Please upload an adult portrait photo."),
}

_VISION_REASON_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("no face", "face not detected", "no human face"),
        "no_face_detected",
        "No clear face was detected. Please upload a front-facing portrait.",
    ),
    (
        ("multiple face", "multiple faces"),
        "multiple_faces_detected",
        "Please upload one clear main subject per portrait slot.",
    ),
    (
        ("side face", "profile"),
        "side_face_detected",
        "Please use a front-facing photo instead of a strong side profile.",
    ),
    (
        ("too dark", "dark", "underexposed"),
        "face_too_dark",
        "The photo is too dark. Please upload a brighter portrait.",
    ),
    (
        ("overexposed", "too bright"),
        "image_overexposed",
        "The photo is overexposed. Please use softer lighting.",
    ),
    (
        ("blur", "blurry", "out of focus"),
        "image_too_blurry",
        "The photo is too blurry. Please upload a sharper portrait.",
    ),
    (
        ("occluded", "mask", "sunglasses", "covered"),
        "face_occluded",
        "Please avoid masks, sunglasses, or anything covering the face.",
    ),
)


class GatekeeperResult(BaseModel):
    passed: bool
    reasons: List[str]
    advice: List[str]
    metrics: Dict[str, float]
    risk_flags: List[str] = []


def _build_blocked_flag_response(risk_flags: list[str], metrics: dict[str, float]) -> GatekeeperResult:
    reasons: list[str] = []
    advice: list[str] = []
    for flag in risk_flags:
        mapping = _BLOCKED_FLAG_RESPONSES.get(flag)
        if not mapping:
            continue
        reason_code, message = mapping
        if reason_code not in reasons:
            reasons.append(reason_code)
        if message not in advice:
            advice.append(message)
    if not reasons:
        reasons = ["sensitive_upload"]
        advice = ["This image looks risky. Please upload a clear everyday portrait photo."]
    return GatekeeperResult(
        passed=False,
        reasons=reasons,
        advice=advice,
        metrics=metrics,
        risk_flags=sorted(set(risk_flags)),
    )


def _normalize_vision_reject_reason(raw_reason: str | None) -> tuple[str, str]:
    normalized = (raw_reason or "").strip()
    lowered = normalized.lower()
    if lowered.startswith("vision_error:"):
        return "vision_unavailable", "Image quality service is temporarily unavailable. Please try again shortly."
    for keywords, reason_code, advice in _VISION_REASON_RULES:
        if any(keyword in lowered for keyword in keywords):
            return reason_code, advice
    return "vision_reject", normalized or "The photo did not pass the quality check. Please upload a clearer portrait."


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _compute_colorfulness(image) -> float:
    """Hasler-Suesstrunk colorfulness metric using a small RGB sample."""
    sample = image.resize((96, 96)).convert("RGB")
    pixels = list(sample.getdata())
    if not pixels:
        return 0.0

    rg_values: list[float] = []
    yb_values: list[float] = []
    for r, g, b in pixels:
        rg = abs(float(r) - float(g))
        yb = abs(0.5 * (float(r) + float(g)) - float(b))
        rg_values.append(rg)
        yb_values.append(yb)

    def _mean(vals: list[float]) -> float:
        return sum(vals) / max(1, len(vals))

    def _std(vals: list[float], mean: float) -> float:
        return (sum((x - mean) ** 2 for x in vals) / max(1, len(vals))) ** 0.5

    rg_mean = _mean(rg_values)
    yb_mean = _mean(yb_values)
    rg_std = _std(rg_values, rg_mean)
    yb_std = _std(yb_values, yb_mean)
    return (rg_std**2 + yb_std**2) ** 0.5 + 0.3 * ((rg_mean**2 + yb_mean**2) ** 0.5)


def _compute_skin_ratio(image) -> float:
    """Estimate skin-like pixels in YCbCr space as a weak document-risk signal."""
    sample = image.resize((128, 128)).convert("YCbCr")
    pixels = list(sample.getdata())
    if not pixels:
        return 0.0

    skin = 0
    for y, cb, cr in pixels:
        if 70 <= y <= 255 and 77 <= cb <= 127 and 133 <= cr <= 173:
            skin += 1
    return skin / float(len(pixels))


def _estimate_document_risk(
    image,
    *,
    width: int,
    height: int,
    brightness: float,
    edge_mean: float,
) -> tuple[bool, list[str], dict[str, float]]:
    """Conservative local heuristic for ID/passport/card-like images."""
    _ = edge_mean
    if Image is None or ImageStat is None or ImageFilter is None:
        return False, [], {}

    gray = image.convert("L")
    gray_stat = ImageStat.Stat(gray)
    contrast = float(gray_stat.stddev[0]) if gray_stat.stddev else 0.0

    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_hist = edges.histogram()
    edge_pixels = float(sum(edge_hist[52:]))
    edge_density = edge_pixels / float(max(1, width * height))

    hsv = image.convert("HSV")
    sat_mean = float(ImageStat.Stat(hsv.split()[1]).mean[0])
    colorfulness = float(_compute_colorfulness(image))
    skin_ratio = float(_compute_skin_ratio(image))

    aspect_ratio = float(max(width, height) / max(1, min(width, height)))
    is_landscape = bool(width >= height * 1.30)

    doc_like = (
        is_landscape
        and aspect_ratio >= 1.35
        and 80.0 <= brightness <= 235.0
        and sat_mean < 80.0
        and colorfulness < 28.0
        and skin_ratio < 0.06
        and edge_density > 0.055
        and contrast < 65.0
    )

    risk_flags: list[str] = []
    if doc_like:
        risk_flags.append("id_document")

    metrics = {
        "aspect_ratio": float(aspect_ratio),
        "contrast": float(contrast),
        "edge_density": float(edge_density),
        "saturation_mean": float(sat_mean),
        "colorfulness": float(colorfulness),
        "skin_ratio": float(skin_ratio),
    }
    return bool(risk_flags), risk_flags, metrics


async def check_image_quality(image_url: str) -> GatekeeperResult:
    """
    Upload gate:
    - blocks explicit safety/document risks
    - degrades provider instability to local checks
    - applies local resolution, exposure, and sharpness checks
    """
    risk_flags: list[str] = []
    ocr_metrics: dict[str, float] = {}
    if llm_service.is_vision_provider_configured():
        try:
            vision = await llm_service.analyze_face_quality(image_url)
        except Exception:
            vision = None
            ocr_metrics["vision_degraded"] = 1.0

        if not isinstance(vision, dict):
            if vision is not None:
                ocr_metrics["vision_invalid"] = 1.0
        else:
            vision_flags = vision.get("risk_flags") or []
            if isinstance(vision_flags, list):
                risk_flags.extend([str(flag).strip() for flag in vision_flags if str(flag).strip()])

            try:
                ocr = await llm_service.detect_sensitive_document_ocr(image_url)
            except Exception:
                ocr = None
                ocr_metrics["ocr_degraded"] = 1.0

            if not isinstance(ocr, dict):
                if ocr is not None:
                    ocr_metrics["ocr_invalid"] = 1.0
            else:
                ocr_flags = ocr.get("risk_flags") or []
                if isinstance(ocr_flags, list):
                    risk_flags.extend([str(flag).strip() for flag in ocr_flags if str(flag).strip()])

                ocr_text = ocr.get("detected_text") or []
                ocr_patterns = ocr.get("matched_patterns") or []
                ocr_metrics.update(
                    {
                        "ocr_text_count": float(len(ocr_text) if isinstance(ocr_text, list) else 0),
                        "ocr_pattern_count": float(len(ocr_patterns) if isinstance(ocr_patterns, list) else 0),
                    }
                )

                if not bool(ocr.get("passed", True)) and not risk_flags:
                    has_text_signal = bool(ocr_text if isinstance(ocr_text, list) else [])
                    has_pattern_signal = bool(ocr_patterns if isinstance(ocr_patterns, list) else [])
                    if has_text_signal or has_pattern_signal:
                        return GatekeeperResult(
                            passed=False,
                            reasons=["ocr_reject"],
                            advice=["Sensitive text was detected. Please upload a regular portrait photo."],
                            metrics=ocr_metrics,
                            risk_flags=sorted(set(risk_flags)),
                        )
                    ocr_metrics["ocr_degraded"] = 1.0

        blocked_flags = sorted(set(flag for flag in risk_flags if flag in _BLOCKED_FLAG_RESPONSES))
        if blocked_flags:
            return _build_blocked_flag_response(blocked_flags, ocr_metrics)

        if isinstance(vision, dict) and vision.get("passed") is False:
            reject_reason = str(vision.get("reject_reason") or "gatekeeper_reject")
            reason_code, advice = _normalize_vision_reject_reason(reject_reason)
            return GatekeeperResult(
                passed=False,
                reasons=[reason_code],
                advice=[advice],
                metrics=ocr_metrics,
                risk_flags=sorted(set(risk_flags)),
            )

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(image_url)
        response.raise_for_status()
        content = response.content

    if len(content) > 10 * 1024 * 1024:
        return GatekeeperResult(
            passed=False,
            reasons=["file_too_large"],
            advice=["The image is too large. Please upload a file under 10MB."],
            metrics={},
            risk_flags=sorted(set(risk_flags)),
        )

    if Image is None or ImageStat is None or ImageFilter is None:
        if settings.gatekeeper_allow_without_pillow:
            return GatekeeperResult(
                passed=True,
                reasons=[],
                advice=["Local image checks were skipped because Pillow is unavailable."],
                metrics={},
                risk_flags=sorted(set(risk_flags)),
            )
        return GatekeeperResult(
            passed=False,
            reasons=["local_checker_unavailable"],
            advice=["Image quality checks are temporarily unavailable. Please try again later."],
            metrics={},
            risk_flags=sorted(set(risk_flags)),
        )

    image = Image.open(BytesIO(content)).convert("RGB")

    width, height = image.size
    reasons: list[str] = []
    advice: list[str] = []

    if min(width, height) < 512:
        reasons.append("low_resolution")
        advice.append("The photo resolution is too low. Please upload a clearer portrait.")

    gray = image.convert("L")
    brightness = ImageStat.Stat(gray).mean[0]
    if brightness < 60:
        reasons.append("too_dark")
        advice.append("The photo is too dark. Please use a brighter portrait.")
    if brightness > 230:
        reasons.append("overexposed")
        advice.append("The photo is overexposed. Please use softer lighting.")

    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_mean = ImageStat.Stat(edges).mean[0]
    if edge_mean < 7:
        reasons.append("too_blurry")
        advice.append("The photo is too blurry. Please upload a sharper portrait.")

    doc_blocked, doc_flags, doc_metrics = _estimate_document_risk(
        image,
        width=width,
        height=height,
        brightness=float(brightness),
        edge_mean=float(edge_mean),
    )
    if doc_blocked:
        risk_flags.extend(doc_flags)
        if "sensitive_document_upload" not in reasons:
            reasons.append("sensitive_document_upload")
        document_advice = "This looks like a document or card image. Please upload a regular portrait photo."
        if document_advice not in advice:
            advice.append(document_advice)

    return GatekeeperResult(
        passed=len(reasons) == 0,
        reasons=reasons,
        advice=advice,
        metrics={
            "width": float(width),
            "height": float(height),
            "brightness": _clamp(float(brightness), 0.0, 255.0),
            "edge_mean": _clamp(float(edge_mean), 0.0, 255.0),
            **ocr_metrics,
            **doc_metrics,
        },
        risk_flags=sorted(set(risk_flags)),
    )
