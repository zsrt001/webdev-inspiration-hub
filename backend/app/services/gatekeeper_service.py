"""Gatekeeper service for upload safety and basic portrait quality checks."""

from __future__ import annotations

from io import BytesIO
from typing import Dict, List
import uuid

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services import llm_service
from app.services.media_asset_service import (
    create_provider_grant,
    load_owned_asset_bytes,
    revoke_provider_grant,
)

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
        ("face too small", "tiny face", "small face"),
        "face_too_small",
        "A closer portrait is recommended so the face is easier to preserve.",
    ),
    (
        ("low resolution", "low-res", "resolution too low"),
        "low_resolution",
        "A higher-resolution portrait is recommended.",
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
    warnings: List[str] = []
    warning_advice: List[str] = []


_QUALITY_WARNING_RESPONSES: dict[str, str] = {
    "low_resolution": "This photo may reduce detail. A higher-resolution portrait is recommended, but you can continue.",
    "too_dark": "This photo is a bit dark and may reduce likeness. Brighter front light is recommended, but you can continue.",
    "overexposed": "This photo is very bright and may lose facial detail. Softer lighting is recommended, but you can continue.",
    "too_blurry": "This photo may be soft. A sharper face or upper-body portrait is recommended, but you can continue.",
    "face_too_small": "The face may be small. A closer portrait is recommended, but you can continue.",
    "not_frontal": "A front-facing portrait gives better likeness. You can continue if this is the best available photo.",
}


def _add_warning(warnings: list[str], advice: list[str], code: str, message: str | None = None) -> None:
    if code not in warnings:
        warnings.append(code)
    warning_message = message or _QUALITY_WARNING_RESPONSES.get(code)
    if warning_message and warning_message not in advice:
        advice.append(warning_message)


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


def _crop_ratio(image, box: tuple[float, float, float, float]):
    width, height = image.size
    left, top, right, bottom = box
    return image.crop(
        (
            int(width * left),
            int(height * top),
            max(int(width * right), int(width * left) + 1),
            max(int(height * bottom), int(height * top) + 1),
        )
    )


def _sharpness_metrics(image) -> dict[str, float]:
    gray = image.convert("L")
    contrast = float(ImageStat.Stat(gray).stddev[0])
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_mean = float(ImageStat.Stat(edges).mean[0])
    return {"edge_mean": edge_mean, "contrast": contrast}


def _portrait_roi_sharpness_metrics(image) -> dict[str, float]:
    """Prefer likely face/upper-body sharpness over whole-image background edges."""
    face_roi = _crop_ratio(image, (0.25, 0.08, 0.75, 0.42))
    upper_body_roi = _crop_ratio(image, (0.18, 0.08, 0.82, 0.68))
    whole = _sharpness_metrics(image)
    face = _sharpness_metrics(face_roi)
    upper = _sharpness_metrics(upper_body_roi)
    return {
        "edge_mean": whole["edge_mean"],
        "contrast": whole["contrast"],
        "face_roi_edge_mean": face["edge_mean"],
        "face_roi_contrast": face["contrast"],
        "upper_body_edge_mean": upper["edge_mean"],
        "upper_body_contrast": upper["contrast"],
        "portrait_roi_edge_mean": max(face["edge_mean"], upper["edge_mean"]),
        "portrait_roi_contrast": max(face["contrast"], upper["contrast"]),
    }


def _compute_colorfulness(image) -> float:
    """Hasler-Suesstrunk colorfulness metric using a small RGB sample."""
    sample = image.resize((96, 96)).convert("RGB")
    pixels = list(sample.get_flattened_data())
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
    pixels = list(sample.get_flattened_data())
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


def _provider_failure(code: str, message: str) -> GatekeeperResult:
    return GatekeeperResult(
        passed=False,
        reasons=[code],
        advice=[message],
        metrics={},
        risk_flags=[],
    )


def _valid_vision_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if not isinstance(payload.get("passed"), bool):
        return False
    if payload.get("reject_reason") is not None and not isinstance(
        payload.get("reject_reason"), str
    ):
        return False
    if payload.get("gender") not in {"m", "f"}:
        return False
    flags = payload.get("risk_flags")
    return isinstance(flags, list) and all(isinstance(flag, str) for flag in flags)


def _valid_ocr_payload(payload: object) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("passed"), bool):
        return False
    for field in ("risk_flags", "detected_text", "matched_patterns"):
        values = payload.get(field)
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return False
    return isinstance(payload.get("notes"), str)


async def _strict_provider_payloads(
    db: AsyncSession,
    asset,
) -> tuple[dict, dict] | GatekeeperResult:
    if not llm_service.is_vision_provider_configured():
        return _provider_failure(
            "vision_unavailable",
            "Image safety checks are temporarily unavailable. Please try again later.",
        )

    issued = await create_provider_grant(
        db,
        asset=asset,
        provider=settings.llm_provider,
        purpose="gatekeeper",
    )
    try:
        try:
            vision = await llm_service.analyze_face_quality(issued.read_url)
        except Exception:
            return _provider_failure(
                "vision_unavailable",
                "Image safety checks are temporarily unavailable. Please try again later.",
            )
        if not _valid_vision_payload(vision):
            return _provider_failure(
                "vision_schema_invalid",
                "Image safety checks returned an invalid result. Please try again later.",
            )
        if str(vision.get("reject_reason") or "").startswith("vision_error:"):
            return _provider_failure(
                "vision_unavailable",
                "Image safety checks are temporarily unavailable. Please try again later.",
            )

        try:
            ocr = await llm_service.detect_sensitive_document_ocr(issued.read_url)
        except Exception:
            return _provider_failure(
                "safety_check_unavailable",
                "Sensitive-document checks are temporarily unavailable. Please try again later.",
            )
        if not _valid_ocr_payload(ocr):
            return _provider_failure(
                "safety_schema_invalid",
                "Sensitive-document checks returned an invalid result. Please try again later.",
            )
        if str(ocr.get("notes") or "").startswith("ocr_error:"):
            return _provider_failure(
                "safety_check_unavailable",
                "Sensitive-document checks are temporarily unavailable. Please try again later.",
            )
        return vision, ocr
    finally:
        await revoke_provider_grant(db, issued.grant)


