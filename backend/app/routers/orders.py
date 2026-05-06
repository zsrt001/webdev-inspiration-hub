"""Orders API routes using database-backed state machine."""

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.user_auth import get_request_user
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.schemas.order import OrderCreate, OrderRead
from app.core.config import get_settings
from app.services.generation_service import generation_service
from app.services.template_service import get_template_by_id
from app.services.credit_service import (
    deduct_credits_async,
    get_balance_async,
    add_credits_async,
    get_generation_cost,
)
from app.models.credit_transaction import CreditTransactionType
from app.services.retention_service import apply_order_retention, delete_storage_urls, order_asset_urls, user_has_paid_credit_history
from app.services.subscription_service import subscription_service
from app.services.trial_access_service import (
    _trial_daily_generation_limit,
    access_tier_for_order,
    can_download_order,
)
from app.services import gatekeeper_service
from app.services.content_policy_service import evaluate_prompt_text, build_rejection_message
from app.core.task_queue import enqueue_generate_order
from app.worker_tasks import run_order_generation
from app.services.preset_service import (
    get_outfit_preset,
    get_scene_preset,
    random_outfit_preset,
    random_scene_preset,
    to_public_url,
)

settings = get_settings()

router = APIRouter()


async def _serialize_order_for_user(db: AsyncSession, order: Order, user_id: uuid.UUID) -> OrderRead:
    has_paid_credits = await user_has_paid_credit_history(db, user_id)
    can_download = can_download_order(order.generation_params, has_paid_credits=has_paid_credits)
    payload = OrderRead.model_validate(order)
    payload.can_download = can_download
    payload.download_locked = not can_download
    if not can_download:
        payload.final_image_urls = None
    return payload


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


def _normalize_identity_image_url(image_url: str) -> str:
    raw = str(image_url or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        parts = urlsplit(raw)
        normalized_path = parts.path.rstrip("/") or parts.path
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), normalized_path, "", ""))
    return raw


