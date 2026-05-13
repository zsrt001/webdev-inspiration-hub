"""Order creation orchestration.

This service keeps the API route thin while preserving the commercial order
contract: policy checks, photo gatekeeping, credit charging, retention, and
generation dispatch all succeed or fail as one unit.
"""

import uuid
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.task_queue import enqueue_generate_order
from app.models.credit_transaction import CreditTransactionType
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.schemas.order import OrderCreate
from app.services import gatekeeper_service
from app.services.content_policy_service import build_rejection_message, evaluate_prompt_text
from app.services.credit_service import (
    add_credits_async,
    deduct_credits_async,
    get_balance_async,
    get_generation_cost,
)
from app.services.generation_service import generation_service
from app.services.preset_service import (
    get_outfit_preset,
    get_scene_preset,
    random_outfit_preset,
    random_scene_preset,
    to_public_url,
)
from app.services.retention_service import apply_order_retention, user_has_paid_credit_history
from app.services.subscription_service import subscription_service
from app.services.template_service import get_template_by_id
from app.services.trial_access_service import (
    TRIAL_ALLOWED_MAX_CREDITS,
    _trial_daily_generation_limit,
    access_tier_for_order,
    trial_generation_allowed,
)
from app.worker_tasks import run_order_generation


settings = get_settings()


@dataclass(slots=True)
class CreditAccessContext:
    credits_cost: int
    access_tier: str
    has_paid_credits: bool
    retention_plan_code: str | None


@dataclass(slots=True)
class DirectorDecision:
    global_style_text: str | None
    scene_text: str | None
    outfit_text: str | None
    legacy_prompt_override: str | None
    effective_scene_source: str | None
    effective_outfit_source: str | None
    effective_scene_image_url: str | None
    effective_clothing_image_url: str | None
    effective_scene_ip_weight: float | None
    effective_clothing_ip_weight: float | None
    effective_scene_preset_id: str | None
    effective_outfit_preset_id: str | None
    effective_scene_preset_title: str | None
    effective_outfit_preset_title: str | None
    ignored_inputs: list[str]
    director_decision_hints: list[str]


async def create_order_for_user(
    request: OrderCreate,
    current_user: User,
    db: AsyncSession,
    *,
    background_tasks: BackgroundTasks | None = None,
) -> Order:
    """Validate, charge, persist, and dispatch a generation order."""
    _validate_runtime_requirements()
    _validate_request_basics(request)
    _enforce_prompt_policy(request)

    gatekeeper_results = await _run_gatekeeper_checks(request.user_images)
    template = get_template_by_id(request.template_id)
    subject_images = [image for image in request.user_images if image]
    subject_count = len(subject_images)
    is_couple_request = subject_count >= 2
    couple_flow = "remote" if bool(request.remote_join) and is_couple_request else (
        "local" if is_couple_request else None
    )
    if is_couple_request:
        _validate_distinct_couple_subjects(subject_images)
    request_fingerprint = _request_fingerprint(request)
    await _enforce_active_order_limit(db, current_user.id, request_fingerprint)

    credits_cost = get_generation_cost(
        template.category if template else None,
        is_remote_join=bool(request.remote_join),
        image_count=len(request.user_images or []),
        director_mode=bool(request.director_mode),
    )
    credit_context = await _resolve_credit_access(
        db,
        current_user.id,
        request,
        template.category if template else None,
        credits_cost,
    )
    director_decision = _resolve_director_decision(
        request,
        is_couple_request=is_couple_request,
        couple_flow=couple_flow,
    )

    charged = False
    generation_state = {"started": False}
    order: Order | None = None
    try:
        await _charge_generation_credits(db, current_user.id, request, credit_context)
        charged = True
        order = _build_order(
            request=request,
            current_user=current_user,
            gatekeeper_results=gatekeeper_results,
            subject_count=subject_count,
            is_couple_request=is_couple_request,
            couple_flow=couple_flow,
            credit_context=credit_context,
            director_decision=director_decision,
        )
        await _persist_order_with_retention(db, order, credit_context)
        await _dispatch_generation(db, order, generation_state, background_tasks=background_tasks)
    except Exception:
        if charged and not generation_state["started"]:
            await add_credits_async(
                db,
                current_user.id,
                credit_context.credits_cost,
                transaction_type=CreditTransactionType.GENERATION_REFUND,
                source="order",
                source_id=str(order.id) if order is not None and order.id else None,
                description="Generation failed before queue start; credits refunded",
            )
            await db.commit()
        raise

    return order


