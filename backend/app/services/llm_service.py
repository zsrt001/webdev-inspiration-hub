"""
LLM Service - Handles image analysis and descriptive text generation for vision-based prompting.
"""

from __future__ import annotations

import json
import logging
import re
import base64
from urllib.parse import urlparse
from typing import Any

import httpx

from app.core.config import get_settings

settings = get_settings()

JIEKOU_CHAT_URL_DEFAULT = "https://api.jiekou.ai/v1/chat/completions"
DEFAULT_JIEKOU_VISION_MODEL = "gemini-3.1-flash"
DEFAULT_WENWEN_CHAT_PATH = "/chat/completions"
DEFAULT_WENWEN_TEXT_MODEL = "deepseek-v3.2"
DEFAULT_WENWEN_VISION_MODEL = "gemini-3.1-pro-preview"

logger = logging.getLogger(__name__)


def _clean_json_block(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()
    return cleaned


_QA_REASON_MAP: dict[str, str] = {
    "cropped_head": "cropped_face",
    "cropped_face": "cropped_face",
    "face_crop": "cropped_face",
    "headless": "headless",
    "no_head": "headless",
    "face_distortion": "face_distortion",
    "distorted_face": "face_distortion",
    "fused_faces": "fused_faces",
    "merged_faces": "fused_faces",
    "merged_bodies": "body_fusion",
    "fused_bodies": "body_fusion",
    "shared_torso": "body_fusion",
    "overlapping_limbs": "body_fusion",
    "conjoined_bodies": "body_fusion",
    "body_fusion": "body_fusion",
    "missing_subject": "subject_missing",
    "missing_person": "subject_missing",
    "single_subject_only": "subject_missing",
    "one_subject_only": "subject_missing",
    "subject_missing": "subject_missing",
    "identity_swapped": "identity_swap",
    "swapped_identity": "identity_swap",
    "identity_swap": "identity_swap",
    "identity_mismatch": "identity_mismatch",
    "face_not_like_source": "identity_mismatch",
    "face_mismatch": "identity_mismatch",
    "identity_not_preserved": "identity_mismatch",
    "extra_limbs": "extra_limbs",
    "extra_arms": "extra_limbs",
    "bad_hands": "bad_hands",
    "hands_distorted": "bad_hands",
    "extra_fingers": "bad_hands",
    "bad_fingers": "bad_hands",
    "dress_exposure_error": "dress_exposure_error",
    "wedding_dress_exposure": "dress_exposure_error",
    "wardrobe_malfunction": "dress_exposure_error",
    "studio_quality_fail": "poor_studio_quality",
    "not_studio_quality": "poor_studio_quality",
    "low_studio_quality": "poor_studio_quality",
    "poor_quality": "poor_studio_quality",
    "ai_look": "poor_studio_quality",
    "waxy_skin": "poor_studio_quality",
    "cheap_composite": "poor_studio_quality",
    "black_or_blank": "black_or_blank",
    "blank": "black_or_blank",
    "black_image": "black_or_blank",
    "watermark_or_text": "watermark_or_text",
    "watermark": "watermark_or_text",
    "text_overlay": "watermark_or_text",
    "nsfw": "nsfw",
    "severe_artifacts": "severe_artifacts",
    "artifact": "severe_artifacts",
}

_ALLOWED_QA_REASONS = {
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
}

_RISK_FLAG_MAP: dict[str, str] = {
    "id_card": "id_document",
    "identity_card": "id_document",
    "identity_document": "id_document",
    "government_id": "id_document",
    "id_document": "id_document",
    "证件": "id_document",
    "证件照": "id_document",
    "身份证": "id_document",
    "passport": "passport",
    "passport_document": "passport",
    "护照": "passport",
    "driver_license": "driver_license",
    "driving_license": "driver_license",
    "驾驶证": "driver_license",
    "bank_card": "bank_card",
    "bankcard": "bank_card",
    "credit_card": "bank_card",
    "debit_card": "bank_card",
    "银行卡": "bank_card",
    "payment_qr": "payment_qr",
    "payment_code": "payment_qr",
    "qr_payment_code": "payment_qr",
    "qr_code": "payment_qr",
    "收款码": "payment_qr",
    "支付码": "payment_qr",
    "付款码": "payment_qr",
    "收钱码": "payment_qr",
    "explicit_nudity": "explicit_nudity",
    "nudity": "explicit_nudity",
    "sexual_content": "explicit_nudity",
    "adult_content": "explicit_nudity",
    "色情": "explicit_nudity",
    "minor": "minor",
    "underage": "minor",
    "child": "minor",
    "未成年": "minor",
}

_OCR_PATTERN_MAP: dict[str, str] = {
    r"\b\d{17}[0-9xX]\b": "id_number_pattern",
    r"\b[A-Z]\d{7,8}\b": "passport_number_pattern",
    r"\b\d{15,19}\b": "bank_card_pattern",
    r"(居民身份证|中华人民共和国|公民身份号码|身份证|identity\s*card|government\s*id)": "id_document_keyword_pattern",
    r"(passport|护照)": "passport_keyword_pattern",
    r"(driver\s*license|driving\s*license|驾驶证)": "driver_license_keyword_pattern",
    r"(银行卡|bank\s*card|credit\s*card|debit\s*card)": "bank_card_keyword_pattern",
    r"(收款码|支付码|付款码|微信支付|支付宝|wechat\s*pay|alipay|payment\s*code|qr\s*code)": "payment_code_pattern",
}

_OCR_PATTERN_FLAG_MAP: dict[str, str] = {
    "id_number_pattern": "id_document",
    "id_document_keyword_pattern": "id_document",
    "passport_number_pattern": "passport",
    "passport_keyword_pattern": "passport",
    "driver_license_keyword_pattern": "driver_license",
    "bank_card_pattern": "bank_card",
    "bank_card_keyword_pattern": "bank_card",
    "payment_code_pattern": "payment_qr",
}


def _normalize_qa_reason(reason: str) -> str:
    key = (reason or "").strip().lower()
    if not key:
        return "other"
    normalized = _QA_REASON_MAP.get(key, key)
    return normalized if normalized in _ALLOWED_QA_REASONS else "other"


def _normalize_risk_flag(flag: str) -> str:
    key = re.sub(r"[\s\-]+", "_", (flag or "").strip().lower()).strip("_")
    if not key:
        return ""
    return _RISK_FLAG_MAP.get(key, key)


def _extract_pattern_hits(text_items: list[str]) -> list[str]:
    hits: list[str] = []
    for text in text_items:
        content = (text or "").strip()
        if not content:
            continue
        for pattern, hit_name in _OCR_PATTERN_MAP.items():
            if re.search(pattern, content, flags=re.IGNORECASE):
                hits.append(hit_name)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in hits:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _active_provider() -> str | None:
    configured = (settings.llm_provider or "").strip().lower()
    if configured == "wenwen":
        return "wenwen" if settings.wenwen_vision_api_key_effective else None
    if configured in {"", "jiekou"} and settings.jiekou_api_key:
        return "jiekou"
    if configured in {"", "wenwen"} and settings.wenwen_vision_api_key_effective:
        return "wenwen"
    return None


def is_vision_provider_configured() -> bool:
    return _active_provider() is not None


def _active_vision_model() -> str:
    if _active_provider() == "wenwen":
        return (settings.wenwen_vision_model or DEFAULT_WENWEN_VISION_MODEL).strip()
    return (settings.jiekou_vision_model or DEFAULT_JIEKOU_VISION_MODEL).strip()


def is_text_provider_configured() -> bool:
    configured = (settings.llm_provider or "").strip().lower()
    if configured == "wenwen":
        return bool(settings.wenwen_text_api_key_effective)
    if configured in {"", "jiekou"} and settings.jiekou_api_key:
        return True
    if configured in {"", "wenwen"} and settings.wenwen_text_api_key_effective:
        return True
    return False


def _is_localish_url(image_url: str) -> bool:
    try:
        parsed = urlparse(str(image_url or "").strip())
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost"}


async def _coerce_remote_image_input(image_url: str) -> str:
    raw = str(image_url or "").strip()
    if not raw:
        return raw
    if not raw.startswith(("http://", "https://")):
        return raw
    if not _is_localish_url(raw):
        return raw

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
        response = await client.get(raw)
        response.raise_for_status()
        content_type = response.headers.get("content-type") or "image/jpeg"
        encoded = base64.b64encode(response.content).decode("utf-8")
        return f"data:{content_type};base64,{encoded}"


async def _llm_chat(payload: dict[str, Any], *, title: str, timeout: float) -> dict[str, Any]:
    provider = _active_provider()
    if provider == "jiekou":
        url = (settings.jiekou_chat_url or JIEKOU_CHAT_URL_DEFAULT).strip()
        api_key = settings.jiekou_api_key
    elif provider == "wenwen":
        base = (settings.wenwen_api_base_url or "").rstrip("/")
        path = (settings.wenwen_chat_path or DEFAULT_WENWEN_CHAT_PATH).strip()
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{base}{path}"
        api_key = settings.wenwen_vision_api_key_effective
    else:
        raise ValueError("No supported vision provider is configured")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": title,
    }

    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def _text_chat(payload: dict[str, Any], *, title: str, timeout: float) -> dict[str, Any]:
    configured = (settings.llm_provider or "").strip().lower()
    if configured in {"", "jiekou"} and settings.jiekou_api_key:
        url = (settings.jiekou_chat_url or JIEKOU_CHAT_URL_DEFAULT).strip()
        api_key = settings.jiekou_api_key
    elif configured in {"wenwen", ""} and settings.wenwen_text_api_key_effective:
        base = (settings.wenwen_api_base_url or "").rstrip("/")
        path = (settings.wenwen_chat_path or DEFAULT_WENWEN_CHAT_PATH).strip()
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{base}{path}"
        api_key = settings.wenwen_text_api_key_effective
    else:
        raise ValueError("No supported text provider is configured")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": title,
    }

    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def optimize_generation_prompt(prompt: str, *, is_couple: bool = False) -> str:
    raw_prompt = str(prompt or "").strip()
    if not raw_prompt or not is_text_provider_configured():
        return raw_prompt

    configured = (settings.llm_provider or "").strip().lower()
    if configured == "wenwen":
        model = (settings.wenwen_text_model or DEFAULT_WENWEN_TEXT_MODEL).strip()
    else:
        model = (settings.jiekou_vision_model or DEFAULT_JIEKOU_VISION_MODEL).strip()

    system_prompt = (
        "You rewrite wedding-image prompts for a generation model. "
        "Keep the original intent, people count, outfit, scene, identity-preservation requirements, and realism constraints. "
        "Never weaken or remove identity lock, reference-face preservation, studio lighting, or negative-quality constraints. "
        "Do not add camera jargon, safety disclaimers, or markdown. "
        "Return one concise production-ready prompt string only."
    )
    user_prompt = (
        f"Rewrite this {'couple' if is_couple else 'single-subject'} wedding generation prompt into a tighter, "
        f"cleaner production prompt while preserving all important details:\n\n{raw_prompt}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 500,
    }
    try:
        result = await _text_chat(payload, title="AI Wedding Prompt Optimizer", timeout=30.0)
        content = result["choices"][0]["message"]["content"]
        optimized = str(content or "").strip()
        return optimized or raw_prompt
    except Exception as exc:
        logger.warning("Prompt optimization failed: %s", exc)
        return raw_prompt