async def check_image_quality(
    db: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    asset_id: uuid.UUID,
) -> GatekeeperResult:
    """
    Upload gate:
    - blocks explicit safety/document risks
    - blocks when the required provider or response schema is unavailable
    - applies local resolution, exposure, and sharpness checks
    """
    private_asset = await load_owned_asset_bytes(
        db,
        owner_user_id=owner_user_id,
        asset_id=asset_id,
    )
    provider_payloads = await _strict_provider_payloads(db, private_asset.asset)
    if isinstance(provider_payloads, GatekeeperResult):
        return provider_payloads
    vision, ocr = provider_payloads
    content = private_asset.content

    risk_flags = [
        str(flag).strip()
        for flag in [*(vision.get("risk_flags") or []), *(ocr.get("risk_flags") or [])]
        if str(flag).strip()
    ]
    ocr_text = ocr.get("detected_text") or []
    ocr_patterns = ocr.get("matched_patterns") or []
    ocr_metrics: dict[str, float] = {
        "ocr_text_count": float(len(ocr_text)),
        "ocr_pattern_count": float(len(ocr_patterns)),
    }
    if not ocr["passed"] and not risk_flags:
        return GatekeeperResult(
            passed=False,
            reasons=["ocr_reject"],
            advice=["Sensitive text was detected. Please upload a regular portrait photo."],
            metrics=ocr_metrics,
            risk_flags=[],
        )

    blocked_flags = sorted(set(flag for flag in risk_flags if flag in _BLOCKED_FLAG_RESPONSES))
    if blocked_flags:
        return _build_blocked_flag_response(blocked_flags, ocr_metrics)

    if vision["passed"] is False:
        reject_reason = str(vision.get("reject_reason") or "gatekeeper_reject")
        reason_code, advice = _normalize_vision_reject_reason(reject_reason)
        return GatekeeperResult(
            passed=False,
            reasons=[reason_code],
            advice=[advice],
            metrics=ocr_metrics,
            risk_flags=sorted(set(risk_flags)),
        )

    if len(content) > 10 * 1024 * 1024:
        return GatekeeperResult(
            passed=False,
            reasons=["file_too_large"],
            advice=["The image is too large. Please upload a file under 10MB."],
            metrics={},
            risk_flags=sorted(set(risk_flags)),
        )

    if Image is None or ImageStat is None or ImageFilter is None:
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
    warnings: list[str] = []
    warning_advice: list[str] = []

    if min(width, height) < 512:
        _add_warning(warnings, warning_advice, "low_resolution")

    gray = image.convert("L")
    gray_stat = ImageStat.Stat(gray)
    brightness = float(gray_stat.mean[0])
    contrast = float(gray_stat.stddev[0])
    hist = gray.histogram()
    dynamic_min = next((idx for idx, count in enumerate(hist) if count), 0)
    dynamic_max = next((idx for idx in range(255, -1, -1) if hist[idx]), 255)
    dynamic_range = float(dynamic_max - dynamic_min)

    if brightness < 12 or (dynamic_range < 18 and contrast < 8):
        reasons.append("black_or_blank")
        advice.append("The image is too dark or blank to generate a usable portrait.")
    elif brightness < 60:
        _add_warning(warnings, warning_advice, "too_dark")
    if brightness > 248 and contrast < 10:
        reasons.append("severely_overexposed")
        advice.append("The image is too overexposed to preserve facial detail.")
    elif brightness > 230:
        _add_warning(warnings, warning_advice, "overexposed")

    sharpness = _portrait_roi_sharpness_metrics(image)
    edge_mean = sharpness["edge_mean"]
    portrait_roi_edge_mean = sharpness["portrait_roi_edge_mean"]
    portrait_roi_contrast = sharpness["portrait_roi_contrast"]
    if portrait_roi_edge_mean < 2.7 and portrait_roi_contrast < 10:
        reasons.append("too_blurry")
        advice.append("The face and upper-body area are too blurry to preserve identity.")
    elif portrait_roi_edge_mean < 7:
        _add_warning(warnings, warning_advice, "too_blurry")

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
            "contrast": _clamp(float(contrast), 0.0, 255.0),
            "dynamic_range": _clamp(float(dynamic_range), 0.0, 255.0),
            "edge_mean": _clamp(float(edge_mean), 0.0, 255.0),
            "face_roi_edge_mean": _clamp(float(sharpness["face_roi_edge_mean"]), 0.0, 255.0),
            "upper_body_edge_mean": _clamp(float(sharpness["upper_body_edge_mean"]), 0.0, 255.0),
            "portrait_roi_edge_mean": _clamp(float(portrait_roi_edge_mean), 0.0, 255.0),
            **ocr_metrics,
            **doc_metrics,
        },
        risk_flags=sorted(set(risk_flags)),
        warnings=warnings,
        warning_advice=warning_advice,
    )
