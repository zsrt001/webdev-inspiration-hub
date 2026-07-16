"""QA Service - output validation and retry guidance."""

from io import BytesIO
import httpx

try:
    from PIL import Image, ImageStat
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]
from app.core.config import get_settings
from app.schemas.qa import (
    QA_CHECKER_VERSION,
    QA_MODEL_VERSION,
    QA_SCHEMA_VERSION,
    StrictQaResponse,
    failed_qa_response,
)
from app.services import llm_service
from app.services.identity_embedding_service import identity_embedding_service
from app.services.photometric_qa_service import photometric_qa_service
from app.services.qa_pipeline import attach_identity_grade, blocking_vision_reasons as _blocking_vision_reasons
from app.services.qa_rules import build_structured_qa_issues, normalize_qa_reason, run_local_qa_rules

settings = get_settings()


def _normalized_dependency_reasons(payload: object, fallback: str) -> list[str]:
    if not isinstance(payload, dict) or type(payload.get("passed")) is not bool:
        return [fallback]
    raw = payload.get("reasons")
    reasons = raw if isinstance(raw, list) else []
    normalized = [normalize_qa_reason(str(reason)) for reason in reasons]
    normalized = list(dict.fromkeys(reason for reason in normalized if reason))[:16]
    if payload["passed"] is True:
        return [] if not normalized else [fallback]
    return normalized or [fallback]


def _typed_failed_response(reasons: list[str]) -> StrictQaResponse:
    normalized = list(dict.fromkeys(reasons))[:16]
    if not normalized:
        normalized = ["other"]
    failed_check = {
        "passed": False,
        "score": 0.0,
        "reason_codes": normalized,
    }
    return StrictQaResponse.model_validate(
        {
            "schema_version": QA_SCHEMA_VERSION,
            "checker_version": QA_CHECKER_VERSION,
            "model_version": QA_MODEL_VERSION,
            "passed": False,
            "reason_codes": normalized,
            "checks": {
                name: dict(failed_check)
                for name in (
                    "technical",
                    "identity",
                    "subject",
                    "safety",
                    "style",
                    "composition",
                    "exposure",
                    "watermark",
                )
            },
        }
    )


async def strict_output_verdict(
    candidate_bytes: bytes,
    *,
    candidate_url: str,
    source_image_urls: list[str],
    is_couple: bool,
    template_style_context: str | None = None,
) -> StrictQaResponse:
    """Run local, embedding, photometric, and strict semantic QA fail-closed."""

    if not all(
        (
            settings.qa_require_vision,
            settings.qa_require_identity_vision,
            settings.qa_require_identity_embedding,
            settings.qa_require_photometric,
        )
    ):
        return failed_qa_response("qa_strict_runtime_disabled")
    sources = [str(url).strip() for url in source_image_urls if str(url).strip()]
    expected_sources = 2 if is_couple else 1
    if len(sources) != expected_sources:
        return failed_qa_response("qa_source_identity_missing")
    if Image is None or ImageStat is None:
        return failed_qa_response("qa_local_checker_unavailable")
    try:
        with Image.open(BytesIO(bytes(candidate_bytes))) as decoded:
            decoded.load()
            local_reasons, _local_metrics = run_local_qa_rules(decoded.convert("RGB"))
    except Exception:
        return failed_qa_response("severe_artifacts")
    if local_reasons:
        return _typed_failed_response(list(local_reasons))

    try:
        identity_payload = await identity_embedding_service.verify_identity_similarity(
            candidate_url,
            is_couple=is_couple,
            source_image_urls=sources,
        )
        identity_reasons = _normalized_dependency_reasons(
            identity_payload,
            "identity_embedding_unavailable",
        )
    except Exception:
        identity_reasons = ["identity_embedding_unavailable"]
    try:
        photometric_payload = await photometric_qa_service.verify_lighting(
            candidate_url,
            is_couple=is_couple,
        )
        photometric_reasons = _normalized_dependency_reasons(
            photometric_payload,
            "photometric_qa_unavailable",
        )
    except Exception:
        photometric_reasons = ["photometric_qa_unavailable"]
    try:
        vision_payload = await llm_service.verify_generated_image_quality(
            candidate_url,
            is_couple=is_couple,
            source_image_urls=sources,
            template_style_context=template_style_context,
        )
    except Exception:
        return failed_qa_response("vision_error")
    contract_payload = (
        vision_payload.get("qa_contract")
        if isinstance(vision_payload, dict)
        else None
    )
    if not isinstance(contract_payload, dict):
        raw_reasons = vision_payload.get("reasons") if isinstance(vision_payload, dict) else None
        normalized = (
            [normalize_qa_reason(str(reason)) for reason in raw_reasons]
            if isinstance(raw_reasons, list)
            else []
        )
        return _typed_failed_response(normalized or ["vision_error"])
    try:
        strict = StrictQaResponse.model_validate(contract_payload)
    except Exception:
        return failed_qa_response("vision_schema_invalid")

    payload = strict.model_dump(mode="json")
    combined = list(strict.reason_codes)
    for check_name, reasons in (
        ("identity", identity_reasons),
        ("exposure", photometric_reasons),
    ):
        if not reasons:
            continue
        payload["checks"][check_name] = {
            "passed": False,
            "score": 0.0,
            "reason_codes": reasons,
        }
        combined.extend(reasons)
    combined = list(dict.fromkeys(combined))[:32]
    payload["passed"] = all(
        check["passed"] is True for check in payload["checks"].values()
    )
    payload["reason_codes"] = [] if payload["passed"] else (combined or ["other"])
    return StrictQaResponse.model_validate(payload)


