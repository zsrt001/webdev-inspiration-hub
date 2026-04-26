"""Subscription billing business operations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_transaction import CreditTransactionType
from app.models.subscription_credit_grant import SubscriptionCreditGrant
from app.models.user_subscription import UserSubscription
from app.services.credit_service import add_credits_with_transaction_async


class SubscriptionService:
    """Coordinates subscription period credit grants against the credit ledger."""

    async def grant_period_credits(
        self,
        db: AsyncSession,
        subscription: UserSubscription,
        *,
        period_key: str,
        credits: int | None = None,
    ) -> SubscriptionCreditGrant:
        normalized_period_key = str(period_key or "").strip()
        if not normalized_period_key:
            raise ValueError("period_key is required")

        result = await db.execute(
            select(SubscriptionCreditGrant).where(
                SubscriptionCreditGrant.subscription_id == subscription.id,
                SubscriptionCreditGrant.period_key == normalized_period_key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        credit_amount = int(credits if credits is not None else getattr(subscription.plan, "monthly_credits", 0) or 0)
        if credit_amount <= 0:
            raise ValueError("credits must be greater than zero")

        _balance, credit_transaction = await add_credits_with_transaction_async(
            db,
            subscription.user_id,
            credit_amount,
            transaction_type=CreditTransactionType.SUBSCRIPTION_GRANT,
            source="subscription",
            source_id=str(subscription.id),
            description=f"Subscription credit grant: {normalized_period_key}",
            metadata={
                "provider": subscription.provider,
                "provider_subscription_id": subscription.provider_subscription_id,
                "period_key": normalized_period_key,
            },
        )
        credit_transaction_id = getattr(credit_transaction, "id", None)
        if credit_transaction_id is None:
            raise RuntimeError("subscription credit transaction was not recorded")

        grant = SubscriptionCreditGrant(
            subscription_id=subscription.id,
            user_id=subscription.user_id,
            period_key=normalized_period_key,
            credits=credit_amount,
            credit_transaction_id=credit_transaction_id,
        )
        db.add(grant)
        await db.flush()
        return grant


subscription_service = SubscriptionService()
