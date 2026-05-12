"""Gatekeeper service for Smart Input quality validation."""

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
    "id_document": ("sensitive_document_upload", "检测到身份证或证件照风险，请上传生活自拍照"),
    "passport": ("sensitive_document_upload", "检测到护照风险，请上传生活自拍照"),
    "driver_license": ("sensitive_document_upload", "检测到驾驶证风险，请上传生活自拍照"),
    "bank_card": ("sensitive_document_upload", "检测到银行卡风险，请上传生活自拍照"),
    "payment_qr": ("payment_code_upload", "检测到收款码或支付码风险，请上传生活自拍照"),
    "explicit_nudity": ("unsafe_image_content", "检测到不符合平台规范的裸露或色情内容，请更换照片"),
    "minor": ("minor_related_image", "检测到未成年人相关风险，请更换照片"),
}

_VISION_REASON_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("no face", "face not detected", "未检测到人脸", "没有人脸", "无人脸"), "no_face_detected", "未检测到清晰正脸，请上传单人生活自拍"),
    (("multiple face", "multiple faces", "多人脸", "多个人脸"), "multiple_faces_detected", "请仅保留 1 位主体，避免多人同时入镜"),
    (("side face", "profile", "侧脸", "偏头"), "side_face_detected", "请正对镜头拍摄，避免大角度侧脸"),
    (("too dark", "dark", "underexposed", "光线过暗", "太暗"), "face_too_dark", "照片过暗，请在明亮环境重新拍摄"),
    (("overexposed", "too bright", "过曝", "曝光过度"), "image_overexposed", "照片过曝，请在柔和光线下重拍"),
    (("blur", "blurry", "out of focus", "模糊", "不清晰"), "image_too_blurry", "照片模糊，请对焦后重拍"),
    (("occluded", "遮挡", "口罩", "墨镜"), "face_occluded", "请避免口罩、墨镜或其他遮挡物遮住面部"),
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
        advice = ["检测到高风险图片，请更换为生活自拍照"]
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
        return "vision_unavailable", "质量检测服务暂时不可用，请稍后重试"
    for keywords, reason_code, advice in _VISION_REASON_RULES:
        if any(keyword in lowered or keyword in normalized for keyword in keywords):
            return reason_code, advice
    return "vision_reject", normalized or "图片未通过质量检测，请更换更清晰的生活自拍照"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _compute_colorfulness(image) -> float:
    """
    Hasler-Suesstrunk colorfulness metric (sampling-based, no numpy).
    """
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
    """
    Estimate skin-like pixels ratio in YCbCr color space.
    Used only as a weak signal for document interception.
    """
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
    """
    Lightweight local heuristic for detecting document-like uploads (ID/passport).
    This is conservative: we only block when several signals agree.
    """
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
    Minimal quality gatekeeper:
    - Resolution check
    - Brightness check
    - Sharpness check (edge strength)

    Production-grade path:
    - If `JIEKOU_API_KEY` is set, run Vision gate first (face/lighting/blur) and reject on FAIL.
    - Then (optionally) run fast local Pillow checks for extra safety and metrics.
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
                            advice=["检测到敏感文本风险，请上传生活自拍照"],
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
            advice=["图片过大，请压缩后再上传"],
            metrics={},
            risk_flags=sorted(set(risk_flags)),
        )

    if Image is None or ImageStat is None or ImageFilter is None:
        if settings.gatekeeper_allow_without_pillow:
            return GatekeeperResult(
                passed=True,
                reasons=[],
                advice=["当前环境未安装 Pillow，已跳过本地图像质量检测"],
                metrics={},
                risk_flags=sorted(set(risk_flags)),
            )
        return GatekeeperResult(
            passed=False,
            reasons=["local_checker_unavailable"],
            advice=["服务缺少本地质检依赖 Pillow，请稍后重试"],
            metrics={},
            risk_flags=sorted(set(risk_flags)),
        )

    image = Image.open(BytesIO(content)).convert("RGB")

    width, height = image.size
    reasons: list[str] = []
    advice: list[str] = []

    if min(width, height) < 512:
        reasons.append("low_resolution")
        advice.append("分辨率过低，请上传更清晰的照片")

    gray = image.convert("L")
    brightness = ImageStat.Stat(gray).mean[0]
    if brightness < 60:
        reasons.append("too_dark")
        advice.append("光线过暗，请在明亮环境下拍摄")
    if brightness > 230:
        reasons.append("overexposed")
        advice.append("光线过强，请避免过曝")

    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_mean = ImageStat.Stat(edges).mean[0]
    if edge_mean < 7:
        reasons.append("too_blurry")
        advice.append("照片过于模糊，请对焦清晰后重拍")

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
        if "检测到疑似证件或卡证素材，请上传生活自拍照" not in advice:
            advice.append("检测到疑似证件或卡证素材，请上传生活自拍照")

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