def blocking_vision_reasons(reasons: list[str]) -> list[str]:
    return _blocking_vision_reasons(reasons)


async def basic_image_check(image_url: str) -> tuple[bool, list[str], dict[str, float]]:
    verdict = await basic_image_verdict(image_url)
    return verdict.get("passed") is True, list(verdict["reasons"]), dict(verdict["metrics"])


async def basic_image_verdict(image_url: str) -> dict:
    if Image is None or ImageStat is None:
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
    template_style_context: str | None = None,
) -> tuple[bool, list[str]]:
    verdict = await verify_with_vision_verdict(
        image_url,
        is_couple=is_couple,
        source_image_urls=source_image_urls,
        template_style_context=template_style_context,
    )
    return verdict.get("passed") is True, list(verdict["reasons"])


async def verify_with_vision_verdict(
    image_url: str,
    *,
    is_couple: bool = False,
    source_image_urls: list[str] | None = None,
    template_style_context: str | None = None,
) -> dict:
    source_images = [str(url).strip() for url in (source_image_urls or []) if str(url).strip()]
    identity_vision_required = bool(source_images) and bool(settings.qa_require_identity_vision)
    if not llm_service.is_vision_provider_configured():
        reasons = ["vision_error"]
        return attach_identity_grade({
            "passed": False,
            "reasons": reasons,
            "issues": build_structured_qa_issues(reasons, source="vision", notes="vision_not_configured"),
            "notes": "identity_vision_required" if identity_vision_required else "vision_not_configured",
            "source": "vision",
        }, is_couple=is_couple)
    verdict = await llm_service.verify_generated_image_quality(
        image_url,
        is_couple=is_couple,
        source_image_urls=source_image_urls,
        template_style_context=template_style_context,
    )
    if verdict.get("passed") is True:
        return attach_identity_grade(
            {"passed": True, "reasons": [], "issues": [], "notes": verdict.get("notes") or "", "source": "vision"},
            is_couple=is_couple,
        )
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
    return attach_identity_grade({
        "passed": False,
        "reasons": normalized,
        "issues": issues,
        "notes": str(verdict.get("notes") or ""),
        "source": "vision",
    }, is_couple=is_couple)


async def output_passes(
    image_url: str,
    *,
    is_couple: bool = False,
    source_image_urls: list[str] | None = None,
    template_style_context: str | None = None,
) -> tuple[bool, list[str]]:
    verdict = await output_verdict(
        image_url,
        is_couple=is_couple,
        source_image_urls=source_image_urls,
        template_style_context=template_style_context,
    )
    return verdict.get("passed") is True, list(verdict["reasons"])