async def verify_generated_image_quality(
    image_url: str,
    *,
    is_couple: bool = False,
    source_image_urls: list[str] | None = None,
) -> dict[str, Any]:
    """
    Strict QA check for generated images.

    Returns:
        {"passed": bool, "reasons": list[str], "notes": str}

    Fail-close when LLM provider is configured but the call fails.
    """
    if not is_vision_provider_configured():
        return {"passed": True, "reasons": [], "notes": "llm_not_configured"}

    source_images = [str(url).strip() for url in (source_image_urls or []) if str(url).strip()]
    couple_rules = (
        "\nCouple-specific rules:\n"
        "- The image must clearly contain two primary wedding subjects.\n"
        "- If one primary subject is missing, collapsed, or unreadable, passed=false with reason subject_missing.\n"
        "- If bodies, torsos, shoulders, or limbs are fused/overlapping unnaturally, passed=false with reason body_fusion.\n"
        "- If identities are swapped or subject roles are clearly confused, passed=false with reason identity_swap.\n"
    ) if is_couple else ""
    identity_rules = (
        "\nIdentity rules:\n"
        "- Compare the generated face(s) against the provided source portrait(s).\n"
        "- The generated subject must still read as the same person, not a generic bride/groom or a beautified replacement.\n"
        "- Preserve face shape, eye shape and spacing, nose shape, mouth shape, jawline, chin, skin undertone, and age impression.\n"
        "- If the generated face noticeably changes identity, becomes distorted, or clearly does not resemble the source identity, passed=false with reason identity_mismatch.\n"
        "- Allow makeup, lighting, hairstyle, and bridal styling changes only when the face identity is still recognizable.\n"
    ) if source_images else ""

    prompt = (
        "You are a strict QA inspector for AI-generated wedding photos.\n"
        "Check for critical errors that make the result NOT acceptable for a paid product.\n"
        "Focus especially on: identity mismatch, distorted faces, too many fingers, broken hands, abnormal limbs, unsafe or wrong wedding dress exposure, missing subjects, severe artifacts, and whether the result looks like a paid bridal-studio deliverable.\n"
        "Return strictly valid JSON only with this schema:\n"
        "{\n"
        '  "passed": boolean,\n'
        '  "reasons": string[],\n'
        '  "notes": string\n'
        "}\n"
        "Rules:\n"
        "- If ANY critical issue exists, passed=false.\n"
        '- reasons must be a subset of: ["headless","cropped_face","face_distortion","fused_faces","body_fusion","subject_missing","identity_swap","identity_mismatch","extra_limbs","bad_hands","dress_exposure_error","poor_studio_quality","black_or_blank","watermark_or_text","nsfw","severe_artifacts","other"].\n'
        "- Use bad_hands ONLY for severe, clearly visible hand failures: impossible finger geometry, extra fingers, missing fingers, broken wrists, or distorted hands that noticeably ruin the paid result.\n"
        "- Do NOT fail for minor or ambiguous hand detail, small/background hands, hands partially covered by bouquet/dress/sleeves, or natural pose blur when the face, dress, and overall wedding portrait are acceptable.\n"
        "- Use dress_exposure_error when the wedding dress exposes private areas, creates unintended nudity, or has impossible cutouts.\n"
        "- Use poor_studio_quality when the image looks like a generic AI render, cheap composite, tourist snapshot, fantasy costume render, waxy/over-smoothed beauty-filter output, flat lighting, harsh backlight, blown-out sky/windows/dress, weak facial detail, or otherwise does not look like a paid bridal-studio wedding portrait.\n"
        "- If identity is wrong and the image is beautiful, still fail with identity_mismatch.\n"
        f"{couple_rules}"
        f"{identity_rules}"
        "- notes: brief (<= 200 chars)."
    )

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for idx, src in enumerate(source_images[:2]):
        content.append({"type": "text", "text": f"Source portrait {idx + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": await _coerce_remote_image_input(src)}})
    content.append({"type": "text", "text": "Generated candidate:"})
    content.append({"type": "image_url", "image_url": {"url": await _coerce_remote_image_input(image_url)}})

    payload = {
        "model": _active_vision_model(),
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        result = await _llm_chat(payload, title="AI Wedding QA", timeout=15.0)
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            return {"passed": False, "reasons": ["vision_error"], "notes": "invalid_llm_content"}
        data = json.loads(_clean_json_block(content))
        passed = bool(data.get("passed"))
        reasons = data.get("reasons") or []
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        reasons = [_normalize_qa_reason(str(reason)) for reason in reasons if str(reason).strip()]
        reasons = [reason for reason in reasons if reason]
        notes = str(data.get("notes") or "")[:200]
        if reasons:
            passed = False
        return {"passed": passed, "reasons": reasons, "notes": notes}
    except Exception as exc:
        logger.warning("Vision QA failed: %s", exc)
        return {"passed": False, "reasons": ["vision_error"], "notes": f"vision_error:{type(exc).__name__}"}


async def analyze_image_prompt(image_url: str, context_type: str) -> str:
    """
    Uses configured LLM provider to describe visual components of an image.

    Args:
        image_url: URL of the reference image
        context_type: Either 'clothing' or 'background'

    Returns:
        A detailed text description for AI generation.
    """
    prompt_map = {
        "clothing": (
            "Analyze the clothing in this wedding photo. Describe the fabric, cut, embroidery, accessories, and overall style in detail. "
            "Focus strictly on physical attributes. DO NOT describe camera, film, quality, or art style."
        ),
        "background": (
            "Analyze the background and lighting in this wedding photo. Describe the environment, architectural details, light source, mood, and color palette in detail. "
            "Focus strictly on physical attributes. DO NOT describe camera, film, quality, or art style."
        ),
        "subject": (
            "Analyze the subject identity cues in this portrait. Describe gender expression, hairstyle, face shape, clothing silhouette, posture, and notable visible features. "
            "Focus strictly on observable physical attributes. DO NOT describe camera, film, quality, or art style."
        ),
    }

    user_prompt = prompt_map.get(
        context_type,
        f"Describe the {context_type} in this image in detail. Output physical description only.",
    )

    image_input = await _coerce_remote_image_input(image_url)
    payload = {
        "model": _active_vision_model(),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an AI Assistant that describes scenes for a PHOTOGRAPHER. "
                    "1. Describe the Subject (Outfit, Action) and the Background strictly. "
                    "2. DO NOT describe the camera, film, or quality (e.g., do not say '4k', 'photorealistic', 'shot on camera'). "
                    "3. DO NOT use art styles like '3d', 'illustration', or 'painting'. "
                    "4. Focus on physical details: lighting direction, fabric texture, and pose."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": image_input}},
                ],
            },
        ],
    }

    try:
        result = await _llm_chat(payload, title="AI Wedding Studio", timeout=30.0)
        description = result["choices"][0]["message"]["content"]
        return str(description).strip()
    except Exception as exc:
        logger.warning("LLM analysis failed: %s", exc)
        return f"classic {context_type} for wedding photography"


