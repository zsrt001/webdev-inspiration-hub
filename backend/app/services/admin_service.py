"""Admin Service - Dashboard analytics and admin operations (DB-backed)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_portrait_job import LivePortraitJob, LivePortraitStatus
from app.models.order import Order, OrderStatus
from app.models.payment_event import PaymentEvent
from app.models.subscription_credit_grant import SubscriptionCreditGrant
from app.models.subscription_plan import SubscriptionPlan
from app.models.user_subscription import SubscriptionStatus, UserSubscription
from app.models.user import User
from app.models.user_credit import UserCredit
from app.services.credit_service import add_credits_async
from app.services.template_service import get_template_by_id


def _pick_first_image_url(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for value in payload.values():
        if isinstance(value, str) and value:
            return value
    return ""


async def _resolve_or_create_user(db: AsyncSession, user_id: str) -> User:
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required")

    user: User | None = None
    try:
        user_uuid = uuid.UUID(user_id)
        result = await db.execute(select(User).where(User.id == user_uuid))
        user = result.scalar_one_or_none()
    except Exception:
        user = None

    if user is None:
        result = await db.execute(select(User).where(User.openid == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        user = User(openid=user_id)
        db.add(user)
        await db.flush()

    return user


async def get_dashboard_stats(db: AsyncSession) -> dict:
    """
    Get dashboard statistics for admin view.

    Returns:
        dict with total_orders, revenue_credits, active_users, recent_activity
    """
    total_orders = int(await db.scalar(select(func.count(Order.id))) or 0)
    total_users = int(await db.scalar(select(func.count(User.id))) or 0)
    total_credits_in_circulation = int(await db.scalar(select(func.coalesce(func.sum(UserCredit.balance), 0))) or 0)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    active_order_users = (
        await db.execute(select(Order.user_id).where(Order.created_at >= cutoff).distinct())
    ).scalars().all()
    active_live_users = (
        await db.execute(select(LivePortraitJob.user_id).where(LivePortraitJob.created_at >= cutoff).distinct())
    ).scalars().all()
    active_users_24h = len({*active_order_users, *active_live_users})

    tpl_rows = await db.execute(
        select(Order.template_id, func.count(Order.id))
        .group_by(Order.template_id)
        .order_by(func.count(Order.id).desc())
    )
    template_breakdown = {
        str(template_id or "unknown"): int(count or 0)
        for template_id, count in tpl_rows.all()
    }

    completed_orders = (
        await db.execute(
            select(Order.generation_params).where(Order.status == OrderStatus.COMPLETED)
        )
    ).scalars().all()
    order_revenue_credits = 0
    for params in completed_orders:
        if isinstance(params, dict):
            try:
                order_revenue_credits += int(params.get("credits_cost") or 0)
            except Exception:
                pass

    live_revenue_credits = int(
        await db.scalar(
            select(func.coalesce(func.sum(LivePortraitJob.credits_cost), 0)).where(
                LivePortraitJob.status == LivePortraitStatus.COMPLETED
            )
        )
        or 0
    )
    total_revenue_credits = int(order_revenue_credits + live_revenue_credits)

    recent_orders = (
        await db.execute(select(Order).order_by(Order.created_at.desc()).limit(50))
    ).scalars().all()
    recent_activity = []
    for order in recent_orders:
        image_url = _pick_first_image_url(order.final_image_urls) or _pick_first_image_url(order.preview_image_urls)
        template_title = None
        if order.template_id:
            template = get_template_by_id(order.template_id)
            template_title = template.title if template else None
        recent_activity.append(
            {
                "id": str(order.id),
                "image_url": image_url,
                "template_id": order.template_id,
                "template_title": template_title,
                "created_at": order.created_at.isoformat() if order.created_at else "",
                "status": order.status.value if hasattr(order.status, "value") else str(order.status),
            }
        )

    active_subscriptions = int(
        await db.scalar(
            select(func.count(UserSubscription.id)).where(
                UserSubscription.status.in_([SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value])
            )
        )
        or 0
    )
    past_due_subscriptions = int(
        await db.scalar(
            select(func.count(UserSubscription.id)).where(UserSubscription.status == SubscriptionStatus.PAST_DUE.value)
        )
        or 0
    )
    canceled_this_month = int(
        await db.scalar(
            select(func.count(UserSubscription.id)).where(
                UserSubscription.status == SubscriptionStatus.CANCELED.value,
                UserSubscription.updated_at >= month_start,
            )
        )
        or 0
    )
    subscription_mrr_cents = int(
        await db.scalar(
            select(func.coalesce(func.sum(SubscriptionPlan.price_cents), 0))
            .select_from(UserSubscription)
            .join(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
            .where(UserSubscription.status.in_([SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value]))
        )
        or 0
    )
    credits_granted_this_month = int(
        await db.scalar(
            select(func.coalesce(func.sum(SubscriptionCreditGrant.credits), 0)).where(
                SubscriptionCreditGrant.created_at >= month_start
            )
        )
        or 0
    )
    failed_events = (
        await db.execute(
            select(PaymentEvent)
            .where(PaymentEvent.error.is_not(None))
            .order_by(PaymentEvent.updated_at.desc())
            .limit(20)
        )
    ).scalars().all()
    recent_failed_payment_events = [
        {
            "id": str(event.id),
            "provider": event.provider,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "object_id": event.object_id,
            "error": event.error,
            "created_at": event.created_at.isoformat() if event.created_at else "",
            "updated_at": event.updated_at.isoformat() if event.updated_at else "",
        }
        for event in failed_events
    ]

    return {
        "total_orders": total_orders,
        "total_revenue_credits": total_revenue_credits,
        "estimated_revenue_usd": round(total_revenue_credits * 0.10, 2),
        "total_users": total_users,
        "active_users_24h": active_users_24h,
        "total_credits_in_circulation": total_credits_in_circulation,
        "template_breakdown": template_breakdown,
        "recent_activity": recent_activity,
        "active_subscriptions": active_subscriptions,
        "past_due_subscriptions": past_due_subscriptions,
        "canceled_this_month": canceled_this_month,
        "subscription_mrr_cents": subscription_mrr_cents,
        "credits_granted_this_month": credits_granted_this_month,
        "recent_failed_payment_events": recent_failed_payment_events,
    }


async def grant_credits_to_user(db: AsyncSession, user_id: str, amount: int) -> dict:
    """
    Grant credits to a specific user (admin operation).

    Args:
        user_id: Target user ID (openid or UUID)
        amount: Number of credits to grant
    """
    target = await _resolve_or_create_user(db, user_id)
    new_balance = await add_credits_async(db, target.id, amount)

    return {
        "success": True,
        "user_id": target.openid or str(target.id),
        "credits_granted": amount,
        "new_balance": new_balance,
    }


async def get_all_users(db: AsyncSession, *, limit: int = 500) -> list[dict]:
    """Get users with credit balances, sorted by balance desc."""
    limit = max(1, min(2000, int(limit)))
    rows = (
        await db.execute(
            select(UserCredit, User)
            .join(User, User.id == UserCredit.user_id)
            .order_by(UserCredit.balance.desc(), UserCredit.updated_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "user_id": user.openid or str(credit.user_id),
            "balance": int(credit.balance or 0),
        }
        for credit, user in rows
    ]