def normalize_identity_image_url(image_url: str) -> str:
    raw = str(image_url or "").strip()
    if not raw:
        return ""
    lower_raw = raw.lower()
    if lower_raw.startswith("http://") or lower_raw.startswith("https://"):
        parts = urlsplit(raw)
        normalized_path = parts.path.rstrip("/") or parts.path
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), normalized_path, "", ""))
    return raw


def _request_fingerprint(request: OrderCreate) -> str:
    payload = {
        "template_id": request.template_id,
        "user_images": [normalize_identity_image_url(image) for image in (request.user_images or [])],
        "director_mode": bool(request.director_mode),
        "remote_join": bool(request.remote_join),
        "global_style_text": request.global_style_text or None,
        "scene_text": request.scene_text or None,
        "outfit_text": request.outfit_text or None,
        "scene_preset_id": request.scene_preset_id or None,
        "clothing_preset_id": request.clothing_preset_id or None,
        "prompt_override": request.prompt_override or None,
        "scene_image_url": normalize_identity_image_url(request.scene_image_url or ""),
        "clothing_image_url": normalize_identity_image_url(request.clothing_image_url or ""),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _enforce_active_order_limit(
    db: AsyncSession,
    user_id: uuid.UUID,
    request_fingerprint: str,
) -> None:
    limit = max(0, int(settings.order_active_user_limit or 0))
    if limit <= 0:
        return

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(settings.order_active_window_minutes or 45)))
    result = await db.execute(
        select(Order)
        .where(
            Order.user_id == user_id,
            Order.deleted_at.is_(None),
            Order.status.in_([OrderStatus.CHECKING, OrderStatus.GENERATING]),
            Order.updated_at >= cutoff,
        )
        .order_by(Order.updated_at.desc(), Order.created_at.desc())
        .limit(limit)
    )
    active_orders = result.scalars().all()
    if not active_orders:
        return

    existing = active_orders[0]
    params = dict(existing.generation_params) if isinstance(existing.generation_params, dict) else {}
    if params.get("request_fingerprint") == request_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "generation_already_in_progress",
                "message": "This generation is already in progress. Opening the existing order.",
                "existing_order_id": str(existing.id),
                "status": existing.status.value if isinstance(existing.status, OrderStatus) else str(existing.status),
                "reused": True,
            },
        )

    raise HTTPException(
        status_code=409,
        detail={
            "error": "generation_already_in_progress",
            "message": "One generation is already in progress. Please wait for it to finish before starting another.",
            "existing_order_id": str(existing.id),
            "status": existing.status.value if isinstance(existing.status, OrderStatus) else str(existing.status),
            "reused": False,
        },
    )


def build_director_decision_hints(
    *,
    director_mode: bool,
    effective_scene_source: str | None,
    effective_outfit_source: str | None,
    ignored_inputs: list[str],
    effective_scene_preset_title: str | None,
    effective_outfit_preset_title: str | None,
    effective_scene_ip_weight: float | None,
    effective_outfit_ip_weight: float | None,
    is_couple_request: bool,
    couple_flow: str | None,
) -> list[str]:
    hints: list[str] = []
    if director_mode:
        hints.append("director_mode_enabled")
    if effective_scene_source:
        scene_hint = f"scene:{effective_scene_source}"
        if effective_scene_preset_title and effective_scene_source in {"preset", "random"}:
            scene_hint += f":{effective_scene_preset_title}"
        if effective_scene_ip_weight is not None and effective_scene_source in {"upload", "preset", "random"}:
            scene_hint += f":w={effective_scene_ip_weight:.2f}"
        hints.append(scene_hint)
    if effective_outfit_source:
        outfit_hint = f"outfit:{effective_outfit_source}"
        if effective_outfit_preset_title and effective_outfit_source in {"preset", "random"}:
            outfit_hint += f":{effective_outfit_preset_title}"
        if effective_outfit_ip_weight is not None and effective_outfit_source in {"upload", "preset", "random"}:
            outfit_hint += f":w={effective_outfit_ip_weight:.2f}"
        hints.append(outfit_hint)
    if ignored_inputs:
        hints.append("ignored:" + ",".join(sorted(set(ignored_inputs))))
    if is_couple_request:
        hints.append(f"couple:{couple_flow or 'local'}")
    return hints