async def analyze_face_quality(image_url: str) -> dict:
    """
    Strict 'AI Gatekeeper' that rejects bad photos (no face, dark, blurry),
    detects sensitive uploads (ID/passport/payment QR), and returns gender
    for prompt optimization.

    Returns:
        {
          'passed': bool,
          'reject_reason': str,
          'gender': 'm' or 'f',
          'risk_flags': string[]
        }
    """
    prompt = (
        "Analyze this uploaded image for a wedding generation app. "
        "1. **Sensitive Upload Check**: Does this look like an ID card/passport/driver license/bank card/payment QR? "
        "2. **Face Check**: Is there a visible human face suitable for portrait generation? (Yes/No) "
        "3. **Lighting**: Is the face too dark / heavy shadow / severe overexposure? (Yes/No) "
        "4. **Quality**: Is it blurry, low resolution, or pixelated? (Yes/No) "
        "5. **Gender**: Is the subject Male or Female? (m/f) "
        "\n\n"
        "Return strictly valid JSON format only:\n"
        "{\n"
        '  "passed": boolean, (True only if NOT sensitive upload and Face=Yes and Lighting=Good and Quality=Good),\n'
        '  "reject_reason": string or null, (e.g. \'Face too dark\', \'No face detected\', \'Image too blurry\'),\n'
        '  "gender": "m" or "f",\n'
        '  "risk_flags": string[] (subset of: [\'id_document\',\'passport\',\'driver_license\',\'bank_card\',\'payment_qr\',\'explicit_nudity\',\'minor\'])\n'
        "}"
    )

    image_input = await _coerce_remote_image_input(image_url)
    payload = {
        "model": _active_vision_model(),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_input}},
                ],
            }
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        result = await _llm_chat(payload, title="AI Wedding Gatekeeper", timeout=15.0)
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("invalid_llm_content")
        data = json.loads(_clean_json_block(content))
        if not isinstance(data, dict):
            raise ValueError("invalid_gatekeeper_payload")

        risk_flags = data.get("risk_flags") or []
        if not isinstance(risk_flags, list):
            risk_flags = [str(risk_flags)]
        normalized_flags = [_normalize_risk_flag(str(value)) for value in risk_flags if str(value).strip()]
        normalized_flags = [flag for flag in normalized_flags if flag]
        data["risk_flags"] = sorted(set(normalized_flags))

        gender = str(data.get("gender") or "f").lower().strip()
        data["gender"] = "m" if gender == "m" else "f"

        if data["risk_flags"]:
            data["passed"] = False
            if not data.get("reject_reason"):
                data["reject_reason"] = "sensitive_upload_detected"

        if "passed" not in data:
            data["passed"] = False
        if "reject_reason" not in data:
            data["reject_reason"] = None
        return data
    except Exception as exc:
        logger.warning("Gatekeeper analysis failed: %s", exc)
        return {
            "passed": False,
            "reject_reason": f"vision_error:{type(exc).__name__}",
            "gender": "f",
            "risk_flags": [],
        }


