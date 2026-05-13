"""QA Service - output validation and retry guidance."""

from io import BytesIO
import httpx

try:
    from PIL import Image, ImageStat
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]
from app.core.config import get_settings
from app.services import llm_service
from app.services.qa_rules import normalize_qa_reason, run_local_qa_rules

settings = get_settings()


def blocking_vision_reasons(reasons: list[str]) -> list[str]:
    """Return actionable vision QA failures.

    A generic "other" verdict is not enough to block delivery when local image
    checks already passed; it is too vague for users and operators to act on.
    """
    blocking: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        normalized = normalize_qa_reason(reason)
        if normalized == "other":
            continue
        if normalized not in seen:
            seen.add(normalized)
            blocking.append(normalized)
    return blocking


async def basic_image_check(image_url: str) -> tuple[bool, list[str], dict[str, float]]:
    if Image is None or ImageStat is None:
        if settings.qa_allow_without_pillow:
            return True, [], {}
        return False, ["qa_local_checker_unavailable"], {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(image_url)
        resp.raise_for_status()
        content = resp.content

    try:
        image = Image.open(BytesIO(content)).convert("RGB")
    except Exception:
        return False, ["invalid_image"], {}

    reasons, metrics = run_local_qa_rules(image)
    return len(reasons) == 0, reasons, metrics


async def verify_with_vision(
    image_url: str,
    *,
    is_couple: bool = False,
    source_image_urls: list[str] | None = None,
) -> tuple[bool, list[str]]:
    if not llm_service.is_vision_provider_configured():
        if settings.qa_require_vision:
            return False, ["vision_not_configured"]
        return True, []
    verdict = await llm_service.verify_generated_image_quality(
        image_url,
        is_couple=is_couple,
        source_image_urls=source_image_urls,
    )
    if verdict.get("passed") is True:
        return True, []
    reasons = verdict.get("reasons") or ["vision_fail"]
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    normalized: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        normalized_reason = normalize_qa_reason(str(reason))
        if normalized_reason and normalized_reason not in seen:
            seen.add(normalized_reason)
            normalized.append(normalized_reason)
    return False, normalized or ["other"]


async def output_passes(
    image_url: str,
    *,
    is_couple: bool = False,
    source_image_urls: list[str] | None = None,
) -> tuple[bool, list[str]]:
    passed, reasons, _metrics = await basic_image_check(image_url)
    if not passed:
        return False, reasons
    vision_ok, vision_reasons = await verify_with_vision(
        image_url,
        is_couple=is_couple,
        source_image_urls=source_image_urls,
    )
    if not vision_ok:
        blocking_reasons = blocking_vision_reasons(vision_reasons)
        if not blocking_reasons and not settings.qa_require_vision:
            return True, []
        combined: list[str] = []
        seen: set[str] = set()
        for reason in [*reasons, *(blocking_reasons or vision_reasons)]:
            normalized = normalize_qa_reason(reason)
            if normalized not in seen:
                seen.add(normalized)
                combined.append(normalized)
        return False, combined
    return True, []
