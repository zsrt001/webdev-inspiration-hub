"""Subscription routes backed by normalized catalog and Provider facts."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.feature_flags import Capability
from app.core.session_auth import get_session_user
from app.models.user import User
from app.schemas.subscription import (
    CurrentSubscriptionRead,
    SubscriptionCancelResponse,
    SubscriptionCheckoutRequest,
    SubscriptionCheckoutResponse,
    SubscriptionPlanRead,
)
from app.services.feature_flag_service import require_request_capability
from app.services.subscription_service import SubscriptionError, subscription_service


router = APIRouter()


def _raise_subscription_error(exc: SubscriptionError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.status_code >= 500,
        },
    )


def _plan_to_read(plan) -> SubscriptionPlanRead:
    return SubscriptionPlanRead(
        code=plan.product_code,
        pre_tax_minor_units=int(plan.pre_tax_minor_units),
        currency=plan.currency,
        credits=int(plan.credits),
        retention_tier=plan.retention_tier,
        display_price=f"${int(plan.pre_tax_minor_units) / 100:.2f}",
    )


def _subscription_to_read(subscription) -> CurrentSubscriptionRead:
    if subscription is None:
        return CurrentSubscriptionRead(
            subscription_id=None,
            status="NONE",
            product_code=None,
            current_period_start=None,
            current_period_end=None,
            paid_through_at=None,
            cancel_at_period_end=False,
            credits_per_paid_period=0,
        )
    snapshot = dict(subscription.catalog_snapshot or {})
    status = (
        subscription.normalized_status.value
        if hasattr(subscription.normalized_status, "value")
        else str(subscription.normalized_status)
    )
    return CurrentSubscriptionRead(
        subscription_id=subscription.id,
        status=status,
        product_code=subscription.product_code,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        paid_through_at=subscription.paid_through_at,
        cancel_at_period_end=bool(subscription.cancel_at_period_end),
        credits_per_paid_period=int(snapshot.get("credits") or 0),
    )


@router.get("/plans", response_model=list[SubscriptionPlanRead])
async def list_subscription_plans(
    db: AsyncSession = Depends(get_db),
) -> list[SubscriptionPlanRead]:
    try:
        plans = await subscription_service.list_active_plans(db)
    except SubscriptionError as exc:
        _raise_subscription_error(exc)
    return [_plan_to_read(plan) for plan in plans]


@router.get("/me", response_model=CurrentSubscriptionRead)
async def get_my_subscription(
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentSubscriptionRead:
    try:
        subscription = await subscription_service.get_current_subscription(db, current_user.id)
    except SubscriptionError as exc:
        _raise_subscription_error(exc)
    return _subscription_to_read(subscription)


@router.post("/checkout", response_model=SubscriptionCheckoutResponse)
async def create_subscription_checkout(
    payload: SubscriptionCheckoutRequest,
    http_request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionCheckoutResponse:
    await require_request_capability(
        http_request,
        db,
        Capability.SUBSCRIPTION_BILLING,
        verified_user_id=current_user.id,
    )
    try:
        checkout = await subscription_service.create_checkout(
            db,
            user_id=current_user.id,
            plan_code=payload.plan_code,
            return_url=payload.return_url,
            idempotency_key=idempotency_key,
        )
    except SubscriptionError as exc:
        _raise_subscription_error(exc)
    return SubscriptionCheckoutResponse.model_validate(checkout)


@router.post("/cancel", response_model=SubscriptionCancelResponse)
async def cancel_my_subscription(
    http_request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionCancelResponse:
    await require_request_capability(
        http_request,
        db,
        Capability.SUBSCRIPTION_BILLING,
        verified_user_id=current_user.id,
    )
    try:
        response = await subscription_service.cancel_current_subscription(
            db,
            current_user.id,
            idempotency_key=idempotency_key,
        )
    except SubscriptionError as exc:
        _raise_subscription_error(exc)
    return SubscriptionCancelResponse.model_validate(response)