def _validate_runtime_requirements() -> None:
    try:
        generation_service.validate_runtime_requirements()
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_runtime_invalid",
                "message": f"Generation runtime invalid: {e}",
            },
        ) from e


def _validate_request_basics(request: OrderCreate) -> None:
    if not request.user_images or not request.user_images[0]:
        raise HTTPException(status_code=400, detail="Missing user image")
    if not request.legal_accepted:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "legal_consent_required",
                "message": "Privacy Policy and Terms of Service must be accepted before generation.",
            },
        )
    if request.remote_join and not settings.remote_join_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "remote_join_disabled",
                "message": "Remote join is disabled in the current commercial deployment.",
            },
        )


def _enforce_prompt_policy(request: OrderCreate) -> None:
    policy_text_segments = [
        request.prompt_override,
        request.global_style_text,
        request.scene_text,
        request.outfit_text,
    ]
    policy_verdict = evaluate_prompt_text("\n".join(segment for segment in policy_text_segments if segment))
    if not policy_verdict.passed:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "content_policy_reject",
                "message": build_rejection_message(policy_verdict),
                "reason": policy_verdict.reason,
                "category": policy_verdict.category,
                "categories": policy_verdict.categories,
                "matched_terms": policy_verdict.matched_terms,
            },
        )


async def _run_gatekeeper_checks(user_images: list[str]) -> list[dict]:
    gatekeeper_results: list[dict] = []
    for idx, image_url in enumerate(user_images):
        if not image_url:
            raise HTTPException(status_code=400, detail=f"Missing user image at index {idx}")
        try:
            verdict = await gatekeeper_service.check_image_quality(image_url)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail={"error": "gatekeeper_fetch_failed", "index": idx, "message": str(e)},
            ) from e
        gatekeeper_results.append(verdict.model_dump())
        if not verdict.passed:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "gatekeeper_reject",
                    "index": idx,
                    "reasons": verdict.reasons,
                    "advice": verdict.advice,
                    "metrics": verdict.metrics,
                    "risk_flags": getattr(verdict, "risk_flags", []),
                },
            )
    return gatekeeper_results


def _validate_distinct_couple_subjects(subject_images: list[str]) -> None:
    normalized_subject_images = [normalize_identity_image_url(image) for image in subject_images]
    if len(set(normalized_subject_images)) < 2:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "duplicate_subject_images",
                "message": "Couple mode requires two different portrait images.",
            },
        )


async def _resolve_credit_access(
    db: AsyncSession,
    user_id: uuid.UUID,
    request: OrderCreate,
    template_category: str | None,
    credits_cost: int,
) -> CreditAccessContext:
    retention_subscription = await subscription_service.get_current_subscription(db, user_id)
    retention_plan_code = getattr(getattr(retention_subscription, "plan", None), "code", None)
    has_paid_credits = bool(retention_plan_code) or await user_has_paid_credit_history(db, user_id)
    access_tier = access_tier_for_order(has_paid_credits=has_paid_credits)
    if not has_paid_credits:
        if not trial_generation_allowed(
            template_category=template_category,
            is_remote_join=bool(request.remote_join),
            image_count=len(request.user_images or []),
            director_mode=bool(request.director_mode),
            credits_cost=credits_cost,
        ):
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "trial_mode_requires_top_up",
                    "message": "Starter credits cover one base single portrait only. Please top up for couple, remote, vintage, or director mode.",
                    "required": credits_cost,
                    "trial_allowed_max": TRIAL_ALLOWED_MAX_CREDITS,
                },
            )
        await _enforce_trial_generation_limit(db, user_id)
    return CreditAccessContext(
        credits_cost=credits_cost,
        access_tier=access_tier,
        has_paid_credits=has_paid_credits,
        retention_plan_code=retention_plan_code,
    )


