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
from app.services.qa_rules import build_structured_qa_issues, normalize_qa_reason, run_local_qa_rules

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
    verdict = await basic_image_verdict(image_url)
    return bool(verdict["passed"]), list(verdict["reasons"]), dict(verdict["metrics"])


async def basic_image_verdict(image_url: str) -> dict:
    if Image is None or ImageStat is None:
        if settings.qa_allow_without_pillow:
            return {
                "passed": True,
                "reasons": [],
                "issues": [],
                "metrics": {},
                "source": "local",
                "notes": "local_checker_skipped",
            }
        reasons = ["qa_local_checker_unavailable"]
        return {
            "passed": False,
            "reasons": reasons,
            "issues": build_structured_qa_issues(reasons, source="local", notes="local_checker_unavailable"),
            "metrics": {},
            "source": "local",
            "notes": "local_checker_unavailable",
        }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(image_url)
        resp.raise_for_status()
        content = resp.content

    try:
        image = Image.open(BytesIO(content)).convert("RGB")
    except Exception:
        reasons = ["severe_artifacts"]
        return {
            "passed": False,
            "reasons": reasons,
            "issues": build_structured_qa_issues(reasons, source="local", notes="invalid_image"),
            "metrics": {},
            "source": "local",
            "notes": "invalid_image",
        }

    reasons, metrics = run_local_qa_rules(image)
    return {
        "passed": len(reasons) == 0,
        "reasons": reasons,
        "issues": build_structured_qa_issues(reasons, source="local", metrics=metrics),
        "metrics": metrics,
        "source": "local",
        "notes": "",
    }


async def verify_with_vision(
    image_url: str,
    *,
    is_couple: bool = False,
    source_image_urls: list[str] | None = None,
) -> tuple[bool, list[str]]:
    verdict = await verify_with_vision_verdict(
        image_url,
        is_couple=is_couple,
        source_image_urls=source_image_urls,
    )
    return bool(verdict["passed"]), list(verdict["reasons"])


async def verify_with_vision_verdict(
    image_url: str,
    *,
    is_couple: bool = False,
    source_image_urls: list[str] | None = None,
) -> dict:
    source_images = [str(url).strip() for url in (source_image_urls or []) if str(url).strip()]
    identity_vision_required = bool(source_images) and bool(settings.qa_require_identity_vision)
    if not llm_service.is_vision_provider_configured():
        if settings.qa_require_vision or identity_vision_required:
            reasons = ["vision_error"]
            return {
                "passed": False,
                "reasons": reasons,
                "issues": build_structured_qa_issues(reasons, source="vision", notes="vision_not_configured"),
                "notes": "identity_vision_required" if identity_vision_required else "vision_not_configured",
                "source": "vision",
            }
        return {"passed": True, "reasons": [], "issues": [], "notes": "llm_not_configured", "source": "vision"}
    verdict = await llm_service.verify_generated_image_quality(
        image_url,
        is_couple=is_couple,
        source_image_urls=source_image_urls,
    )
    if verdict.get("passed") is True:
        return {"passed": True, "reasons": [], "issues": [], "notes": verdict.get("notes") or "", "source": "vision"}
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
    normalized = normalized or ["other"]
    issues = verdict.get("issues")
    if not isinstance(issues, list) or not issues:
        issues = build_structured_qa_issues(
            normalized,
            source="vision",
            notes=str(verdict.get("notes") or ""),
        )
    return {
        "passed": False,
        "reasons": normalized,
        "issues": issues,
        "notes": str(verdict.get("notes") or ""),
        "source": "vision",
    }


async def output_passes(
    image_url: str,
    *,
    is_couple: bool = False,
    source_image_urls: list[str] | None = None,
) -> tuple[bool, list[str]]:
    verdict = await output_verdict(
        image_url,
        is_couple=is_couple,
        source_image_urls=source_image_urls,
    )
    return bool(verdict["passed"]), list(verdict["reasons"])


async def output_verdict(
    image_url: str,
    *,
    is_couple: bool = False,
    source_image_urls: list[str] | None = None,
) -> dict:
    source_images = [str(url).strip() for url in (source_image_urls or []) if str(url).strip()]
    identity_vision_required = bool(source_images) and bool(settings.qa_require_identity_vision)
    local_verdict = await basic_image_verdict(image_url)
    if not local_verdict["passed"]:
        return {
            "passed": False,
            "reasons": local_verdict["reasons"],
            "issues": local_verdict["issues"],
            "local": local_verdict,
            "vision": None,
            "notes": "local_qa_failed",
        }

    vision_verdict = await verify_with_vision_verdict(
        image_url,
        is_couple=is_couple,
        source_image_urls=source_image_urls,
    )
    vision_ok = bool(vision_verdict["passed"])
    vision_reasons = list(vision_verdict["reasons"])
    if not vision_ok:
        blocking_reasons = blocking_vision_reasons(vision_reasons)
        if (settings.qa_fail_on_vision_error or identity_vision_required) and "vision_error" in blocking_reasons:
            reasons = ["vision_error"]
            return {
                "passed": False,
                "reasons": reasons,
                "issues": build_structured_qa_issues(reasons, source="vision", notes=vision_verdict.get("notes")),
                "local": local_verdict,
                "vision": vision_verdict,
                "notes": "identity_vision_required" if identity_vision_required else "vision_error_blocked",
            }
        if not blocking_reasons:
            return {
                "passed": True,
                "reasons": [],
                "issues": [],
                "local": local_verdict,
                "vision": vision_verdict,
                "notes": "vision_non_blocking",
            }
        if blocking_reasons == ["vision_error"]:
            return {
                "passed": True,
                "reasons": [],
                "issues": [],
                "local": local_verdict,
                "vision": vision_verdict,
                "notes": "vision_error_degraded",
            }
        combined: list[str] = []
        seen: set[str] = set()
        for reason in [*(local_verdict["reasons"] or []), *(blocking_reasons or vision_reasons)]:
            normalized = normalize_qa_reason(reason)
            if normalized not in seen:
                seen.add(normalized)
                combined.append(normalized)
        issues = build_structured_qa_issues(combined, source="vision", notes=vision_verdict.get("notes"))
        return {
            "passed": False,
            "reasons": combined,
            "issues": issues,
            "local": local_verdict,
            "vision": vision_verdict,
            "notes": "vision_qa_failed",
        }
    return {
        "passed": True,
        "reasons": [],
        "issues": [],
        "local": local_verdict,
        "vision": vision_verdict,
        "notes": "passed",
    }
