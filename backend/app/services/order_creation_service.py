"""Admission boundary for durable generation orders.

The only accepted identity inputs are owned private MediaAsset IDs. Gatekeeper
I/O completes before the atomic PostgreSQL order/reservation/job/outbox write;
no queue, Provider-generation, Redis, or background-task call exists here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.models.idempotency_record import IdempotencyRecord, IdempotencyState
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.models.welcome_grant_claim import WelcomeGrantClaim
from app.schemas.order import AcceptedOrder, OrderCreate
from app.services import gatekeeper_service
from app.services.billing_catalog_service import (
    BillingCatalogUnavailable,
    load_active_catalog,
)
from app.services.content_policy_service import evaluate_prompt_text
from app.services.credit_reservation_service import (
    FundingPolicyViolation,
    InsufficientCredits,
    OrderFundingPolicySnapshot,
)
from app.services.credit_service import get_generation_cost
from app.services.idempotency_service import IdempotencyConflict, canonical_request_hash
from app.services.media_asset_service import AssetAccessError
from app.services.order_transaction_service import (
    CreateOrderCommand,
    OrderPolicySnapshot,
    OrderTransactionError,
    create_order_transaction,
    require_server_runtime_execution_stamp,
)
from app.services.template_service import get_template_by_id, template_is_commercial


settings = get_settings()
GATEKEEPER_POLICY_VERSION = "gatekeeper.v1"


@dataclass(frozen=True, slots=True)
class OrderAdmissionFacts:
    catalog_version_id: uuid.UUID
    catalog_version: str
    catalog_release_sha: str
    welcome_claim_id: uuid.UUID | None
    welcome_spendable_credits: int
    trial_attempts_in_rolling_24h: int
    ready_trial_exists: bool


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _validate_idempotency_key(value: str) -> str:
    key = str(value or "").strip()
    if not key or len(key) > 128:
        raise _error(400, "idempotency_key_invalid", "A valid Idempotency-Key is required.")
    return key


def canonical_order_request_hash(request: OrderCreate) -> str:
    return canonical_request_hash(request.model_dump(mode="json", exclude_none=False))


def _validate_request(request: OrderCreate) -> tuple[object, str, str, int, bool]:
    if request.legal_accepted is not True:
        raise _error(422, "legal_acceptance_required", "The current legal terms must be accepted.")
    if len(set(request.asset_ids)) != len(request.asset_ids):
        raise _error(422, "duplicate_source_asset", "Each source asset must be distinct.")

    template = get_template_by_id(request.template_id)
    if template is None or not template_is_commercial(template):
        raise _error(422, "template_not_available", "The selected template is not commercially available.")
    subject_count = len(request.asset_ids)
    expected_category = "single" if subject_count == 1 else "couple"
    if str(template.category).strip().lower() != expected_category:
        raise _error(
            422,
            "template_subject_count_mismatch",
            "The selected template does not match the number of subjects.",
        )

    raw_reference_fields = (
        "scene_image_url",
        "clothing_image_url",
        "pose_image_url",
        "depth_image_url",
        "normal_image_url",
    )
    if any(getattr(request, field) for field in raw_reference_fields):
        raise _error(
            422,
            "legacy_reference_url_forbidden",
            "Generation references must be uploaded as owned private assets.",
        )
    advanced_control_fields = (
        "scene_ip_weight",
        "clothing_ip_weight",
        "face_ip_weight",
        "pose_cn_weight",
        "depth_cn_weight",
        "normal_cn_weight",
        "pose_cn_start",
        "pose_cn_end",
        "depth_cn_start",
        "depth_cn_end",
        "normal_cn_start",
        "normal_cn_end",
    )
    if any(getattr(request, field) is not None for field in advanced_control_fields):
        raise _error(
            422,
            "legacy_generation_control_forbidden",
            "Legacy client-side generation controls are no longer accepted.",
        )
    if request.upload_quality is not None:
        raise _error(
            422,
            "client_quality_facts_forbidden",
            "Image quality facts are computed by the server.",
        )

    prompt_fields = (
        request.global_style_text,
        request.scene_text,
        request.outfit_text,
        request.prompt_override,
    )
    for value in prompt_fields:
        if value and len(value) > 1000:
            raise _error(422, "prompt_too_long", "Prompt text exceeds the supported length.")
        verdict = evaluate_prompt_text(value)
        if not verdict.passed:
            raise _error(
                422,
                str(verdict.reason or "prompt_policy_rejected"),
                "Prompt text violates the generation content policy.",
            )

    director_mode = bool(request.director_mode)
    generation_mode = expected_category
    scene_tier = "premium" if director_mode else "base"
    credit_cost = get_generation_cost(
        str(template.category),
        image_count=subject_count,
        director_mode=director_mode,
    )
    return template, generation_mode, scene_tier, credit_cost, director_mode


def build_create_order_command(
    *,
    request: OrderCreate,
    user_id: uuid.UUID,
    idempotency_key: str,
    facts: OrderAdmissionFacts,
) -> CreateOrderCommand:
    key = _validate_idempotency_key(idempotency_key)
    _template, generation_mode, scene_tier, credit_cost, director_mode = _validate_request(request)
    subject_count = len(request.asset_ids)
    trial_eligible = (
        generation_mode == "single"
        and scene_tier == "base"
        and not director_mode
        and credit_cost == 2
        and facts.welcome_claim_id is not None
        and int(facts.welcome_spendable_credits) >= credit_cost
        and int(facts.trial_attempts_in_rolling_24h) < 3
        and facts.ready_trial_exists is False
    )
    policy_suffix = "director" if director_mode else "base"
    product_policy = OrderPolicySnapshot(
        template_id=request.template_id,
        product_code=f"generation_{generation_mode}_{policy_suffix}",
        catalog_version_id=facts.catalog_version_id,
        catalog_version=facts.catalog_version,
        catalog_release_sha=facts.catalog_release_sha,
        generation_mode=generation_mode,
        scene_tier=scene_tier,
        subject_count=subject_count,
        director_mode=director_mode,
        credit_cost=credit_cost,
        gatekeeper_policy_version=GATEKEEPER_POLICY_VERSION,
        gatekeeper_passed=True,
        global_style_text=request.global_style_text,
        scene_text=request.scene_text,
        outfit_text=request.outfit_text,
        scene_preset_id=request.scene_preset_id,
        clothing_preset_id=request.clothing_preset_id,
        prompt_override=request.prompt_override,
    )
    funding_policy = OrderFundingPolicySnapshot(
        generation_mode=generation_mode,
        subject_count=subject_count,
        is_trial=trial_eligible,
        identity_claim_id=facts.welcome_claim_id if trial_eligible else None,
        attempts_in_rolling_24h=int(facts.trial_attempts_in_rolling_24h),
        ready_trial_exists=bool(facts.ready_trial_exists),
        allowed_lot_class="WELCOME_ONLY" if trial_eligible else "PAID_ONLY",
        scene_tier=scene_tier,
        director_mode=director_mode,
    )
    return CreateOrderCommand(
        user_id=user_id,
        idempotency_key=key,
        request_hash=canonical_order_request_hash(request),
        asset_ids=tuple(request.asset_ids),
        product_policy=product_policy,
        funding_policy=funding_policy,
        credit_cost=credit_cost,
    )


async def _completed_replay(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
    request_hash: str,
) -> AcceptedOrder | None:
    record = await db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id,
            IdempotencyRecord.endpoint == "orders.create",
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
    )
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise _error(409, "idempotency_payload_mismatch", "This idempotency key was used for another request.")
    state = record.state.value if hasattr(record.state, "value") else str(record.state)
    if (
        state == IdempotencyState.COMPLETED.value
        and record.response_status == 202
        and record.response_json is not None
    ):
        return AcceptedOrder.model_validate(record.response_json, strict=False)
    return None


async def _load_admission_facts(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime,
) -> OrderAdmissionFacts:
    catalog = await load_active_catalog(db, environment=settings.runtime_environment, now=now)
    claim = await db.scalar(
        select(WelcomeGrantClaim).where(WelcomeGrantClaim.user_id == user_id)
    )
    welcome_lots = list(
        (
            await db.scalars(
                select(CreditGrantLot).where(
                    CreditGrantLot.user_id == user_id,
                    CreditGrantLot.source_type == GrantLotSourceType.WELCOME.value,
                    or_(CreditGrantLot.expires_at.is_(None), CreditGrantLot.expires_at > now),
                )
            )
        ).all()
    )
    welcome_spendable = sum(int(lot.spendable_amount) for lot in welcome_lots)
    trial_expression = Order.funding_policy_snapshot["is_trial"].as_boolean().is_(True)
    recent_cutoff = now - timedelta(hours=24)
    recent_attempts = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                Order.user_id == user_id,
                Order.created_at >= recent_cutoff,
                trial_expression,
            )
        )
        or 0
    )
    ready_trial_exists = bool(
        await db.scalar(
            select(func.count(Order.id)).where(
                Order.user_id == user_id,
                Order.status == OrderStatus.READY.value,
                trial_expression,
            )
        )
        or 0
    )
    return OrderAdmissionFacts(
        catalog_version_id=uuid.UUID(catalog.catalog_id),
        catalog_version=catalog.version,
        catalog_release_sha=catalog.release_sha,
        welcome_claim_id=claim.id if claim is not None else None,
        welcome_spendable_credits=welcome_spendable,
        trial_attempts_in_rolling_24h=recent_attempts,
        ready_trial_exists=ready_trial_exists,
    )


async def _run_gatekeeper_checks(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    asset_ids: tuple[uuid.UUID, ...],
) -> None:
    for asset_id in asset_ids:
        try:
            result = await gatekeeper_service.check_image_quality(
                db,
                owner_user_id=user_id,
                asset_id=asset_id,
            )
        except AssetAccessError as exc:
            status = 404 if exc.code == "asset_not_found" else 409
            raise _error(status, exc.code, "The source asset is not available for this order.") from exc
        if result.passed is not True:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "image_gate_rejected",
                    "message": "One or more source images failed safety or quality checks.",
                    "asset_id": str(asset_id),
                    "reasons": list(result.reasons)[:12],
                    "advice": list(result.advice)[:12],
                    "risk_flags": list(result.risk_flags)[:12],
                },
            )


async def create_order_for_user(
    request: OrderCreate,
    current_user: User,
    db: AsyncSession,
    *,
    idempotency_key: str,
) -> AcceptedOrder:
    key = _validate_idempotency_key(idempotency_key)
    request_hash = canonical_order_request_hash(request)
    replay = await _completed_replay(
        db,
        user_id=current_user.id,
        idempotency_key=key,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay

    try:
        require_server_runtime_execution_stamp()
        now = datetime.now(timezone.utc)
        facts = await _load_admission_facts(db, user_id=current_user.id, now=now)
        command = build_create_order_command(
            request=request,
            user_id=current_user.id,
            idempotency_key=key,
            facts=facts,
        )
        await _run_gatekeeper_checks(
            db,
            user_id=current_user.id,
            asset_ids=command.asset_ids,
        )
        accepted = await create_order_transaction(db, command, now=now)
        await db.commit()
        return accepted
    except HTTPException:
        await db.rollback()
        raise
    except InsufficientCredits as exc:
        await db.rollback()
        raise HTTPException(
            status_code=402,
            detail={
                "code": "insufficient_credits",
                "message": "The account does not have enough eligible credits.",
                "required": exc.required,
            },
        ) from exc
    except BillingCatalogUnavailable as exc:
        await db.rollback()
        raise _error(503, exc.code, "The active commercial catalog is unavailable.") from exc
    except (IdempotencyConflict, FundingPolicyViolation) as exc:
        await db.rollback()
        code = getattr(exc, "code", "order_policy_conflict")
        raise _error(409, code, "The order request conflicts with an existing commercial fact.") from exc
    except OrderTransactionError as exc:
        await db.rollback()
        raise _error(exc.status_code, exc.code, "The order could not enter the durable generation queue.") from exc
    except Exception:
        await db.rollback()
        raise