async def _enforce_trial_generation_limit(db: AsyncSession, user_id: uuid.UUID) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    count = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                Order.user_id == user_id,
                Order.created_at >= since,
                Order.generation_params["access_tier"].astext == "trial_preview",
            )
        )
        or 0
    )
    if count >= _trial_daily_generation_limit():
        raise HTTPException(
            status_code=429,
            detail={
                "error": "trial_daily_limit_reached",
                "message": "Free preview quota reached today. Please top up to continue.",
                "limit": _trial_daily_generation_limit(),
            },
        )


async def _charge_generation_credits(
    db: AsyncSession,
    user_id: uuid.UUID,
    request: OrderCreate,
    credit_context: CreditAccessContext,
) -> None:
    if await deduct_credits_async(
        db,
        user_id,
        credit_context.credits_cost,
        transaction_type=CreditTransactionType.GENERATION_DEBIT,
        source="order",
        description=f"Generation debit for template {request.template_id}",
        metadata={
            "template_id": request.template_id,
            "remote_join": bool(request.remote_join),
            "director_mode": bool(request.director_mode),
            "credits_cost": credit_context.credits_cost,
            "access_tier": credit_context.access_tier,
        },
    ):
        return

    balance = await get_balance_async(db, user_id)
    raise HTTPException(
        status_code=402,
        detail={
            "error": "insufficient_credits",
            "message": "Not enough credits. Please top up to continue.",
            "required": credit_context.credits_cost,
            "balance": balance,
        },
    )


