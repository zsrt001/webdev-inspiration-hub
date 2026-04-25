"""Deterministic content policy checks for prompt text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class ContentPolicyVerdict:
    passed: bool
    reason: str | None
    matched_terms: list[str]
    category: str | None = None
    categories: list[str] = field(default_factory=list)


_CATEGORY_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "sexual_explicit": (
        re.compile(
            r"\b(?:nsfw|nude|naked|porn|pornographic|erotic|sex|sexual|fetish|bdsm|lingerie|topless|bottomless|nipples?|breasts?|cameltoe|explicit\s+nudity|adult\s+content|x{2,3})\b",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"(色情|情色|裸(?:体|照|露)?|露点|全裸|半裸|性爱|做爱|约炮|成人视频|AV片|情趣内衣|性暗示|SM|调教|自慰|乳沟|巨乳|露胸|裸模|成人内容)",
            flags=re.IGNORECASE,
        ),
    ),
    "minor_related": (
        re.compile(
            r"\b(?:underage|minor|child\s*bride|teen\s*sex|lolita|schoolgirl\s+fetish|barely\s+legal)\b",
            flags=re.IGNORECASE,
        ),
        re.compile(r"(未成年|幼女|萝莉|儿童新娘|学生制服诱惑|童颜巨乳)", flags=re.IGNORECASE),
    ),
    "document_risk": (
        re.compile(
            r"\b(?:id\s*card|identity\s*card|identity\s*document|passport|driver'?s?\s*license|bank\s*card|credit\s*card|debit\s*card|government\s*id|document\s*photo)\b",
            flags=re.IGNORECASE,
        ),
        re.compile(r"(身份证|证件照|护照|驾驶证|银行卡|信用卡|借记卡|证件正反面)", flags=re.IGNORECASE),
    ),
    "payment_risk": (
        re.compile(
            r"\b(?:payment\s*qr|payment\s*code|qr\s*code\s*payment|wechat\s*pay|alipay\s*code|pay\s*to\s*code)\b",
            flags=re.IGNORECASE,
        ),
        re.compile(r"(收款码|支付码|付款码|微信收款码|支付宝收款码|二维码收款)", flags=re.IGNORECASE),
    ),
}

_CATEGORY_PRIORITY: tuple[str, ...] = (
    "minor_related",
    "sexual_explicit",
    "document_risk",
    "payment_risk",
)

_CATEGORY_REASON_MAP: dict[str, str] = {
    "sexual_explicit": "explicit_prompt_disallowed",
    "minor_related": "minor_related_prompt_disallowed",
    "document_risk": "sensitive_document_prompt_disallowed",
    "payment_risk": "payment_code_prompt_disallowed",
}

_CATEGORY_MESSAGE_MAP: dict[str, str] = {
    "sexual_explicit": "Prompt contains disallowed sexual or NSFW content.",
    "minor_related": "Prompt contains disallowed minor-related content.",
    "document_risk": "Prompt contains disallowed document or card-related content.",
    "payment_risk": "Prompt contains disallowed payment-code related content.",
}


def build_rejection_message(verdict: ContentPolicyVerdict) -> str:
    if verdict.category:
        return _CATEGORY_MESSAGE_MAP.get(verdict.category, "Prompt contains disallowed content.")
    return "Prompt contains disallowed content."


def evaluate_prompt_text(text: str | None) -> ContentPolicyVerdict:
    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return ContentPolicyVerdict(passed=True, reason=None, matched_terms=[])

    matched_terms: list[str] = []
    matched_categories: list[str] = []

    for category, patterns in _CATEGORY_PATTERNS.items():
        category_hit = False
        for pattern in patterns:
            for match in pattern.finditer(normalized):
                token = match.group(0).strip()
                if token and token not in matched_terms:
                    matched_terms.append(token[:32])
                category_hit = True
        if category_hit and category not in matched_categories:
            matched_categories.append(category)

    if not matched_terms:
        return ContentPolicyVerdict(passed=True, reason=None, matched_terms=[])

    primary_category = next(
        (category for category in _CATEGORY_PRIORITY if category in matched_categories),
        matched_categories[0],
    )
    return ContentPolicyVerdict(
        passed=False,
        reason=_CATEGORY_REASON_MAP.get(primary_category, "explicit_prompt_disallowed"),
        matched_terms=matched_terms[:12],
        category=primary_category,
        categories=matched_categories,
    )
