"""Subscription billing business operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.credit_transaction import CreditTransactionType
from app.models.subscription_credit_grant import SubscriptionCreditGrant
from app.models.subscription_plan import SubscriptionPlan
from app.models.user_subscription import SubscriptionStatus
from app.models.user_subscription import UserSubscription
from app.services.credit_service import add_credits_with_transaction_async

settings = get_settings()

DEFAULT_PLAN_SEEDS = (
    {
        "id": uuid.UUID("30aa1d5f-bbbd-4b1d-9f5b-2608ad6f0001"),
        "code": "starter_monthly",
        "name": "Starter Monthly",
        "billing_interval": "month",
        "price_cents": 1900,
        "currency": "USD",
        "monthly_credits": 80,
        "feature_flags": {"tier": "starter"},
        "is_active": True,
    },
    {
        "id": uuid.UUID("30aa1d5f-bbbd-4b1d-9f5b-2608ad6f0002"),
        "code": "creator_monthly",
        "name": "Creator Monthly",
        "billing_interval": "month",
        "price_cents": 4900,
        "currency": "USD",
        "monthly_credits": 260,
        "feature_flags": {"tier": "creator"},
        "is_active": True,
    },
    {
        "id": uuid.UUID("30aa1d5f-bbbd-4b1d-9f5b-2608ad6f0003"),
        "code": "studio_monthly",
        "name": "Studio Monthly",
        "billing_interval": "month",
        "price_cents": 12900,
        "currency": "USD",
        "monthly_credits": 900,
        "feature_flags": {"tier": "studio"},
        "is_active": True,
    },
)


class SubscriptionError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class SubscriptionService:
    """Coordinates subscription period credit grants against the credit ledger."""

    _ACTIVE_EVENTS = {
        "subscription.active",
        "subscription.created",
        "subscription.update",
        "subscription.updated",
        "subscription.paid",
        "invoice.paid",
        "subscription.payment_succeeded",
    }

    def _status_value(self, status: SubscriptionStatus | str | None) -> str:
        value = status.value if hasattr(status, "value") else status
        normalized = str(value or "").strip().lower()
        aliases = {
            "active": SubscriptionStatus.ACTIVE.value,
            "trial": SubscriptionStatus.TRIALING.value,
            "trialing": SubscriptionStatus.TRIALING.value,
            "past_due": SubscriptionStatus.PAST_DUE.value,
            "past-due": SubscriptionStatus.PAST_DUE.value,
            "canceled": SubscriptionStatus.CANCELED.value,
            "cancelled": SubscriptionStatus.CANCELED.value,
            "expired": SubscriptionStatus.EXPIRED.value,
            "ended": SubscriptionStatus.EXPIRED.value,
        }
        return aliases.get(normalized, normalized)

    def _parse_datetime(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _period_key(self, payload: dict[str, Any]) -> str:
        explicit = str(payload.get("period_key") or "").strip()
        if explicit:
            return explicit
        start = self._parse_datetime(
            payload.get("current_period_start")
            or payload.get("current_period_start_date")
            or payload.get("period_start")
        )
        if start is None:
            start = datetime.now(timezone.utc)
        return start.strftime("%Y-%m")

    def _metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        for key in ("metadata", "custom_data"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return {}

    def _provider_subscription_id(self, payload: dict[str, Any]) -> str:
        for key in ("subscription_id", "provider_subscription_id", "subscriptionId", "id"):
            value = payload.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        subscription = payload.get("subscription")
        if isinstance(subscription, dict):
            return self._provider_subscription_id(subscription)
        if subscription is not None:
            return str(subscription).strip()
        return ""

    def _provider_customer_id(self, payload: dict[str, Any]) -> str | None:
        for key in ("customer_id", "provider_customer_id", "customer"):
            value = payload.get(key)
            if value is not None:
                if isinstance(value, dict):
                    value = value.get("id") or value.get("customer_id")
                text = str(value).strip()
                if text:
                    return text
        return None

    def _product_id(self, payload: dict[str, Any]) -> str:
        for key in ("product_id", "product"):
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, dict):
                value = value.get("id") or value.get("product_id")
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _plan_code_for_product_id(self, product_id: str) -> str:
        normalized = str(product_id or "").strip()
        if not normalized:
            return ""
        mapping = {
            str(settings.creem_subscription_starter_product_id or "").strip(): "starter_monthly",
            str(settings.creem_subscription_creator_product_id or "").strip(): "creator_monthly",
            str(settings.creem_subscription_studio_product_id or "").strip(): "studio_monthly",
        }
        return mapping.get(normalized, "")

    async def _find_plan(self, db: AsyncSession, payload: dict[str, Any]) -> SubscriptionPlan | None:
        metadata = self._metadata(payload)
        plan_code = str(
            metadata.get("plan_code")
            or payload.get("plan_code")
            or self._plan_code_for_product_id(self._product_id(payload))
            or ""
        ).strip()
        if not plan_code:
            return None
        result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == plan_code))
        return result.scalar_one_or_none()

    async def _get_or_create_subscription(
        self,
        db: AsyncSession,
        *,
        provider: str,
        payload: dict[str, Any],
    ) -> UserSubscription:
        provider_subscription_id = self._provider_subscription_id(payload)
        if not provider_subscription_id:
            raise ValueError("provider_subscription_id is required")

        result = await db.execute(
            select(UserSubscription)
            .where(
                UserSubscription.provider == provider,
                UserSubscription.provider_subscription_id == provider_subscription_id,
            )
            .with_for_update()
        )
        subscription = result.scalar_one_or_none()
        if subscription is not None:
            return subscription

        metadata = self._metadata(payload)
        raw_user_id = str(metadata.get("user_id") or payload.get("user_id") or "").strip()
        if not raw_user_id:
            raise ValueError("subscription user_id is required for first provider event")
        plan = await self._find_plan(db, payload)
        if plan is None:
            raise ValueError("subscription plan is required for first provider event")

        subscription = UserSubscription(
            user_id=uuid.UUID(raw_user_id),
            plan_id=plan.id,
            provider=provider,
            provider_customer_id=self._provider_customer_id(payload),
            provider_subscription_id=provider_subscription_id,
            status=SubscriptionStatus.ACTIVE,
            metadata_json={"provider_payload": payload},
        )
        subscription.plan = plan
        db.add(subscription)
        await db.flush()
        return subscription

    def _credits_for_period(self, subscription: UserSubscription, payload: dict[str, Any]) -> int:
        metadata = self._metadata(payload)
        for source in (payload, metadata):
            value = source.get("credits") or source.get("monthly_credits")
            if value is not None:
                return int(value)
        return int(getattr(subscription.plan, "monthly_credits", 0) or 0)

    def _subscription_product_id(self, plan_code: str) -> str:
        mapping = {
            "starter_monthly": settings.creem_subscription_starter_product_id,
            "creator_monthly": settings.creem_subscription_creator_product_id,
            "studio_monthly": settings.creem_subscription_studio_product_id,
        }
        return str(mapping.get(plan_code) or "").strip()

    def _api_base_url(self) -> str:
        return (settings.creem_api_base_url or "https://api.creem.io").rstrip("/")

    def _headers(self) -> dict[str, str]:
        if not settings.creem_api_key:
            raise SubscriptionError(
                code="creem_not_configured",
                message="Subscription provider is not configured.",
                status_code=503,
            )
        return {
            "x-api-key": settings.creem_api_key,
            "Content-Type": "application/json",
        }

    def _append_query(self, url: str, **params: str) -> str:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update({k: v for k, v in params.items() if v})
        return urlunparse(parsed._replace(query=urlencode(query), fragment=parsed.fragment))

    def _safe_return_url(self, return_url: str | None) -> str:
        candidate = str(return_url or "").strip()
        if not candidate:
            return settings.effective_frontend_base_url.rstrip("/")
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return settings.effective_frontend_base_url.rstrip("/")
        if parsed.scheme != "https" and not settings.using_manual_review_payments:
            default = settings.effective_frontend_base_url.rstrip("/")
            default_parsed = urlparse(default)
            if default_parsed.scheme == "https" and default_parsed.hostname:
                return urlunparse(
                    default_parsed._replace(
                        path=parsed.path or default_parsed.path,
                        query=parsed.query,
                        fragment=parsed.fragment,
                    )
                )
        return candidate

    async def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(
                method,
                f"{self._api_base_url()}{path}",
                json=json_body,
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

    def _extract_checkout_url(self, payload: dict[str, Any]) -> str:
        for key in ("checkout_url", "url", "hosted_checkout_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("data", "checkout", "result"):
            child = payload.get(key)
            if isinstance(child, dict):
                result = self._extract_checkout_url(child)
                if result:
                    return result
        return ""

    async def list_active_plans(self, db: AsyncSession) -> list[SubscriptionPlan]:
        await self.ensure_default_plans(db)
        result = await db.execute(
            select(SubscriptionPlan)
            .where(SubscriptionPlan.is_active.is_(True))
            .order_by(SubscriptionPlan.price_cents.asc(), SubscriptionPlan.code.asc())
        )
        return list(result.scalars().all())

    async def ensure_default_plans(self, db: AsyncSession) -> None:
        for plan in DEFAULT_PLAN_SEEDS:
            statement = (
                pg_insert(SubscriptionPlan)
                .values(**plan)
                .on_conflict_do_nothing(index_elements=["code"])
            )
            await db.execute(statement)
        await db.flush()

    async def get_current_subscription(self, db: AsyncSession, user_id: uuid.UUID) -> UserSubscription | None:
        result = await db.execute(
            select(UserSubscription)
            .where(
                UserSubscription.user_id == user_id,
                UserSubscription.status.in_(
                    [
                        SubscriptionStatus.TRIALING.value,
                        SubscriptionStatus.ACTIVE.value,
                        SubscriptionStatus.PAST_DUE.value,
                    ]
                ),
            )
            .order_by(UserSubscription.created_at.desc())
        )
        return result.scalars().first()

    async def create_checkout(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        plan_code: str,
        return_url: str | None,
    ) -> dict[str, str]:
        normalized_plan_code = str(plan_code or "").strip()
        await self.ensure_default_plans(db)
        result = await db.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.code == normalized_plan_code,
                SubscriptionPlan.is_active.is_(True),
            )
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise SubscriptionError(code="plan_not_found", message="Subscription plan not found.", status_code=404)

        safe_return_url = self._safe_return_url(return_url)
        if settings.using_manual_review_payments:
            return {
                "provider": "manual_review",
                "status": "pending",
                "checkout_url": self._append_query(
                    safe_return_url,
                    subscription="manual_review",
                    plan_code=plan.code,
                ),
            }

        product_id = self._subscription_product_id(plan.code)
        if not product_id:
            raise SubscriptionError(
                code="subscription_product_missing",
                message=f"Subscription product is not configured for plan {plan.code}.",
                status_code=503,
            )

        success_url = self._append_query(safe_return_url, subscription="success", plan_code=plan.code)
        payload = {
            "product_id": product_id,
            "request_id": str(uuid.uuid4()),
            "success_url": success_url,
            "metadata": {
                "user_id": str(user_id),
                "plan_code": plan.code,
                "monthly_credits": int(plan.monthly_credits or 0),
            },
        }
        try:
            response_data = await self._request("POST", "/v1/checkouts", json_body=payload)
        except httpx.HTTPError as exc:
            raise SubscriptionError(
                code="subscription_checkout_unavailable",
                message="Unable to start subscription checkout.",
                status_code=503,
            ) from exc

        checkout_url = self._extract_checkout_url(response_data)
        if not checkout_url:
            raise SubscriptionError(
                code="subscription_checkout_invalid",
                message="Subscription checkout URL is missing from provider response.",
                status_code=503,
            )
        return {"provider": "creem", "status": "pending", "checkout_url": checkout_url}

    async def cancel_current_subscription(self, db: AsyncSession, user_id: uuid.UUID) -> UserSubscription:
        subscription = await self.get_current_subscription(db, user_id)
        if subscription is None:
            raise SubscriptionError(code="subscription_not_found", message="Subscription not found.", status_code=404)
        subscription.cancel_at_period_end = True
        subscription.metadata_json = {
            **(subscription.metadata_json or {}),
            "cancel_requested_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.flush()
        return subscription

    async def process_provider_event(
        self,
        db: AsyncSession,
        *,
        provider: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> UserSubscription | None:
        normalized_event_type = str(event_type or "").strip().lower()
        subscription = await self._get_or_create_subscription(db, provider=provider, payload=payload)
        status = self._status_value(payload.get("status"))

        if normalized_event_type in {"subscription.canceled", "subscription.cancelled"}:
            status = SubscriptionStatus.CANCELED.value
        elif normalized_event_type == "subscription.scheduled_cancel":
            status = SubscriptionStatus.ACTIVE.value
        elif normalized_event_type == "subscription.past_due":
            status = SubscriptionStatus.PAST_DUE.value
        elif normalized_event_type in {"subscription.unpaid", "subscription.paused"}:
            status = SubscriptionStatus.PAST_DUE.value
        elif normalized_event_type == "subscription.expired":
            status = SubscriptionStatus.EXPIRED.value
        elif normalized_event_type == "subscription.trialing":
            status = SubscriptionStatus.TRIALING.value
        elif not status and normalized_event_type in self._ACTIVE_EVENTS:
            status = SubscriptionStatus.ACTIVE.value

        if status:
            subscription.status = status
        customer_id = self._provider_customer_id(payload)
        if customer_id:
            subscription.provider_customer_id = customer_id
        subscription.current_period_start = self._parse_datetime(
            payload.get("current_period_start")
            or payload.get("current_period_start_date")
            or payload.get("period_start")
        ) or subscription.current_period_start
        subscription.current_period_end = self._parse_datetime(
            payload.get("current_period_end")
            or payload.get("current_period_end_date")
            or payload.get("period_end")
        ) or subscription.current_period_end
        if normalized_event_type == "subscription.scheduled_cancel":
            subscription.cancel_at_period_end = True
        elif "cancel_at_period_end" in payload:
            subscription.cancel_at_period_end = bool(payload.get("cancel_at_period_end"))
        subscription.metadata_json = {
            **(subscription.metadata_json or {}),
            "last_event_type": normalized_event_type,
            "last_provider_payload": payload,
        }

        if normalized_event_type in {"subscription.paid", "invoice.paid", "subscription.payment_succeeded"}:
            await self.grant_period_credits(
                db,
                subscription,
                period_key=self._period_key(payload),
                credits=self._credits_for_period(subscription, payload),
            )

        await db.flush()
        return subscription

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