def _resolve_director_decision(
    request: OrderCreate,
    *,
    is_couple_request: bool,
    couple_flow: str | None,
) -> DirectorDecision:
    global_style_text = (request.global_style_text or "").strip() or None
    scene_text = (request.scene_text or "").strip() or None
    outfit_text = (request.outfit_text or "").strip() or None
    legacy_prompt_override = (request.prompt_override or "").strip() or None

    legacy_text_mode = bool(legacy_prompt_override and not any([global_style_text, scene_text, outfit_text]))
    scene_text_present = bool(scene_text or legacy_text_mode)
    outfit_text_present = bool(outfit_text or legacy_text_mode)
    apply_director_cascade = bool(
        request.director_mode
        or request.scene_preset_id
        or request.clothing_preset_id
        or request.scene_image_url
        or request.clothing_image_url
        or scene_text_present
        or outfit_text_present
    )
    ignored_inputs: list[str] = []

    effective_scene_source: str | None = "upload" if request.scene_image_url else None
    effective_outfit_source: str | None = "upload" if request.clothing_image_url else None
    effective_scene_image_url: str | None = request.scene_image_url
    effective_clothing_image_url: str | None = request.clothing_image_url
    effective_scene_ip_weight: float | None = request.scene_ip_weight
    effective_clothing_ip_weight: float | None = request.clothing_ip_weight
    effective_scene_preset_id: str | None = None
    effective_outfit_preset_id: str | None = None
    effective_scene_preset_title: str | None = None
    effective_outfit_preset_title: str | None = None

    if apply_director_cascade:
        if request.scene_image_url:
            effective_scene_source = "upload"
            effective_scene_image_url = request.scene_image_url
            effective_scene_ip_weight = request.scene_ip_weight if request.scene_ip_weight is not None else 0.6
            if scene_text_present:
                ignored_inputs.append("scene_text")
            if request.scene_preset_id:
                ignored_inputs.append("scene_preset_id")
        elif scene_text_present:
            effective_scene_source = "text"
            effective_scene_image_url = None
            effective_scene_ip_weight = None
            if request.scene_preset_id:
                ignored_inputs.append("scene_preset_id")
        else:
            preset = get_scene_preset(request.scene_preset_id) if request.scene_preset_id else None
            if preset:
                effective_scene_source = "preset"
            else:
                preset = random_scene_preset()
                effective_scene_source = "random"
            effective_scene_preset_id = preset["id"]
            effective_scene_preset_title = preset["title"]
            effective_scene_image_url = to_public_url(preset["image_url"])
            effective_scene_ip_weight = request.scene_ip_weight if request.scene_ip_weight is not None else 0.5

        if request.clothing_image_url:
            effective_outfit_source = "upload"
            effective_clothing_image_url = request.clothing_image_url
            effective_clothing_ip_weight = request.clothing_ip_weight if request.clothing_ip_weight is not None else 0.6
            if outfit_text_present:
                ignored_inputs.append("outfit_text")
            if request.clothing_preset_id:
                ignored_inputs.append("clothing_preset_id")
        elif outfit_text_present:
            effective_outfit_source = "text"
            effective_clothing_image_url = None
            effective_clothing_ip_weight = None
            if request.clothing_preset_id:
                ignored_inputs.append("clothing_preset_id")
        else:
            preset = get_outfit_preset(request.clothing_preset_id) if request.clothing_preset_id else None
            if preset:
                effective_outfit_source = "preset"
            else:
                preset = random_outfit_preset()
                effective_outfit_source = "random"
            effective_outfit_preset_id = preset["id"]
            effective_outfit_preset_title = preset["title"]
            effective_clothing_image_url = to_public_url(preset["image_url"])
            effective_clothing_ip_weight = request.clothing_ip_weight if request.clothing_ip_weight is not None else 0.5

    director_decision_hints = build_director_decision_hints(
        director_mode=bool(request.director_mode),
        effective_scene_source=effective_scene_source,
        effective_outfit_source=effective_outfit_source,
        ignored_inputs=ignored_inputs,
        effective_scene_preset_title=effective_scene_preset_title,
        effective_outfit_preset_title=effective_outfit_preset_title,
        effective_scene_ip_weight=effective_scene_ip_weight,
        effective_outfit_ip_weight=effective_clothing_ip_weight,
        is_couple_request=is_couple_request,
        couple_flow=couple_flow,
    )
    return DirectorDecision(
        global_style_text=global_style_text,
        scene_text=scene_text,
        outfit_text=outfit_text,
        legacy_prompt_override=legacy_prompt_override,
        effective_scene_source=effective_scene_source,
        effective_outfit_source=effective_outfit_source,
        effective_scene_image_url=effective_scene_image_url,
        effective_clothing_image_url=effective_clothing_image_url,
        effective_scene_ip_weight=effective_scene_ip_weight,
        effective_clothing_ip_weight=effective_clothing_ip_weight,
        effective_scene_preset_id=effective_scene_preset_id,
        effective_outfit_preset_id=effective_outfit_preset_id,
        effective_scene_preset_title=effective_scene_preset_title,
        effective_outfit_preset_title=effective_outfit_preset_title,
        ignored_inputs=ignored_inputs,
        director_decision_hints=director_decision_hints,
    )


def _build_order(
    *,
    request: OrderCreate,
    current_user: User,
    gatekeeper_results: list[dict],
    subject_count: int,
    is_couple_request: bool,
    couple_flow: str | None,
    credit_context: CreditAccessContext,
    director_decision: DirectorDecision,
) -> Order:
    generation_params = _build_generation_params(
        request=request,
        gatekeeper_results=gatekeeper_results,
        subject_count=subject_count,
        is_couple_request=is_couple_request,
        couple_flow=couple_flow,
        credit_context=credit_context,
        director_decision=director_decision,
    )
    return Order(
        user_id=current_user.id,
        status=OrderStatus.CHECKING,
        template_id=request.template_id,
        source_image_urls={"images": request.user_images},
        generation_params=generation_params,
        price_cents=0,
    )


