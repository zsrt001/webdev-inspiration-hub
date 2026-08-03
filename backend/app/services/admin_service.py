"""Admin Service - Dashboard analytics and admin operations (DB-backed)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_portrait_job import LivePortraitJob, LivePortraitStatus
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.order import Order, OrderStatus
from app.models.payment_event import PaymentEvent
from app.models.subscription_credit_grant import SubscriptionCreditGrant
from app.models.subscription_plan import SubscriptionPlan
from app.models.user_subscription import SubscriptionStatus, UserSubscription
from app.models.user import User
from app.models.user_credit import UserCredit
from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.services.credit_reversal_service import reverse_root_grant
from app.services.idempotency_service import (
    IdempotencyConflict,
    begin_idempotent_request,
    canonical_request_hash,
    complete_idempotent_request,
)
from app.services.template_service import get_template_by_id


def _pick_first_image_url(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for value in payload.values():
        if isinstance(value, str) and value:
            return value
    return ""


async def _resolve_user(db: AsyncSession, user_id: str) -> User:
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required")

    try:
        user_uuid = uuid.UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("user_id must be a UUID") from exc
    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None:
        raise LookupError("user not found")

    return user


async def get_dashboard_stats(db: AsyncSession) -> dict:
    """
    Get dashboard statistics for admin view.

    Returns:
        dict with total_orders, revenue_credits, active_users, recent_activity
    """
    total_orders = int(await db.scalar(select(func.count(Order.id))) or 0)
    total_users = int(await db.scalar(select(func.count(User.id))) or 0)
    total_credits_in_circulation = int(
        await db.scalar(select(func.coalesce(func.sum(CreditTransaction.amount), 0)))
        or 0
    )

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

    generation_debits = -int(
        await db.scalar(
            select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
                CreditTransaction.transaction_type
                == CreditTransactionType.GENERATION_DEBIT.value
            )
        )
        or 0
    )
    generation_refunds = int(
        await db.scalar(
            select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
                CreditTransaction.transaction_type
                == CreditTransactionType.GENERATION_REFUND.value
            )
        )
        or 0
    )
    total_revenue_credits = max(0, generation_debits - generation_refunds)

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
            select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
                CreditTransaction.transaction_type
                == CreditTransactionType.SUBSCRIPTION_GRANT.value,
                CreditTransaction.created_at >= month_start,
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
        # Monetary revenue is unavailable until normalized payment facts land;
        # credits are not converted with a fabricated exchange rate.
        "estimated_revenue_usd": 0.0,
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


async def adjust_user_credits_by_admin(
    db: AsyncSession,
    *,
    admin_user_id: uuid.UUID,
    user_id: str,
    amount: int,
    idempotency_key: str,
    approval_id: str,
    reason: str,
    positive_grant_policy: dict | None,
    reversal_roots: list[dict],
) -> dict:
    """Apply an audited root grant or bounded named-root compensation."""

    clean_approval = str(approval_id or "").strip()
    clean_reason = str(reason or "").strip()
    clean_key = str(idempotency_key or "").strip()
    if not clean_approval or len(clean_approval) > 128:
        raise ValueError("approval_id_invalid")
    if len(clean_reason) < 8 or len(clean_reason) > 500:
        raise ValueError("approval_reason_invalid")
    if not clean_key or len(clean_key) > 80:
        raise ValueError("idempotency_key_invalid")
    normalized_amount = int(amount)
    if normalized_amount == 0 or abs(normalized_amount) > 10000:
        raise ValueError("adjustment_amount_invalid")
    target = await _resolve_user(db, user_id)
    payload = {
        "admin_user_id": str(admin_user_id),
        "target_user_id": str(target.id),
        "amount": normalized_amount,
        "approval_id": clean_approval,
        "reason": clean_reason,
        "positive_grant_policy": positive_grant_policy,
        "reversal_roots": reversal_roots,
    }
    attempt = await begin_idempotent_request(
        db,
        user_id=admin_user_id,
        endpoint="admin.credit.adjust",
        key=clean_key,
        request_hash=canonical_request_hash(payload),
    )
    if attempt.replayed:
        if attempt.state != "COMPLETED" or attempt.response_json is None:
            raise IdempotencyConflict("admin_credit_adjustment_in_progress")
        return dict(attempt.response_json)

    if normalized_amount > 0:
        if positive_grant_policy is None or reversal_roots:
            raise ValueError("positive_grant_policy_required")
        policy_code = str(positive_grant_policy.get("policy_code") or "").strip()
        source_reference = str(
            positive_grant_policy.get("source_reference") or ""
        ).strip()
        retention_days = int(positive_grant_policy.get("retention_days") or 0)
        if (
            policy_code not in {"support_compensation", "service_recovery"}
            or not source_reference
            or len(source_reference) > 128
            or retention_days != 90
        ):
            raise ValueError("positive_grant_policy_invalid")
        credit = await db.scalar(
            select(UserCredit)
            .where(UserCredit.user_id == target.id)
            .with_for_update()
        )
        if credit is None:
            credit = UserCredit(
                id=uuid.uuid4(),
                user_id=target.id,
                balance=0,
                reserved_balance=0,
            )
            db.add(credit)
            await db.flush()
        prior_balance = int(credit.balance or 0)
        next_balance = prior_balance + normalized_amount
        transaction_id = uuid.uuid4()
        transaction = CreditTransaction(
            id=transaction_id,
            user_id=target.id,
            transaction_type=CreditTransactionType.ADMIN_GRANT,
            amount=normalized_amount,
            balance_after=next_balance,
            source="database_admin",
            source_id=clean_approval,
            description=clean_reason,
            root_transaction_id=transaction_id,
            request_id=f"admin:{clean_key}",
            metadata_json={
                "admin_user_id": str(admin_user_id),
                "approval_id": clean_approval,
                "policy_code": policy_code,
                "source_reference": source_reference,
            },
        )
        lot = CreditGrantLot(
            id=uuid.uuid4(),
            user_id=target.id,
            root_transaction_id=transaction_id,
            source_type=GrantLotSourceType.ADMIN,
            source_id=source_reference,
            original_amount=normalized_amount,
            debt_offset_amount=min(normalized_amount, max(0, -prior_balance)),
            reversed_amount=0,
            frozen_amount=0,
            consumed_amount=0,
            retention_tier="paid_90d",
            expires_at=datetime.now(timezone.utc) + timedelta(days=retention_days),
        )
        db.add(transaction)
        await db.flush()
        db.add(lot)
        credit.balance = next_balance
        await db.flush()
        new_balance = next_balance
    else:
        if positive_grant_policy is not None or not reversal_roots:
            raise ValueError("reversal_roots_required")
        reversal_total = sum(int(item.get("amount") or 0) for item in reversal_roots)
        if reversal_total != abs(normalized_amount):
            raise ValueError("reversal_root_amount_mismatch")
        seen_roots: set[uuid.UUID] = set()
        for index, root in enumerate(reversal_roots):
            root_id = uuid.UUID(str(root.get("root_transaction_id") or ""))
            root_amount = int(root.get("amount") or 0)
            if root_id in seen_roots or root_amount <= 0:
                raise ValueError("reversal_root_invalid")
            seen_roots.add(root_id)
            await reverse_root_grant(
                db,
                user_id=target.id,
                root_transaction_id=root_id,
                amount=root_amount,
                request_id=f"admin:{clean_key}:{index}",
                reason_code=clean_reason,
            )
        credit = await db.scalar(
            select(UserCredit)
            .where(UserCredit.user_id == target.id)
            .with_for_update()
        )
        if credit is None:
            raise ValueError("credit_account_missing")
        new_balance = int(credit.balance or 0)

    response = {
        "success": True,
        "user_id": str(target.id),
        "credits_granted": normalized_amount,
        "new_balance": new_balance,
    }
    await complete_idempotent_request(
        db,
        record_id=attempt.record_id,
        response_status=200,
        response_json=response,
    )
    return response


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
            "user_id": str(credit.user_id),
            "balance": int(credit.balance or 0),
        }
        for credit, user in rows
    ]