async def detect_sensitive_document_ocr(image_url: str) -> dict[str, Any]:
    """
    OCR-focused sensitive upload detection.

    Returns:
      {
        "passed": bool,
        "risk_flags": string[],
        "detected_text": string[],
        "matched_patterns": string[],
        "notes": string
      }
    """
    if not is_vision_provider_configured():
        return {
            "passed": True,
            "risk_flags": [],
            "detected_text": [],
            "matched_patterns": [],
            "notes": "llm_not_configured",
        }

    prompt = (
        "You are an OCR and compliance inspector for user-uploaded images.\n"
        "Task:\n"
        "1) Read visible text (OCR-style extraction).\n"
        "2) Identify if the image likely contains sensitive documents/cards/payment code.\n"
        "3) Return strict JSON only.\n"
        "Schema:\n"
        "{\n"
        '  "passed": boolean,\n'
        '  "risk_flags": string[],\n'
        '  "detected_text": string[],\n'
        '  "notes": string\n'
        "}\n"
        "risk_flags subset: ['id_document','passport','driver_license','bank_card','payment_qr','explicit_nudity','minor']\n"
        "Set passed=false when any risk_flag exists."
    )

    image_input = await _coerce_remote_image_input(image_url)
    payload = {
        "model": _active_vision_model(),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_input}},
                ],
            }
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        result = await _llm_chat(payload, title="AI Wedding OCR Gatekeeper", timeout=20.0)
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("invalid_llm_content")
        data = json.loads(_clean_json_block(content))
        if not isinstance(data, dict):
            raise ValueError("invalid_ocr_payload")

        risk_flags_raw = data.get("risk_flags") or []
        if not isinstance(risk_flags_raw, list):
            risk_flags_raw = [str(risk_flags_raw)]
        normalized_flags = [_normalize_risk_flag(str(flag)) for flag in risk_flags_raw if str(flag).strip()]
        normalized_flags = [flag for flag in normalized_flags if flag]

        detected_text_raw = data.get("detected_text") or []
        if not isinstance(detected_text_raw, list):
            detected_text_raw = [str(detected_text_raw)]
        detected_text = [str(item).strip()[:80] for item in detected_text_raw if str(item).strip()]

        pattern_hits = _extract_pattern_hits(detected_text)
        pattern_flags = [
            _OCR_PATTERN_FLAG_MAP[hit]
            for hit in pattern_hits
            if hit in _OCR_PATTERN_FLAG_MAP
        ]
        if pattern_flags:
            normalized_flags = sorted(set([*normalized_flags, *pattern_flags]))

        passed = bool(data.get("passed", True))
        if normalized_flags:
            passed = False

        return {
            "passed": passed,
            "risk_flags": sorted(set(normalized_flags)),
            "detected_text": detected_text[:8],
            "matched_patterns": pattern_hits[:8],
            "notes": str(data.get("notes") or "")[:200],
        }
    except Exception as exc:
        logger.warning("OCR sensitive detection failed: %s", exc)
        return {
            "passed": False,
            "risk_flags": [],
            "detected_text": [],
            "matched_patterns": [],
            "notes": f"ocr_error:{type(exc).__name__}",
        }