def _build_generation_params(
    *,
    request: OrderCreate,
    gatekeeper_results: list[dict],
    subject_count: int,
    is_couple_request: bool,
    couple_flow: str | None,
    credit_context: CreditAccessContext,
    director_decision: DirectorDecision,
) -> dict:
    distinct_subject_images = (
        len({normalize_identity_image_url(image) for image in request.user_images if image})
        if is_couple_request
        else subject_count
    )
    return {
        "credits_cost": credit_context.credits_cost,
        "request_fingerprint": _request_fingerprint(request),
        "access_tier": credit_context.access_tier,
        "download_locked": not credit_context.has_paid_credits,
        "gatekeeper": {"passed": True, "images": gatekeeper_results},
        "quality_control": {
            "preflight": {
                "gatekeeper_required": True,
                "gatekeeper_passed": True,
                "identity_reference_count": subject_count,
                "distinct_identity_reference_count": distinct_subject_images,
            },
            "generation": {
                "identity_edit_required": bool(settings.wenwen_require_image_edit_identity),
                "identity_references_first": True,
                "style_references_are_not_identities": True,
                "studio_prompt_guardrails": True,
            },
            "postcheck": {
                "local_qa_required": True,
                "vision_qa_required_for_identity": bool(settings.qa_require_identity_vision),
                "vision_provider_error_blocks_delivery": bool(settings.qa_fail_on_vision_error),
                "qa_retry_max_attempts": settings.generation_max_retries + 1,
            },
        },
        "remote_join": bool(request.remote_join),
        "couple_flow": couple_flow,
        "subject_count": subject_count,
        "director_mode": bool(request.director_mode),
        "content_policy": {"passed": True},
        "prompt_override": director_decision.legacy_prompt_override,
        "global_style_text": director_decision.global_style_text,
        "scene_text": director_decision.scene_text,
        "outfit_text": director_decision.outfit_text,
        "scene_image_url": request.scene_image_url,
        "clothing_image_url": request.clothing_image_url,
        "scene_preset_id": request.scene_preset_id,
        "clothing_preset_id": request.clothing_preset_id,
        "effective_scene_source": director_decision.effective_scene_source,
        "effective_outfit_source": director_decision.effective_outfit_source,
        "ignored_inputs": sorted(set(director_decision.ignored_inputs)),
        "effective_scene_preset_id": director_decision.effective_scene_preset_id,
        "effective_outfit_preset_id": director_decision.effective_outfit_preset_id,
        "effective_scene_preset_title": director_decision.effective_scene_preset_title,
        "effective_outfit_preset_title": director_decision.effective_outfit_preset_title,
        "effective_scene_image_url": director_decision.effective_scene_image_url,
        "effective_clothing_image_url": director_decision.effective_clothing_image_url,
        "effective_scene_ip_weight": director_decision.effective_scene_ip_weight,
        "effective_outfit_ip_weight": director_decision.effective_clothing_ip_weight,
        "director_decision_hints": director_decision.director_decision_hints,
        "director_summary": {
            "scene": {
                "source": director_decision.effective_scene_source,
                "preset_id": director_decision.effective_scene_preset_id,
                "preset_title": director_decision.effective_scene_preset_title,
                "text_applied": bool(director_decision.scene_text),
                "upload_applied": bool(request.scene_image_url),
                "ip_weight": director_decision.effective_scene_ip_weight,
            },
            "outfit": {
                "source": director_decision.effective_outfit_source,
                "preset_id": director_decision.effective_outfit_preset_id,
                "preset_title": director_decision.effective_outfit_preset_title,
                "text_applied": bool(director_decision.outfit_text),
                "upload_applied": bool(request.clothing_image_url),
                "ip_weight": director_decision.effective_clothing_ip_weight,
            },
            "global_style_text_applied": bool(
                director_decision.global_style_text or director_decision.legacy_prompt_override
            ),
            "text_segments": {
                "global_style_text": director_decision.global_style_text,
                "scene_text": director_decision.scene_text,
                "outfit_text": director_decision.outfit_text,
            },
        },
        "couple_guardrails": {
            "is_couple": is_couple_request,
            "couple_flow": couple_flow,
            "dual_face_input": is_couple_request,
            "remote_join": bool(request.remote_join),
            "distinct_subject_images": distinct_subject_images,
        },
        "pose_image_url": request.pose_image_url,
        "depth_image_url": request.depth_image_url,
        "normal_image_url": request.normal_image_url,
        "scene_ip_weight": director_decision.effective_scene_ip_weight,
        "clothing_ip_weight": director_decision.effective_clothing_ip_weight,
        "face_ip_weight": request.face_ip_weight,
        "pose_cn_weight": request.pose_cn_weight,
        "depth_cn_weight": request.depth_cn_weight,
        "normal_cn_weight": request.normal_cn_weight,
        "pose_cn_start": request.pose_cn_start,
        "pose_cn_end": request.pose_cn_end,
        "depth_cn_start": request.depth_cn_start,
        "depth_cn_end": request.depth_cn_end,
        "normal_cn_start": request.normal_cn_start,
        "normal_cn_end": request.normal_cn_end,
    }