async def output_verdict(
    image_url: str,
    *,
    is_couple: bool = False,
    source_image_urls: list[str] | None = None,
    template_style_context: str | None = None,
) -> dict:
    source_images = [str(url).strip() for url in (source_image_urls or []) if str(url).strip()]
    identity_vision_required = bool(source_images) and bool(settings.qa_require_identity_vision)
    local_verdict = await basic_image_verdict(image_url)
    if local_verdict.get("passed") is not True:
        return attach_identity_grade({
            "passed": False,
            "reasons": local_verdict["reasons"],
            "issues": local_verdict["issues"],
            "local": local_verdict,
            "vision": None,
            "notes": "local_qa_failed",
        }, is_couple=is_couple)

    identity_embedding_verdict = None
    if source_images and bool(settings.qa_require_identity_embedding):
        identity_embedding_verdict = await identity_embedding_service.verify_identity_similarity(
            image_url,
            is_couple=is_couple,
            source_image_urls=source_images,
        )
        if identity_embedding_verdict.get("passed") is not True:
            dependency_reasons = list(
                identity_embedding_verdict.get("reasons")
                if isinstance(identity_embedding_verdict.get("reasons"), list)
                else []
            ) or ["identity_embedding_unavailable"]
            return attach_identity_grade({
                "passed": False,
                "reasons": dependency_reasons,
                "issues": [
                    issue
                    for issue in (identity_embedding_verdict.get("issues") or [])
                    if isinstance(issue, dict)
                ],
                "local": local_verdict,
                "identity_embedding": identity_embedding_verdict,
                "vision": None,
                "notes": "identity_embedding_qa_failed",
            }, is_couple=is_couple)

    photometric_verdict = None
    if bool(settings.qa_require_photometric):
        photometric_verdict = await photometric_qa_service.verify_lighting(
            image_url,
            is_couple=is_couple,
        )
        if photometric_verdict.get("passed") is not True:
            dependency_reasons = list(
                photometric_verdict.get("reasons")
                if isinstance(photometric_verdict.get("reasons"), list)
                else []
            ) or ["photometric_qa_unavailable"]
            return attach_identity_grade({
                "passed": False,
                "reasons": dependency_reasons,
                "issues": [
                    issue
                    for issue in (photometric_verdict.get("issues") or [])
                    if isinstance(issue, dict)
                ],
                "local": local_verdict,
                "identity_embedding": identity_embedding_verdict,
                "photometric": photometric_verdict,
                "vision": None,
                "notes": "photometric_qa_failed",
            }, is_couple=is_couple)

    vision_verdict = await verify_with_vision_verdict(
        image_url,
        is_couple=is_couple,
        source_image_urls=source_image_urls,
        template_style_context=template_style_context,
    )
    vision_ok = vision_verdict.get("passed") is True
    vision_reasons = list(vision_verdict["reasons"])
    if not vision_ok:
        blocking_reasons = blocking_vision_reasons(vision_reasons)
        combined: list[str] = []
        seen: set[str] = set()
        for reason in [*(local_verdict["reasons"] or []), *(blocking_reasons or vision_reasons)]:
            normalized = normalize_qa_reason(reason)
            if normalized not in seen:
                seen.add(normalized)
                combined.append(normalized)
        combined = combined or ["vision_error"]
        issues = build_structured_qa_issues(combined, source="vision", notes=vision_verdict.get("notes"))
        return attach_identity_grade({
            "passed": False,
            "reasons": combined,
            "issues": issues,
            "local": local_verdict,
            "identity_embedding": identity_embedding_verdict,
            "photometric": photometric_verdict,
            "vision": vision_verdict,
            "notes": "vision_qa_failed",
        }, is_couple=is_couple)
    return attach_identity_grade({
        "passed": True,
        "reasons": [],
        "issues": [],
        "local": local_verdict,
        "identity_embedding": identity_embedding_verdict,
        "photometric": photometric_verdict,
        "vision": vision_verdict,
        "notes": "passed",
    }, is_couple=is_couple)