async def verify_image_quality(image_url: str) -> str:
    """
    QA Step: Verifies the generated image for critical failures.
    Returns 'PASS' or 'FAIL'.
    """
    verdict = await verify_generated_image_quality(image_url)
    return "PASS" if verdict.get("passed") is True else "FAIL"


async def refine_flux_prompt(user_input: str) -> str:
    """
    Step A (The Brain): Converts user input into professional photography prompts
    for the current Jiekou + ComfyUI generation pipeline.
    """
    payload = {
        "model": _active_vision_model(),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a professional photography consultant for Flux image generation. "
                    "Your task is to convert simple user descriptions into high-end photography prompts. "
                    "ALWAYS inject these technical parameters: 'Shot on Phase One XF, 100mm macro lens, Direct Flash, Hard Shadows, Visible Pores'. "
                    "NEVER use terms like 'smooth skin', 'airbrushed', or 'plastic'. "
                    "Focus on physical textures and high-intensity studio lighting. "
                    "Output ONLY the final prompt string."
                ),
            },
            {
                "role": "user",
                "content": f"User Input: {user_input}",
            },
        ],
    }

    try:
        result = await _llm_chat(payload, title="AI Wedding Prompt Brain", timeout=15.0)
        refined_prompt = str(result["choices"][0]["message"]["content"]).strip()
        refined_prompt = refined_prompt.replace("smooth skin", "visible skin pores").replace(
            "airbrushed", "raw skin texture"
        )
        return refined_prompt
    except Exception as exc:
        logger.warning("Prompt refinement failed: %s", exc)
        return f"Shot on Phase One XF, 100mm macro, Direct Flash, Hard Shadows, Visible Pores, {user_input}"