async def _persist_order_with_retention(
    db: AsyncSession,
    order: Order,
    credit_context: CreditAccessContext,
) -> None:
    apply_order_retention(
        order,
        plan_code=credit_context.retention_plan_code,
        has_paid_credits=credit_context.has_paid_credits,
    )
    order.generation_params = {
        **(order.generation_params or {}),
        "retention": {
            "plan_code": credit_context.retention_plan_code,
            "has_paid_credits": credit_context.has_paid_credits,
            "source_images_expires_at": order.source_images_expires_at.isoformat()
            if order.source_images_expires_at
            else None,
            "expires_at": order.expires_at.isoformat() if order.expires_at else None,
        },
        "entitlement": {
            "access_tier": credit_context.access_tier,
            "download_locked": not credit_context.has_paid_credits,
            "trial_daily_limit": _trial_daily_generation_limit() if not credit_context.has_paid_credits else None,
        },
    }
    db.add(order)
    await db.flush()
    await db.commit()
    await db.refresh(order)


async def _dispatch_generation(
    db: AsyncSession,
    order: Order,
    generation_state: dict[str, bool],
    *,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    base_params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
    if settings.using_inline_generation_execution:
        inline_task_id = f"inline-{order.id}"
        if settings.is_vercel_runtime and background_tasks is not None:
            base_params["queue_job_id"] = inline_task_id
            base_params["execution_mode"] = "inline_background"
            base_params["inline_background_started_at"] = datetime.now(timezone.utc).isoformat()
            order.generation_params = base_params
            order.task_id = inline_task_id
            order.status = OrderStatus.GENERATING
            await db.commit()
            await db.refresh(order)
            generation_state["started"] = True
            background_tasks.add_task(run_order_generation, str(order.id))
            return
        base_params["queue_job_id"] = inline_task_id
        base_params["execution_mode"] = "inline"
        order.generation_params = base_params
        order.task_id = inline_task_id
        order.status = OrderStatus.GENERATING
        await db.commit()
        generation_state["started"] = True
        await run_order_generation(str(order.id))
        await db.refresh(order)
        return

    try:
        queue_job_id = await enqueue_generate_order(str(order.id))
        generation_state["started"] = True
    except Exception as e:
        order.status = OrderStatus.CREATED
        order.error_message = f"queue_unavailable: {e}"
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "error": "queue_unavailable",
                "message": "Generation queue unavailable. Please try again later.",
            },
        ) from e

    base_params["queue_job_id"] = queue_job_id
    base_params["execution_mode"] = "arq"
    order.generation_params = base_params
    order.task_id = queue_job_id
    order.status = OrderStatus.GENERATING
    await db.commit()
