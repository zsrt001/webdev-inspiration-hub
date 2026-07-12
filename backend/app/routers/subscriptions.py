"""Subscription billing routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.feature_flags import Capability
from app.core.user_auth import get_request_user
from app.models.user import User
from app.schemas.subscription import (
    CurrentSubscriptionRead,
    SubscriptionCancelResponse,
    SubscriptionCheckoutRequest,
    SubscriptionCheckoutResponse,
    SubscriptionPlanRead,
)
from app.services.subscription_service import SubscriptionError, subscription_service
from app.services.feature_flag_service import require_request_capability

router = APIRouter()


def _raise_subscription_error(exc: SubscriptionError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.code, "message": exc.message},
    )


def _plan_to_read(plan) -> SubscriptionPlanRead:
    return SubscriptionPlanRead(
        code=plan.code,
        name=plan.name,
        billing_interval=plan.billing_interval,
        price_cents=int(plan.price_cents or 0),
        currency=plan.currency,
        monthly_credits=int(plan.monthly_credits or 0),
        feature_flags=plan.feature_flags or {},
    )


def _subscription_to_read(subscription) -> CurrentSubscriptionRead:
    if subscription is None:
        return CurrentSubscriptionRead(
            status="none",
            plan_code=None,
            current_period_start=None,
            current_period_end=None,
            cancel_at_period_end=False,
            monthly_credits=0,
        )
    plan = getattr(subscription, "plan", None)
    status = subscription.status.value if hasattr(subscription.status, "value") else str(subscription.status)
    return CurrentSubscriptionRead(
        status=status,
        plan_code=getattr(plan, "code", None),
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=bool(subscription.cancel_at_period_end),
        monthly_credits=int(getattr(plan, "monthly_credits", 0) or 0),
    )


@router.get("/plans", response_model=list[SubscriptionPlanRead])
async def list_subscription_plans(db: AsyncSession = Depends(get_db)):
    plans = await subscription_service.list_active_plans(db)
    return [_plan_to_read(plan) for plan in plans]


@router.get("/me", response_model=CurrentSubscriptionRead)
async def get_my_subscription(
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    subscription = await subscription_service.get_current_subscription(db, current_user.id)
    return _subscription_to_read(subscription)


@router.post("/checkout", response_model=SubscriptionCheckoutResponse)
async def create_subscription_checkout(
    request: SubscriptionCheckoutRequest,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    await require_request_capability(None, db, Capability.SUBSCRIPTION_BILLING)
    try:
        checkout = await subscription_service.create_checkout(
            db,
            user_id=current_user.id,
            plan_code=request.plan_code,
            return_url=request.return_url,
        )
    except SubscriptionError as exc:
        _raise_subscription_error(exc)
    return SubscriptionCheckoutResponse(**checkout)


@router.post("/cancel", response_model=SubscriptionCancelResponse)
async def cancel_my_subscription(
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    await require_request_capability(None, db, Capability.SUBSCRIPTION_BILLING)
    try:
        subscription = await subscription_service.cancel_current_subscription(db, current_user.id)
    except SubscriptionError as exc:
        _raise_subscription_error(exc)
    status = subscription.status.value if hasattr(subscription.status, "value") else str(subscription.status)
    return SubscriptionCancelResponse(
        status=status,
        cancel_at_period_end=bool(subscription.cancel_at_period_end),
        current_period_end=subscription.current_period_end,
    )