def _build_director_decision_hints(
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


@router.get("/", response_model=list[OrderRead])
async def list_orders(
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all orders for current user (newest first)."""
    result = await db.execute(
        select(Order)
        .where(Order.user_id == current_user.id, Order.deleted_at.is_(None))
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()
    return [await _serialize_order_for_user(db, o, current_user.id) for o in orders]


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: str,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific order by ID."""
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")
    result = await db.execute(select(Order).where(Order.id == order_uuid))
    order = result.scalar_one_or_none()
    if not order or order.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    return await _serialize_order_for_user(db, order, current_user.id)


@router.post("/create", response_model=OrderRead)
@router.post("", response_model=OrderRead)
async def create_order(
    request: OrderCreate,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create order and enqueue generation.
    """
    try:
        generation_service.validate_runtime_requirements()
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_runtime_invalid",
                "message": f"Generation runtime invalid: {e}",
            },
        )

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

    gatekeeper_results: list[dict] = []
    for idx, image_url in enumerate(request.user_images):
        if not image_url:
            raise HTTPException(status_code=400, detail=f"Missing user image at index {idx}")
        try:
            verdict = await gatekeeper_service.check_image_quality(image_url)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail={"error": "gatekeeper_fetch_failed", "index": idx, "message": str(e)},
            )
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

    template = get_template_by_id(request.template_id)
    subject_count = len([image for image in request.user_images if image])
    is_couple_request = subject_count >= 2
    couple_flow = "remote" if bool(request.remote_join) and is_couple_request else ("local" if is_couple_request else None)
    if is_couple_request:
        normalized_subject_images = [_normalize_identity_image_url(image) for image in request.user_images if image]
        if len(set(normalized_subject_images)) < 2:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "duplicate_subject_images",
                    "message": "Couple mode requires two different portrait images.",
                },
            )
    credits_cost = get_generation_cost(
        template.category if template else None,
        is_remote_join=bool(request.remote_join),
        image_count=len(request.user_images or []),
        director_mode=bool(request.director_mode),
    )
    retention_subscription = await subscription_service.get_current_subscription(db, current_user.id)
    retention_plan_code = getattr(getattr(retention_subscription, "plan", None), "code", None)
    has_paid_credits = bool(retention_plan_code) or await user_has_paid_credit_history(db, current_user.id)
    access_tier = access_tier_for_order(has_paid_credits=has_paid_credits)
    if not has_paid_credits:
        await _enforce_trial_generation_limit(db, current_user.id)

    if not await deduct_credits_async(
        db,
        current_user.id,
        credits_cost,
        transaction_type=CreditTransactionType.GENERATION_DEBIT,
        source="order",
        description=f"Generation debit for template {request.template_id}",
        metadata={
            "template_id": request.template_id,
            "remote_join": bool(request.remote_join),
            "director_mode": bool(request.director_mode),
            "credits_cost": credits_cost,
            "access_tier": access_tier,
        },
    ):
        balance = await get_balance_async(db, current_user.id)
        raise HTTPException(
            status_code=402,
            detail={
                "error": "insufficient_credits",
                "message": "Not enough credits. Please top up to continue.",
                "required": credits_cost,
                "balance": balance,
            },
        )

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
        # Scene: Upload > Text > Preset > Random
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

        # Outfit: Upload > Text > Preset > Random
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
    director_decision_hints = _build_director_decision_hints(
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
    generation_started = False
    try:
        order = Order(
            user_id=current_user.id,
            status=OrderStatus.CHECKING,
            template_id=request.template_id,
            source_image_urls={"images": request.user_images},
            generation_params={
                "credits_cost": credits_cost,
                "access_tier": access_tier,
                "download_locked": not has_paid_credits,
                "gatekeeper": {"passed": True, "images": gatekeeper_results},
                "remote_join": bool(request.remote_join),
                "couple_flow": couple_flow,
                "subject_count": subject_count,
                "director_mode": bool(request.director_mode),
                "content_policy": {"passed": True},
                "prompt_override": legacy_prompt_override,
                "global_style_text": global_style_text,
                "scene_text": scene_text,
                "outfit_text": outfit_text,
                "scene_image_url": request.scene_image_url,
                "clothing_image_url": request.clothing_image_url,
                "scene_preset_id": request.scene_preset_id,
                "clothing_preset_id": request.clothing_preset_id,
                "effective_scene_source": effective_scene_source,
                "effective_outfit_source": effective_outfit_source,
                "ignored_inputs": sorted(set(ignored_inputs)),
                "effective_scene_preset_id": effective_scene_preset_id,
                "effective_outfit_preset_id": effective_outfit_preset_id,
                "effective_scene_preset_title": effective_scene_preset_title,
                "effective_outfit_preset_title": effective_outfit_preset_title,
                "effective_scene_image_url": effective_scene_image_url,
                "effective_clothing_image_url": effective_clothing_image_url,
                "effective_scene_ip_weight": effective_scene_ip_weight,
                "effective_outfit_ip_weight": effective_clothing_ip_weight,
                "director_decision_hints": director_decision_hints,
                "director_summary": {
                    "scene": {
                        "source": effective_scene_source,
                        "preset_id": effective_scene_preset_id,
                        "preset_title": effective_scene_preset_title,
                        "text_applied": bool(scene_text),
                        "upload_applied": bool(request.scene_image_url),
                        "ip_weight": effective_scene_ip_weight,
                    },
                    "outfit": {
                        "source": effective_outfit_source,
                        "preset_id": effective_outfit_preset_id,
                        "preset_title": effective_outfit_preset_title,
                        "text_applied": bool(outfit_text),
                        "upload_applied": bool(request.clothing_image_url),
                        "ip_weight": effective_clothing_ip_weight,
                    },
                    "global_style_text_applied": bool(global_style_text or legacy_prompt_override),
                    "text_segments": {
                        "global_style_text": global_style_text,
                        "scene_text": scene_text,
                        "outfit_text": outfit_text,
                    },
                },
                "couple_guardrails": {
                    "is_couple": is_couple_request,
                    "couple_flow": couple_flow,
                    "dual_face_input": is_couple_request,
                    "remote_join": bool(request.remote_join),
                    "distinct_subject_images": len({
                        _normalize_identity_image_url(image) for image in request.user_images if image
                    }) if is_couple_request else subject_count,
                },
                "pose_image_url": request.pose_image_url,
                "depth_image_url": request.depth_image_url,
                "normal_image_url": request.normal_image_url,
                "scene_ip_weight": effective_scene_ip_weight,
                "clothing_ip_weight": effective_clothing_ip_weight,
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
            },
            price_cents=0,
        )
        apply_order_retention(
            order,
            plan_code=retention_plan_code,
            has_paid_credits=has_paid_credits,
        )
        order.generation_params = {
            **(order.generation_params or {}),
            "retention": {
                "plan_code": retention_plan_code,
                "has_paid_credits": has_paid_credits,
                "source_images_expires_at": order.source_images_expires_at.isoformat()
                if order.source_images_expires_at
                else None,
                "expires_at": order.expires_at.isoformat() if order.expires_at else None,
            },
            "entitlement": {
                "access_tier": access_tier,
                "download_locked": not has_paid_credits,
                "trial_daily_limit": _trial_daily_generation_limit() if not has_paid_credits else None,
            },
        }
        db.add(order)
        await db.flush()

        # Commit before enqueue so worker can see the order immediately.
        await db.commit()
        await db.refresh(order)

        base_params = order.generation_params if isinstance(order.generation_params, dict) else {}
        if settings.using_inline_generation_execution:
            inline_task_id = f"inline-{order.id}"
            base_params["queue_job_id"] = inline_task_id
            base_params["execution_mode"] = "inline"
            order.generation_params = base_params
            order.task_id = inline_task_id
            order.status = OrderStatus.GENERATING
            await db.commit()
            generation_started = True
            await run_order_generation(str(order.id))
            await db.refresh(order)
        else:
            try:
                queue_job_id = await enqueue_generate_order(str(order.id))
                generation_started = True
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
                )

            base_params["queue_job_id"] = queue_job_id
            base_params["execution_mode"] = "arq"
            order.generation_params = base_params
            order.task_id = queue_job_id
            order.status = OrderStatus.GENERATING
            await db.commit()
    except Exception:
        if not generation_started:
            await add_credits_async(
                db,
                current_user.id,
                credits_cost,
                transaction_type=CreditTransactionType.GENERATION_REFUND,
                source="order",
                source_id=str(order.id) if "order" in locals() else None,
                description="Generation failed before queue start; credits refunded",
            )
            await db.commit()
        raise

    return await _serialize_order_for_user(db, order, current_user.id)


@router.delete("/{order_id}")
async def delete_my_order(
    order_id: str,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete an order and remove associated stored image assets."""
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")
    result = await db.execute(select(Order).where(Order.id == order_uuid))
    order = result.scalar_one_or_none()
    if not order or order.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.deleted_at is None:
        summary = delete_storage_urls(order_asset_urls(order))
        order.source_image_urls = None
        order.preview_image_urls = None
        order.final_image_urls = None
        order.deleted_at = datetime.now(timezone.utc)
        order.storage_cleanup_status = "deleted" if summary["failed"] == 0 else "cleanup_failed"
        await db.flush()
    return {"success": True, "order_id": str(order.id), "storage_cleanup_status": order.storage_cleanup_status}
