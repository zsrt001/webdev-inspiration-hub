"""Provider-neutral subscription projections backed by immutable paid facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import uuid

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.payment_event import PaymentEvent, PaymentEventProcessingState
from app.models.subscription_cancel_intent import CancelIntentState, SubscriptionCancelIntent
from app.models.subscription_checkout_intent import (
    SubscriptionCheckoutIntent,
    SubscriptionCheckoutIntentState,
)
from app.models.subscription_credit_grant import SubscriptionCreditGrant
from app.models.subscription_invoice import (
    SubscriptionInvoice,
    SubscriptionInvoiceAdjustmentFact,
)
from app.models.subscription_plan import SubscriptionPlan
from app.models.user_credit import UserCredit
from app.models.user_subscription import (
    NormalizedSubscriptionStatus,
    SubscriptionStatus,
    UserSubscription,
)
from app.services.billing_catalog_service import (
    BillingCatalogUnavailable,
    CheckoutCatalogSelection,
    load_active_catalog,
    require_subscription_checkout_catalog_product,
    require_subscription_catalog_product,
)
from app.services.idempotency_service import (
    IdempotencyConflict,
    canonical_request_hash,
    lock_idempotency_scope,
)
from app.services.credit_reversal_service import CreditReversalError, reverse_root_grant
from app.services.payment_reconciliation_service import (
    freeze_unspent_purchase_lineage,
    open_payment_reconciliation_case,
    unfreeze_purchase_lineage,
)


settings = get_settings()


class SubscriptionError(RuntimeError):
    def __init__(self, *, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(code)


class SubscriptionFactInvalid(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CancellationReconciliationPending(SubscriptionError):
    def __init__(self, intent_id: uuid.UUID | None = None):
        self.intent_id = intent_id
        super().__init__(
            code="subscription_cancellation_reconciliation_pending",
            message="Cancellation status requires Provider reconciliation.",
            status_code=409,
        )


class SubscriptionCheckoutReconciliationPending(SubscriptionError):
    def __init__(self, intent_id: uuid.UUID | None = None):
        self.intent_id = intent_id
        super().__init__(
            code="subscription_checkout_reconciliation_pending",
            message="Subscription checkout status requires Provider reconciliation.",
            status_code=409,
        )


@dataclass(frozen=True, slots=True)
class ValidatedSubscriptionPaidFact:
    provider_transaction_id: str
    period_start: datetime
    period_end: datetime
    pre_tax_minor_units: int
    tax_minor_units: int
    currency: str

    @property
    def total_minor_units(self) -> int:
        return self.pre_tax_minor_units + self.tax_minor_units


@dataclass(frozen=True, slots=True)
class SubscriptionGrantResult:
    invoice: SubscriptionInvoice
    grant: SubscriptionCreditGrant | None
    replayed: bool


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _aware(value: datetime | None, *, field: str) -> datetime:
    if value is None or value.tzinfo is None:
        raise SubscriptionFactInvalid(f"{field}_invalid")
    return value.astimezone(timezone.utc)


def normalized_status_for_event(
    event_type: str,
    raw_status: str | None,
) -> NormalizedSubscriptionStatus:
    event = str(event_type or "").strip().lower()
    status = str(raw_status or "").strip().lower()
    if event == "subscription.scheduled_cancel" or status == "scheduled_cancel":
        return NormalizedSubscriptionStatus.CANCEL_REQUESTED
    if event == "subscription.canceled" or status in {"canceled", "cancelled"}:
        return NormalizedSubscriptionStatus.CANCELED
    if event == "subscription.expired" or status == "expired":
        return NormalizedSubscriptionStatus.EXPIRED
    if event == "subscription.past_due" or status in {"past_due", "unpaid", "paused"}:
        return NormalizedSubscriptionStatus.PAST_DUE
    if status in {"trial", "trialing", "pending"} or event == "subscription.trialing":
        return NormalizedSubscriptionStatus.PENDING
    if event in {"subscription.active", "subscription.paid"} or status == "active":
        return NormalizedSubscriptionStatus.ACTIVE
    raise SubscriptionFactInvalid("subscription_status_unrecognized")


def validate_subscription_paid_fact(
    *,
    provider_transaction_id: str | None,
    period_start: datetime | None,
    period_end: datetime | None,
    event_pre_tax_minor_units: int | None,
    event_tax_minor_units: int | None,
    event_currency: str | None,
    catalog_pre_tax_minor_units: int,
    catalog_currency: str,
) -> ValidatedSubscriptionPaidFact:
    transaction_id = str(provider_transaction_id or "").strip()
    if not transaction_id or len(transaction_id) > 128:
        raise SubscriptionFactInvalid("subscription_transaction_id_required")
    start = _aware(period_start, field="subscription_period_start")
    end = _aware(period_end, field="subscription_period_end")
    if end <= start:
        raise SubscriptionFactInvalid("subscription_period_invalid")
    if event_pre_tax_minor_units is None or isinstance(event_pre_tax_minor_units, bool):
        raise SubscriptionFactInvalid("subscription_pre_tax_amount_required")
    if event_tax_minor_units is None or isinstance(event_tax_minor_units, bool):
        raise SubscriptionFactInvalid("subscription_tax_amount_required")
    pre_tax = int(event_pre_tax_minor_units)
    tax = int(event_tax_minor_units)
    currency = str(event_currency or "").upper()
    if pre_tax != int(catalog_pre_tax_minor_units):
        raise SubscriptionFactInvalid("subscription_catalog_amount_mismatch")
    if tax < 0:
        raise SubscriptionFactInvalid("subscription_tax_amount_invalid")
    if currency != str(catalog_currency).upper():
        raise SubscriptionFactInvalid("subscription_catalog_currency_mismatch")
    return ValidatedSubscriptionPaidFact(
        provider_transaction_id=transaction_id,
        period_start=start,
        period_end=end,
        pre_tax_minor_units=pre_tax,
        tax_minor_units=tax,
        currency=currency,
    )


def cancel_replay_or_raise(intent: SubscriptionCancelIntent) -> dict:
    state = _value(intent.state)
    if state == CancelIntentState.CONFIRMED.value:
        if not isinstance(intent.stored_response, dict):
            raise CancellationReconciliationPending(intent.id)
        return dict(intent.stored_response)
    if state in {
        CancelIntentState.CALLING.value,
        CancelIntentState.UNKNOWN.value,
        CancelIntentState.FAILED_RETRYABLE.value,
    }:
        raise CancellationReconciliationPending(intent.id)
    raise SubscriptionError(
        code="subscription_cancel_not_ready",
        message="Cancellation intent is not ready.",
        status_code=409,
    )


def subscription_checkout_replay_or_raise(
    intent: SubscriptionCheckoutIntent,
) -> dict[str, str]:
    state = _value(intent.state)
    if state in {
        SubscriptionCheckoutIntentState.READY.value,
        SubscriptionCheckoutIntentState.CONFIRMED.value,
    }:
        stored = intent.stored_response
        if not isinstance(stored, dict):
            raise SubscriptionCheckoutReconciliationPending(intent.id)
        try:
            checkout_url = str(stored["checkout_url"]).strip()
        except KeyError as exc:
            raise SubscriptionCheckoutReconciliationPending(intent.id) from exc
        if not checkout_url:
            raise SubscriptionCheckoutReconciliationPending(intent.id)
        return {
            "provider": str(stored.get("provider") or "creem"),
            "status": "READY",
            "checkout_url": checkout_url,
        }
    if state in {
        SubscriptionCheckoutIntentState.CALLING.value,
        SubscriptionCheckoutIntentState.UNKNOWN.value,
        SubscriptionCheckoutIntentState.FAILED_RETRYABLE.value,
    }:
        raise SubscriptionCheckoutReconciliationPending(intent.id)
    raise SubscriptionError(
        code="subscription_checkout_not_ready",
        message="Subscription checkout intent is not ready.",
        status_code=409,
    )


class SubscriptionService:
    def _api_base_url(self) -> str:
        return (settings.creem_api_base_url or "https://api.creem.io").rstrip("/")

    def _headers(self) -> dict[str, str]:
        key = str(settings.creem_api_key or "").strip()
        if not key:
            raise SubscriptionError(
                code="creem_not_configured",
                message="Subscription provider is not configured.",
                status_code=503,
            )
        return {"x-api-key": key, "Content-Type": "application/json"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            response = await client.request(
                method,
                f"{self._api_base_url()}{path}",
                json=json_body,
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise SubscriptionError(
                    code="subscription_provider_response_invalid",
                    message="Subscription provider returned an invalid response.",
                    status_code=503,
                )
            return data

    def _append_query(self, url: str, **params: str) -> str:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update({key: value for key, value in params.items() if value})
        return urlunparse(
            parsed._replace(
                path=parsed.path or "/",
                query=urlencode(query),
                fragment=parsed.fragment,
            )
        )

    def _safe_return_url(self, return_url: str | None) -> str:
        default = settings.effective_frontend_base_url.rstrip("/")
        candidate = str(return_url or "").strip()
        if not candidate:
            return default
        parsed = urlparse(candidate)
        default_parsed = urlparse(default)
        allowed = {default_parsed.hostname, "localhost", "127.0.0.1"}
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.hostname not in allowed
            or parsed.username
            or parsed.password
        ):
            return default
        if parsed.scheme != "https" and default_parsed.scheme == "https":
            return urlunparse(
                default_parsed._replace(
                    path=parsed.path or default_parsed.path,
                    query=parsed.query,
                    fragment=parsed.fragment,
                )
            )
        return candidate

    @staticmethod
    def _checkout_provider_request_id(
        user_id: uuid.UUID,
        idempotency_key: str,
    ) -> str:
        digest = hashlib.sha256(
            f"{user_id}:{idempotency_key}:subscription-checkout".encode("utf-8")
        ).hexdigest()
        return f"subco_{digest}"

    @staticmethod
    def _checkout_request_hash(
        selection: CheckoutCatalogSelection,
        *,
        user_id: uuid.UUID,
        return_url: str,
    ) -> str:
        return canonical_request_hash(
            {
                "user_id": str(user_id),
                "product": selection.as_snapshot(),
                "return_url": return_url,
            }
        )

    @staticmethod
    def _extract_checkout_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        for key in ("checkout", "result"):
            child = payload.get(key)
            if isinstance(child, dict):
                return child
        data = payload.get("data")
        if isinstance(data, dict):
            return SubscriptionService._extract_checkout_payload(data)
        return payload

    @staticmethod
    def _checkout_product_id(payload: dict[str, Any]) -> str | None:
        value = payload.get("product_id") or payload.get("product")
        if isinstance(value, dict):
            value = value.get("id")
        if value is None:
            order = payload.get("order")
            if isinstance(order, dict):
                value = order.get("product")
                if isinstance(value, dict):
                    value = value.get("id")
        clean = str(value or "").strip()
        return clean or None

    @staticmethod
    def _checkout_url(payload: dict[str, Any]) -> str | None:
        for key in ("checkout_url", "url", "hosted_checkout_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _validate_checkout_response(
        self,
        payload: dict[str, Any],
        *,
        intent: SubscriptionCheckoutIntent,
    ) -> tuple[str, str]:
        checkout_id = str(payload.get("id") or payload.get("checkout_id") or "").strip()
        checkout_url = self._checkout_url(payload)
        returned_request_id = str(payload.get("request_id") or "").strip()
        returned_product_id = self._checkout_product_id(payload)
        snapshot = dict(intent.catalog_snapshot or {})
        expected_product_id = str(snapshot.get("provider_product_id") or "").strip()
        if (
            not checkout_id
            or returned_request_id != intent.provider_request_id
            or not expected_product_id
            or returned_product_id != expected_product_id
        ):
            raise SubscriptionError(
                code="subscription_checkout_identity_mismatch",
                message="Subscription checkout identity did not match the local intent.",
                status_code=503,
            )
        if not checkout_url:
            raise SubscriptionError(
                code="subscription_checkout_url_missing",
                message="Subscription provider did not return a checkout URL.",
                status_code=503,
            )
        parsed = urlparse(checkout_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise SubscriptionError(
                code="subscription_checkout_url_invalid",
                message="Subscription provider returned an unsafe checkout URL.",
                status_code=503,
            )
        status = str(payload.get("status") or "").strip().lower()
        if status and status not in {"pending", "processing", "completed"}:
            raise SubscriptionError(
                code="subscription_checkout_status_invalid",
                message="Subscription provider returned an invalid checkout status.",
                status_code=503,
            )
        product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
        order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
        returned_amount = product.get("price")
        if returned_amount is None:
            returned_amount = order.get("sub_total")
        returned_currency = product.get("currency") or order.get("currency")
        if returned_amount is not None and (
            isinstance(returned_amount, bool)
            or not isinstance(returned_amount, int)
            or returned_amount != int(snapshot.get("pre_tax_minor_units") or -1)
        ):
            raise SubscriptionError(
                code="subscription_checkout_amount_mismatch",
                message="Subscription checkout amount did not match the active catalog.",
                status_code=503,
            )
        if returned_currency is not None and str(returned_currency).upper() != str(
            snapshot.get("currency") or ""
        ).upper():
            raise SubscriptionError(
                code="subscription_checkout_currency_mismatch",
                message="Subscription checkout currency did not match the active catalog.",
                status_code=503,
            )
        return checkout_id, checkout_url

    async def _subscription_plan_for_checkout(
        self,
        db: AsyncSession,
        *,
        product_code: str,
    ) -> SubscriptionPlan:
        plans = list(
            (
                await db.scalars(
                    select(SubscriptionPlan).where(
                        SubscriptionPlan.catalog_product_code == product_code,
                        SubscriptionPlan.is_active.is_(True),
                    )
                )
            ).all()
        )
        if len(plans) != 1:
            raise SubscriptionError(
                code="subscription_plan_projection_unavailable",
                message="Subscription plan projection requires reconciliation.",
                status_code=503,
            )
        return plans[0]

    async def list_active_plans(self, db: AsyncSession):
        try:
            catalog = await load_active_catalog(
                db,
                environment=settings.runtime_environment,
            )
        except BillingCatalogUnavailable as exc:
            raise SubscriptionError(
                code="billing_catalog_unavailable",
                message="The active billing catalog is unavailable.",
                status_code=503,
            ) from exc
        return [product for product in catalog.products if product.product_kind == "subscription"]

    async def get_current_subscription(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> UserSubscription | None:
        rows = list(
            (
                await db.scalars(
                    select(UserSubscription)
                    .where(
                        UserSubscription.user_id == user_id,
                        UserSubscription.normalized_status.in_(
                            [
                                NormalizedSubscriptionStatus.PENDING.value,
                                NormalizedSubscriptionStatus.ACTIVE.value,
                                NormalizedSubscriptionStatus.PAST_DUE.value,
                                NormalizedSubscriptionStatus.CANCEL_REQUESTED.value,
                            ]
                        ),
                    )
                    .order_by(UserSubscription.created_at.desc())
                )
            ).all()
        )
        if len(rows) > 1:
            raise SubscriptionError(
                code="subscription_projection_conflict",
                message="Subscription state requires reconciliation.",
                status_code=409,
            )
        return rows[0] if rows else None

    async def create_checkout(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        plan_code: str,
        return_url: str | None,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        clean_key = str(idempotency_key or "").strip()
        if not clean_key or len(clean_key) > 128:
            raise SubscriptionError(
                code="idempotency_key_required",
                message="A valid Idempotency-Key header is required.",
                status_code=400,
            )
        active = await self.get_current_subscription(db, user_id)
        if active is not None:
            raise SubscriptionError(
                code="subscription_already_nonterminal",
                message="An active or pending subscription already exists.",
                status_code=409,
            )
        self._headers()  # fail before creating an intent if Creem is not configured
        safe_return_url = self._safe_return_url(return_url)
        try:
            selection = await require_subscription_checkout_catalog_product(
                db,
                product_code=str(plan_code or "").strip(),
                provider="creem",
            )
        except BillingCatalogUnavailable as exc:
            code = (
                "plan_not_found"
                if exc.code in {"product_not_active", "subscription_checkout_product_kind_invalid"}
                else "billing_catalog_unavailable"
            )
            raise SubscriptionError(
                code=code,
                message=(
                    "Subscription plan not found."
                    if code == "plan_not_found"
                    else "The active billing catalog is unavailable."
                ),
                status_code=404 if code == "plan_not_found" else 503,
            ) from exc
        plan = await self._subscription_plan_for_checkout(
            db,
            product_code=selection.product.product_code,
        )
        request_hash = self._checkout_request_hash(
            selection,
            user_id=user_id,
            return_url=safe_return_url,
        )
        await lock_idempotency_scope(
            db,
            user_id=user_id,
            endpoint="subscriptions.checkout.single_flight",
            key="user",
        )
        active = await self.get_current_subscription(db, user_id)
        if active is not None:
            raise SubscriptionError(
                code="subscription_already_nonterminal",
                message="An active or pending subscription already exists.",
                status_code=409,
            )
        existing = await db.scalar(
            select(SubscriptionCheckoutIntent)
            .where(
                SubscriptionCheckoutIntent.user_id == user_id,
                SubscriptionCheckoutIntent.idempotency_key == clean_key,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise SubscriptionError(
                    code="idempotency_payload_mismatch",
                    message="The Idempotency-Key was already used for another request.",
                    status_code=409,
                )
            return subscription_checkout_replay_or_raise(existing)
        pending = await db.scalar(
            select(SubscriptionCheckoutIntent)
            .where(
                SubscriptionCheckoutIntent.user_id == user_id,
                SubscriptionCheckoutIntent.state.in_(
                    [
                        SubscriptionCheckoutIntentState.NEW.value,
                        SubscriptionCheckoutIntentState.CALLING.value,
                        SubscriptionCheckoutIntentState.READY.value,
                        SubscriptionCheckoutIntentState.UNKNOWN.value,
                        SubscriptionCheckoutIntentState.FAILED_RETRYABLE.value,
                    ]
                ),
            )
            .with_for_update()
        )
        if pending is not None:
            raise SubscriptionError(
                code="subscription_checkout_already_pending",
                message="A subscription checkout is already pending.",
                status_code=409,
            )
        provider_request_id = self._checkout_provider_request_id(user_id, clean_key)
        intent = SubscriptionCheckoutIntent(
            id=uuid.uuid4(),
            user_id=user_id,
            plan_id=plan.id,
            catalog_version_id=selection.catalog_version_id,
            product_code=selection.product.product_code,
            idempotency_key=clean_key,
            request_hash=request_hash,
            provider_request_id=provider_request_id,
            internal_metadata_id=uuid.uuid4(),
            state=SubscriptionCheckoutIntentState.CALLING,
            catalog_snapshot=selection.as_snapshot(),
            attempt_count=1,
            call_started_at=datetime.now(timezone.utc),
        )
        db.add(intent)
        await db.flush()
        await db.commit()  # durable CALLING boundary before Creem I/O

        success_url = self._append_query(
            safe_return_url,
            subscription="return",
            subscription_checkout_intent_id=str(intent.id),
        )
        provider_request = {
            "product_id": selection.product.provider_product_id,
            "request_id": provider_request_id,
            "units": 1,
            "success_url": success_url,
            "metadata": {
                "vowpic_subscription_checkout_ref": str(intent.internal_metadata_id)
            },
        }
        try:
            response_data = await self._request(
                "POST",
                "/v1/checkouts",
                json_body=provider_request,
            )
        except httpx.RequestError as exc:
            intent.state = SubscriptionCheckoutIntentState.UNKNOWN
            intent.last_error = f"creem_subscription_checkout_ambiguous:{type(exc).__name__}"
            await db.flush()
            await db.commit()
            raise SubscriptionCheckoutReconciliationPending(intent.id) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                intent.state = SubscriptionCheckoutIntentState.UNKNOWN
            else:
                intent.state = SubscriptionCheckoutIntentState.FAILED_RETRYABLE
            intent.last_error = (
                f"creem_subscription_checkout_rejected:{exc.response.status_code}"
            )
            await db.flush()
            await db.commit()
            raise SubscriptionCheckoutReconciliationPending(intent.id) from exc
        except SubscriptionError as exc:
            intent.state = SubscriptionCheckoutIntentState.UNKNOWN
            intent.last_error = "creem_subscription_checkout_response_invalid"
            await db.flush()
            await db.commit()
            raise SubscriptionCheckoutReconciliationPending(intent.id) from exc

        checkout_payload = self._extract_checkout_payload(response_data)
        try:
            checkout_id, checkout_url = self._validate_checkout_response(
                checkout_payload,
                intent=intent,
            )
        except SubscriptionError:
            intent.state = SubscriptionCheckoutIntentState.UNKNOWN
            intent.last_error = "creem_subscription_checkout_validation_failed"
            await db.flush()
            await db.commit()
            raise
        locked = await db.scalar(
            select(SubscriptionCheckoutIntent)
            .where(SubscriptionCheckoutIntent.id == intent.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:
            raise SubscriptionCheckoutReconciliationPending(intent.id)
        if locked.provider_checkout_id not in {None, checkout_id}:
            raise SubscriptionCheckoutReconciliationPending(intent.id)
        stored_response = {
            "provider": "creem",
            "status": "READY",
            "checkout_url": checkout_url,
        }
        locked.provider_checkout_id = checkout_id
        locked.checkout_url = checkout_url
        locked.stored_response = stored_response
        locked.ready_at = datetime.now(timezone.utc)
        locked.last_error = None
        if _value(locked.state) in {
            SubscriptionCheckoutIntentState.CALLING.value,
            SubscriptionCheckoutIntentState.UNKNOWN.value,
        }:
            locked.state = SubscriptionCheckoutIntentState.READY
        elif _value(locked.state) != SubscriptionCheckoutIntentState.CONFIRMED.value:
            raise SubscriptionCheckoutReconciliationPending(locked.id)
        await db.flush()
        await db.commit()
        return stored_response

    @staticmethod
    def _parse_provider_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc) if value.tzinfo else None
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) or str(value).isdigit():
            numeric = float(value)
            if numeric > 10_000_000_000:
                numeric /= 1000.0
            try:
                return datetime.fromtimestamp(numeric, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None

    async def _mark_event_reconciliation(
        self,
        db: AsyncSession,
        *,
        event: PaymentEvent,
        subscription: UserSubscription | None,
        reason_code: str,
    ) -> None:
        await open_payment_reconciliation_case(
            db,
            purchase=None,
            case_key=f"subscription-event:{event.event_id}",
            subject_type="subscription" if subscription is not None else "payment_event",
            subject_id=str(subscription.id if subscription is not None else event.id),
            reason_code=reason_code,
            raw_payload_sha256=str(event.raw_payload_sha256),
        )
        event.processing_state = PaymentEventProcessingState.RECONCILIATION_REQUIRED
        event.error = reason_code

    async def _checkout_intent_for_event(
        self,
        db: AsyncSession,
        event: PaymentEvent,
    ) -> SubscriptionCheckoutIntent | None:
        metadata = dict(event.business_metadata or {})
        internal_ref: uuid.UUID | None = None
        try:
            if metadata.get("vowpic_subscription_checkout_ref"):
                internal_ref = uuid.UUID(
                    str(metadata["vowpic_subscription_checkout_ref"])
                )
        except ValueError:
            internal_ref = None
        conditions = []
        if event.request_id:
            conditions.append(
                SubscriptionCheckoutIntent.provider_request_id == event.request_id
            )
        if internal_ref is not None:
            conditions.append(
                SubscriptionCheckoutIntent.internal_metadata_id == internal_ref
            )
        provider_checkout_id = str(
            metadata.get("provider_checkout_id") or ""
        ).strip()
        if provider_checkout_id:
            conditions.append(
                SubscriptionCheckoutIntent.provider_checkout_id
                == provider_checkout_id
            )
        if not conditions:
            return None
        rows = list(
            (
                await db.scalars(
                    select(SubscriptionCheckoutIntent)
                    .where(or_(*conditions))
                    .with_for_update()
                )
            ).all()
        )
        unique = {row.id: row for row in rows}
        if len(unique) > 1:
            raise SubscriptionFactInvalid(
                "subscription_checkout_correlation_conflict"
            )
        return next(iter(unique.values())) if unique else None

    @staticmethod
    def _validate_event_checkout_identity(
        event: PaymentEvent,
        intent: SubscriptionCheckoutIntent,
    ) -> None:
        metadata = dict(event.business_metadata or {})
        if event.request_id and event.request_id != intent.provider_request_id:
            raise SubscriptionFactInvalid("subscription_checkout_request_mismatch")
        internal_ref = str(
            metadata.get("vowpic_subscription_checkout_ref") or ""
        ).strip()
        if internal_ref and internal_ref != str(intent.internal_metadata_id):
            raise SubscriptionFactInvalid("subscription_checkout_metadata_mismatch")
        provider_checkout_id = str(
            metadata.get("provider_checkout_id") or ""
        ).strip()
        if (
            provider_checkout_id
            and intent.provider_checkout_id
            and provider_checkout_id != intent.provider_checkout_id
        ):
            raise SubscriptionFactInvalid("subscription_checkout_id_mismatch")
        snapshot = dict(intent.catalog_snapshot or {})
        expected_product_id = str(
            snapshot.get("provider_product_id") or ""
        ).strip()
        provider_product_id = str(
            metadata.get("provider_product_id") or ""
        ).strip()
        if (
            not expected_product_id
            or not provider_product_id
            or provider_product_id != expected_product_id
        ):
            raise SubscriptionFactInvalid("subscription_checkout_product_mismatch")
        if event.pre_tax_minor_units is not None and int(
            event.pre_tax_minor_units
        ) != int(snapshot.get("pre_tax_minor_units") or -1):
            raise SubscriptionFactInvalid("subscription_checkout_amount_mismatch")
        if event.currency is not None and str(event.currency).upper() != str(
            snapshot.get("currency") or ""
        ).upper():
            raise SubscriptionFactInvalid("subscription_checkout_currency_mismatch")

    async def _create_subscription_projection_from_intent(
        self,
        db: AsyncSession,
        *,
        event: PaymentEvent,
        intent: SubscriptionCheckoutIntent,
        provider_subscription_id: str,
    ) -> UserSubscription:
        self._validate_event_checkout_identity(event, intent)
        rows = list(
            (
                await db.scalars(
                    select(UserSubscription)
                    .where(
                        UserSubscription.user_id == intent.user_id,
                        UserSubscription.normalized_status.in_(
                            [
                                NormalizedSubscriptionStatus.PENDING.value,
                                NormalizedSubscriptionStatus.ACTIVE.value,
                                NormalizedSubscriptionStatus.PAST_DUE.value,
                                NormalizedSubscriptionStatus.CANCEL_REQUESTED.value,
                            ]
                        ),
                    )
                    .with_for_update()
                )
            ).all()
        )
        if len(rows) > 1:
            raise SubscriptionFactInvalid("subscription_projection_conflict")
        if rows:
            subscription = rows[0]
            if (
                subscription.provider != "creem"
                or subscription.provider_subscription_id
                != provider_subscription_id
                or subscription.user_id != intent.user_id
            ):
                raise SubscriptionFactInvalid(
                    "subscription_projection_checkout_conflict"
                )
        else:
            snapshot = dict(intent.catalog_snapshot or {})
            subscription = UserSubscription(
                id=uuid.uuid4(),
                user_id=intent.user_id,
                plan_id=intent.plan_id,
                provider="creem",
                provider_customer_id=event.customer_id,
                provider_subscription_id=provider_subscription_id,
                status=SubscriptionStatus.TRIALING,
                normalized_status=NormalizedSubscriptionStatus.PENDING,
                catalog_version_id=intent.catalog_version_id,
                product_code=intent.product_code,
                catalog_snapshot=snapshot,
                cancel_at_period_end=False,
                metadata_json={
                    "subscription_checkout_intent_id": str(intent.id),
                },
            )
            db.add(subscription)
            await db.flush()
        intent.provider_subscription_id = provider_subscription_id
        provider_checkout_id = str(
            (event.business_metadata or {}).get("provider_checkout_id") or ""
        ).strip()
        if provider_checkout_id:
            intent.provider_checkout_id = provider_checkout_id
        intent.provider_evidence = {
            "payment_event_id": str(event.id),
            "event_id": event.event_id,
            "raw_payload_sha256": event.raw_payload_sha256,
        }
        intent.confirmed_at = event.occurred_at or datetime.now(timezone.utc)
        intent.last_error = None
        intent.state = SubscriptionCheckoutIntentState.CONFIRMED
        await db.flush()
        return subscription

    async def _find_subscription_for_event(
        self,
        db: AsyncSession,
        event: PaymentEvent,
    ) -> UserSubscription | None:
        subscription = await db.scalar(
            select(UserSubscription)
            .where(
                UserSubscription.provider == "creem",
                UserSubscription.provider_subscription_id == event.object_id,
            )
            .with_for_update()
        )
        if subscription is not None:
            return subscription
        if not str(event.event_type or "").lower().startswith("subscription."):
            return None
        intent = await self._checkout_intent_for_event(db, event)
        provider_subscription_id = str(event.object_id or "").strip()
        if intent is None or not provider_subscription_id:
            return None
        return await self._create_subscription_projection_from_intent(
            db,
            event=event,
            intent=intent,
            provider_subscription_id=provider_subscription_id,
        )

    async def apply_checkout_completed_event(
        self,
        db: AsyncSession,
        *,
        event: PaymentEvent,
    ) -> bool:
        """Claim a signed checkout event only when it matches a subscription intent."""

        try:
            intent = await self._checkout_intent_for_event(db, event)
        except SubscriptionFactInvalid as exc:
            await self._mark_event_reconciliation(
                db,
                event=event,
                subscription=None,
                reason_code=exc.code,
            )
            return True
        if intent is None:
            return False
        try:
            self._validate_event_checkout_identity(event, intent)
            provider_checkout_id = str(
                (event.business_metadata or {}).get("provider_checkout_id")
                or event.object_id
                or ""
            ).strip()
            if not provider_checkout_id:
                raise SubscriptionFactInvalid(
                    "subscription_checkout_id_missing"
                )
            if (
                intent.provider_checkout_id
                and intent.provider_checkout_id != provider_checkout_id
            ):
                raise SubscriptionFactInvalid(
                    "subscription_checkout_id_mismatch"
                )
            intent.provider_checkout_id = provider_checkout_id
            intent.provider_evidence = {
                "payment_event_id": str(event.id),
                "event_id": event.event_id,
                "raw_payload_sha256": event.raw_payload_sha256,
            }
            provider_subscription_id = str(
                (event.business_metadata or {}).get(
                    "provider_subscription_id"
                )
                or ""
            ).strip()
            if provider_subscription_id:
                await self._create_subscription_projection_from_intent(
                    db,
                    event=event,
                    intent=intent,
                    provider_subscription_id=provider_subscription_id,
                )
            elif _value(intent.state) == SubscriptionCheckoutIntentState.CALLING.value:
                intent.state = SubscriptionCheckoutIntentState.UNKNOWN
                intent.last_error = (
                    "creem_checkout_completed_without_subscription_id"
                )
        except SubscriptionFactInvalid as exc:
            await self._mark_event_reconciliation(
                db,
                event=event,
                subscription=None,
                reason_code=exc.code,
            )
            return True
        event.processing_state = PaymentEventProcessingState.APPLIED
        event.processed_at = datetime.now(timezone.utc)
        event.error = None
        await db.flush()
        return True

    async def _find_invoice_candidate(
        self,
        db: AsyncSession,
        *,
        subscription: UserSubscription,
        event: PaymentEvent,
        paid: ValidatedSubscriptionPaidFact,
    ) -> SubscriptionInvoice | None:
        rows = list(
            (
                await db.scalars(
                    select(SubscriptionInvoice)
                    .where(
                        or_(
                            SubscriptionInvoice.provider_transaction_id
                            == paid.provider_transaction_id,
                            SubscriptionInvoice.payment_event_id == event.id,
                            (
                                (SubscriptionInvoice.subscription_id == subscription.id)
                                & (SubscriptionInvoice.period_start == paid.period_start)
                                & (SubscriptionInvoice.period_end == paid.period_end)
                            ),
                        )
                    )
                    .with_for_update()
                )
            ).all()
        )
        unique = {row.id: row for row in rows}
        if len(unique) > 1:
            raise SubscriptionFactInvalid("subscription_invoice_uniqueness_conflict")
        invoice = next(iter(unique.values())) if unique else None
        if invoice is not None and (
            invoice.subscription_id != subscription.id
            or invoice.provider_transaction_id != paid.provider_transaction_id
            or invoice.period_start != paid.period_start
            or invoice.period_end != paid.period_end
            or invoice.pre_tax_minor_units != paid.pre_tax_minor_units
            or invoice.tax_minor_units != paid.tax_minor_units
            or invoice.currency != paid.currency
        ):
            raise SubscriptionFactInvalid("subscription_invoice_replay_conflict")
        return invoice

    @staticmethod
    def _lot_expiry(retention_tier: str, period_end: datetime) -> datetime:
        days = {
            "subscription_180d": 180,
            "studio_365d": 365,
            "paid_90d": 90,
        }.get(str(retention_tier))
        if days is None:
            raise SubscriptionFactInvalid("subscription_retention_tier_invalid")
        return period_end + timedelta(days=days)

    async def apply_subscription_paid_transaction(
        self,
        db: AsyncSession,
        *,
        event: PaymentEvent,
        subscription: UserSubscription,
    ) -> SubscriptionGrantResult:
        metadata = dict(event.business_metadata or {})
        transaction_id = str(metadata.get("last_transaction_id") or "").strip()
        period_start = self._parse_provider_datetime(metadata.get("current_period_start_date"))
        period_end = self._parse_provider_datetime(metadata.get("current_period_end_date"))
        provider_product_id = str(metadata.get("provider_product_id") or "").strip()
        try:
            selection = await require_subscription_catalog_product(
                db,
                provider_product_id=provider_product_id,
                pre_tax_minor_units=int(event.pre_tax_minor_units or -1),
                currency=str(event.currency or ""),
            )
        except BillingCatalogUnavailable as exc:
            raise SubscriptionFactInvalid("subscription_catalog_mismatch") from exc
        paid = validate_subscription_paid_fact(
            provider_transaction_id=transaction_id,
            period_start=period_start,
            period_end=period_end,
            event_pre_tax_minor_units=event.pre_tax_minor_units,
            event_tax_minor_units=event.tax_minor_units,
            event_currency=event.currency,
            catalog_pre_tax_minor_units=selection.product.pre_tax_minor_units,
            catalog_currency=selection.product.currency,
        )
        if subscription.product_code and subscription.product_code != selection.product.product_code:
            raise SubscriptionFactInvalid("subscription_product_changed_without_supported_flow")
        invoice = await self._find_invoice_candidate(
            db,
            subscription=subscription,
            event=event,
            paid=paid,
        )
        if invoice is not None and invoice.credit_grant_id is not None:
            grant = await db.scalar(
                select(SubscriptionCreditGrant).where(
                    SubscriptionCreditGrant.id == invoice.credit_grant_id
                )
            )
            if grant is None:
                raise SubscriptionFactInvalid("subscription_invoice_grant_missing")
            return SubscriptionGrantResult(invoice=invoice, grant=grant, replayed=True)
        if invoice is None:
            invoice = SubscriptionInvoice(
                id=uuid.uuid4(),
                subscription_id=subscription.id,
                user_id=subscription.user_id,
                payment_event_id=event.id,
                provider="creem",
                provider_transaction_id=paid.provider_transaction_id,
                provider_invoice_id=str(metadata.get("provider_invoice_id") or "").strip() or None,
                period_start=paid.period_start,
                period_end=paid.period_end,
                pre_tax_minor_units=paid.pre_tax_minor_units,
                tax_minor_units=paid.tax_minor_units,
                currency=paid.currency,
                provider_status=str(event.normalized_status or "paid"),
                occurred_at=event.occurred_at,
                raw_payload_sha256=str(event.raw_payload_sha256),
                catalog_version_id=selection.catalog_version_id,
                catalog_snapshot=selection.as_snapshot(),
                refunded_minor_units=0,
                disputed_minor_units=0,
                dispute_state="NONE",
            )
            db.add(invoice)
            await db.flush()
        credit = await db.scalar(
            select(UserCredit)
            .where(UserCredit.user_id == subscription.user_id)
            .with_for_update()
        )
        if credit is None:
            raise SubscriptionFactInvalid("subscription_credit_account_missing")
        prior_balance = int(credit.balance or 0)
        amount = int(selection.product.credits)
        debt_offset = min(amount, max(0, -prior_balance))
        next_balance = prior_balance + amount
        transaction_id_value = uuid.uuid4()
        lot_id = uuid.uuid4()
        grant_id = uuid.uuid4()
        transaction = CreditTransaction(
            id=transaction_id_value,
            user_id=subscription.user_id,
            transaction_type=CreditTransactionType.SUBSCRIPTION_GRANT,
            amount=amount,
            balance_after=next_balance,
            source="subscription_invoice",
            source_id=str(invoice.id),
            description=f"Verified subscription invoice: {selection.product.product_code}",
            metadata_json={
                "provider": "creem",
                "provider_transaction_id": paid.provider_transaction_id,
                "payment_event_id": str(event.id),
            },
            root_transaction_id=transaction_id_value,
            request_id=f"subscription-paid:{paid.provider_transaction_id}",
        )
        lot = CreditGrantLot(
            id=lot_id,
            user_id=subscription.user_id,
            root_transaction_id=transaction_id_value,
            source_type=GrantLotSourceType.SUBSCRIPTION,
            source_id=str(invoice.id),
            original_amount=amount,
            debt_offset_amount=debt_offset,
            reversed_amount=0,
            frozen_amount=0,
            consumed_amount=0,
            retention_tier=selection.product.retention_tier,
            expires_at=self._lot_expiry(selection.product.retention_tier, paid.period_end),
        )
        grant = SubscriptionCreditGrant(
            id=grant_id,
            subscription_id=subscription.id,
            user_id=subscription.user_id,
            period_key=paid.period_start.isoformat(),
            period_start=paid.period_start,
            period_end=paid.period_end,
            credits=amount,
            invoice_id=invoice.id,
            credit_transaction_id=transaction_id_value,
            grant_lot_id=lot_id,
        )
        db.add(transaction)
        db.add(lot)
        db.add(grant)
        credit.balance = next_balance
        invoice.credit_grant_id = grant_id
        subscription.catalog_version_id = selection.catalog_version_id
        subscription.product_code = selection.product.product_code
        subscription.catalog_snapshot = selection.as_snapshot()
        subscription.last_provider_transaction_id = paid.provider_transaction_id
        subscription.paid_through_at = max(
            [item for item in (subscription.paid_through_at, paid.period_end) if item is not None]
        )
        await db.flush()
        return SubscriptionGrantResult(invoice=invoice, grant=grant, replayed=False)

    async def _invoice_for_adjustment(
        self,
        db: AsyncSession,
        event: PaymentEvent,
    ) -> SubscriptionInvoice | None:
        provider_transaction_id = str(
            (event.business_metadata or {}).get("provider_payment_id") or ""
        ).strip()
        if not provider_transaction_id:
            return None
        return await db.scalar(
            select(SubscriptionInvoice)
            .where(
                SubscriptionInvoice.provider == "creem",
                SubscriptionInvoice.provider_transaction_id == provider_transaction_id,
            )
            .with_for_update()
        )

    async def _invoice_lineage(
        self,
        db: AsyncSession,
        invoice: SubscriptionInvoice,
    ) -> tuple[SubscriptionCreditGrant, CreditGrantLot]:
        if invoice.credit_grant_id is None:
            raise SubscriptionFactInvalid("subscription_invoice_grant_missing")
        grant = await db.scalar(
            select(SubscriptionCreditGrant).where(
                SubscriptionCreditGrant.id == invoice.credit_grant_id
            )
        )
        if (
            grant is None
            or grant.invoice_id != invoice.id
            or grant.credit_transaction_id is None
            or grant.grant_lot_id is None
        ):
            raise SubscriptionFactInvalid("subscription_invoice_lineage_invalid")
        lot = await db.scalar(
            select(CreditGrantLot)
            .where(
                CreditGrantLot.id == grant.grant_lot_id,
                CreditGrantLot.root_transaction_id == grant.credit_transaction_id,
            )
            .with_for_update()
        )
        if lot is None:
            raise SubscriptionFactInvalid("subscription_invoice_grant_lot_missing")
        return grant, lot

    async def _invoice_reconciliation(
        self,
        db: AsyncSession,
        *,
        invoice: SubscriptionInvoice,
        event: PaymentEvent,
        reason_code: str,
        case_key: str,
        freeze_lineage: bool,
    ) -> None:
        if freeze_lineage:
            try:
                _grant, lot = await self._invoice_lineage(db, invoice)
            except SubscriptionFactInvalid:
                lot = None
            if lot is not None:
                freeze_unspent_purchase_lineage(lot)
        reconciliation_user_id = invoice.user_id
        try:
            _grant, lot = await self._invoice_lineage(db, invoice)
        except SubscriptionFactInvalid:
            reconciliation_user_id = invoice.user_id
        else:
            reconciliation_user_id = lot.user_id
        await open_payment_reconciliation_case(
            db,
            purchase=None,
            user_id=reconciliation_user_id,
            case_key=case_key,
            subject_type="subscription_invoice",
            subject_id=str(invoice.id),
            reason_code=reason_code,
            raw_payload_sha256=str(event.raw_payload_sha256),
        )
        event.processing_state = PaymentEventProcessingState.RECONCILIATION_REQUIRED
        event.error = reason_code

    @staticmethod
    def _adjustment_amount(event: PaymentEvent) -> int:
        try:
            return int(str((event.business_metadata or {}).get("event_minor_units") or ""))
        except ValueError:
            return 0

    @staticmethod
    def _subscription_dispute_outcome(event: PaymentEvent) -> str:
        metadata = dict(event.business_metadata or {})
        explicit = str(metadata.get("dispute_outcome") or "").strip().upper()
        status = str(event.normalized_status or "").strip().upper()
        if explicit in {"OPEN", "WON", "LOST"}:
            return explicit
        if status in {"WON", "CLOSED_WON", "RESOLVED_WON"}:
            return "WON"
        if status in {"LOST", "CHARGEBACK", "CLOSED_LOST"}:
            return "LOST"
        if str(event.event_type).lower() == "dispute.created":
            return "OPEN"
        return "REVIEW"

    async def apply_subscription_adjustment_event(
        self,
        db: AsyncSession,
        *,
        event: PaymentEvent,
    ) -> bool:
        """Apply a refund/dispute only when it belongs to a normalized invoice.

        False means the Provider transaction is not a subscription invoice and
        lets the credit-pack path retain authority over the event.
        """

        event_type = str(event.event_type or "").lower()
        if event_type != "refund.created" and not event_type.startswith("dispute."):
            return False
        invoice = await self._invoice_for_adjustment(db, event)
        if invoice is None:
            return False
        adjustment_kind = "REFUND" if event_type == "refund.created" else "DISPUTE"
        provider_object_id = str(
            (event.business_metadata or {}).get(
                "provider_refund_id" if adjustment_kind == "REFUND" else "provider_dispute_id"
            )
            or event.object_id
            or ""
        ).strip()
        amount = self._adjustment_amount(event)
        outcome = (
            "RECEIVED"
            if adjustment_kind == "REFUND"
            else self._subscription_dispute_outcome(event)
        )
        existing = await db.scalar(
            select(SubscriptionInvoiceAdjustmentFact).where(
                or_(
                    SubscriptionInvoiceAdjustmentFact.payment_event_id == event.id,
                    (
                        (SubscriptionInvoiceAdjustmentFact.provider == "creem")
                        & (
                            SubscriptionInvoiceAdjustmentFact.provider_object_id
                            == provider_object_id
                        )
                        & (
                            SubscriptionInvoiceAdjustmentFact.adjustment_kind
                            == adjustment_kind
                        )
                        & (SubscriptionInvoiceAdjustmentFact.outcome == outcome)
                    ),
                )
            )
        )
        if existing is not None:
            if (
                existing.invoice_id != invoice.id
                or int(existing.amount_minor_units) != amount
                or existing.currency != event.currency
            ):
                await self._invoice_reconciliation(
                    db,
                    invoice=invoice,
                    event=event,
                    reason_code="subscription_adjustment_replay_conflict",
                    case_key=f"subscription-adjustment-conflict:{provider_object_id}",
                    freeze_lineage=True,
                )
                await db.flush()
                return True
            event.processing_state = PaymentEventProcessingState.APPLIED
            event.processed_at = datetime.now(timezone.utc)
            event.error = None
            await db.flush()
            return True
        if not provider_object_id or amount <= 0 or str(event.currency or "") != invoice.currency:
            await self._invoice_reconciliation(
                db,
                invoice=invoice,
                event=event,
                reason_code="subscription_adjustment_money_invalid",
                case_key=f"subscription-adjustment-invalid:{event.event_id}",
                freeze_lineage=True,
            )
            await db.flush()
            return True

        total = invoice.total_minor_units
        reversal_transaction_id: uuid.UUID | None = None
        final_outcome = outcome
        if adjustment_kind == "REFUND":
            already_refunded = int(invoice.refunded_minor_units or 0)
            remaining = total - already_refunded
            if already_refunded == 0 and amount == total:
                try:
                    grant, lot = await self._invoice_lineage(db, invoice)
                    reversal = await reverse_root_grant(
                        db,
                        user_id=lot.user_id,
                        root_transaction_id=grant.credit_transaction_id,
                        amount=int(grant.credits),
                        request_id=f"creem-subscription-refund:{provider_object_id}",
                        reason_code="provider_subscription_full_refund",
                        transaction_type=CreditTransactionType.SUBSCRIPTION_REVERSAL,
                        now=event.occurred_at,
                    )
                except (SubscriptionFactInvalid, CreditReversalError) as exc:
                    await self._invoice_reconciliation(
                        db,
                        invoice=invoice,
                        event=event,
                        reason_code=getattr(exc, "code", "subscription_refund_lineage_invalid"),
                        case_key=f"subscription-refund:{provider_object_id}:lineage",
                        freeze_lineage=True,
                    )
                    await db.flush()
                    return True
                reversal_transaction_id = reversal.transaction.id
                invoice.refunded_minor_units = total
                final_outcome = "FULL"
                event.processing_state = PaymentEventProcessingState.APPLIED
                event.processed_at = datetime.now(timezone.utc)
                event.error = None
            else:
                final_outcome = (
                    "OVER_CAPTURE"
                    if amount > max(0, remaining)
                    else "PARTIAL_RECONCILIATION_REQUIRED"
                )
                if amount <= max(0, remaining):
                    invoice.refunded_minor_units = already_refunded + amount
                await self._invoice_reconciliation(
                    db,
                    invoice=invoice,
                    event=event,
                    reason_code=(
                        "subscription_refund_exceeds_invoice"
                        if final_outcome == "OVER_CAPTURE"
                        else "subscription_partial_refund_reconciliation"
                    ),
                    case_key=f"subscription-refund:{provider_object_id}",
                    freeze_lineage=True,
                )
        else:
            if amount > total:
                final_outcome = "OVER_CAPTURE"
                await self._invoice_reconciliation(
                    db,
                    invoice=invoice,
                    event=event,
                    reason_code="subscription_dispute_exceeds_invoice",
                    case_key=f"subscription-dispute:{provider_object_id}:over",
                    freeze_lineage=True,
                )
            elif outcome == "OPEN":
                _grant, lot = await self._invoice_lineage(db, invoice)
                freeze_unspent_purchase_lineage(lot)
                invoice.disputed_minor_units = max(
                    int(invoice.disputed_minor_units or 0), amount
                )
                invoice.dispute_state = "OPEN"
                await open_payment_reconciliation_case(
                    db,
                    purchase=None,
                    user_id=lot.user_id,
                    case_key=f"subscription-dispute:{provider_object_id}:open",
                    subject_type="subscription_invoice",
                    subject_id=str(invoice.id),
                    reason_code="provider_subscription_dispute_open",
                    raw_payload_sha256=str(event.raw_payload_sha256),
                )
                event.processing_state = PaymentEventProcessingState.APPLIED
                event.processed_at = datetime.now(timezone.utc)
                event.error = None
            elif outcome == "WON":
                _grant, lot = await self._invoice_lineage(db, invoice)
                unfreeze_purchase_lineage(lot)
                invoice.disputed_minor_units = max(
                    int(invoice.disputed_minor_units or 0), amount
                )
                invoice.dispute_state = "WON"
                event.processing_state = PaymentEventProcessingState.APPLIED
                event.processed_at = datetime.now(timezone.utc)
                event.error = None
            elif outcome == "LOST" and amount == total:
                try:
                    grant, lot = await self._invoice_lineage(db, invoice)
                    reversal = await reverse_root_grant(
                        db,
                        user_id=lot.user_id,
                        root_transaction_id=grant.credit_transaction_id,
                        amount=int(grant.credits),
                        request_id=f"creem-subscription-dispute:{provider_object_id}:lost",
                        reason_code="provider_subscription_dispute_lost",
                        transaction_type=CreditTransactionType.SUBSCRIPTION_REVERSAL,
                        now=event.occurred_at,
                    )
                except (SubscriptionFactInvalid, CreditReversalError) as exc:
                    await self._invoice_reconciliation(
                        db,
                        invoice=invoice,
                        event=event,
                        reason_code=getattr(exc, "code", "subscription_dispute_lineage_invalid"),
                        case_key=f"subscription-dispute:{provider_object_id}:lineage",
                        freeze_lineage=True,
                    )
                    await db.flush()
                    return True
                reversal_transaction_id = reversal.transaction.id
                invoice.disputed_minor_units = total
                invoice.dispute_state = "LOST"
                event.processing_state = PaymentEventProcessingState.APPLIED
                event.processed_at = datetime.now(timezone.utc)
                event.error = None
            else:
                final_outcome = (
                    "PARTIAL_RECONCILIATION_REQUIRED"
                    if outcome == "LOST"
                    else "REVIEW_RECONCILIATION_REQUIRED"
                )
                invoice.disputed_minor_units = max(
                    int(invoice.disputed_minor_units or 0), amount
                )
                await self._invoice_reconciliation(
                    db,
                    invoice=invoice,
                    event=event,
                    reason_code="subscription_dispute_reconciliation_required",
                    case_key=f"subscription-dispute:{provider_object_id}:{outcome.lower()}",
                    freeze_lineage=True,
                )

        db.add(
            SubscriptionInvoiceAdjustmentFact(
                id=uuid.uuid4(),
                invoice_id=invoice.id,
                payment_event_id=event.id,
                provider="creem",
                provider_object_id=provider_object_id,
                adjustment_kind=adjustment_kind,
                amount_minor_units=amount,
                currency=str(event.currency),
                outcome=final_outcome,
                reversal_transaction_id=reversal_transaction_id,
                occurred_at=event.occurred_at,
            )
        )
        await db.flush()
        return True

    async def apply_normalized_payment_event(
        self,
        db: AsyncSession,
        *,
        event: PaymentEvent,
    ) -> UserSubscription | None:
        try:
            subscription = await self._find_subscription_for_event(db, event)
        except SubscriptionFactInvalid as exc:
            await self._mark_event_reconciliation(
                db,
                event=event,
                subscription=None,
                reason_code=exc.code,
            )
            return None
        if subscription is None:
            await self._mark_event_reconciliation(
                db,
                event=event,
                subscription=None,
                reason_code="subscription_projection_not_found",
            )
            return None
        try:
            status = normalized_status_for_event(event.event_type, event.normalized_status)
        except SubscriptionFactInvalid as exc:
            await self._mark_event_reconciliation(
                db,
                event=event,
                subscription=subscription,
                reason_code=exc.code,
            )
            return subscription
        if str(event.event_type).lower() == "subscription.paid":
            try:
                await self.apply_subscription_paid_transaction(
                    db,
                    event=event,
                    subscription=subscription,
                )
            except SubscriptionFactInvalid as exc:
                await self._mark_event_reconciliation(
                    db,
                    event=event,
                    subscription=subscription,
                    reason_code=exc.code,
                )
                return subscription
        newer = (
            subscription.last_provider_event_at is None
            or event.occurred_at >= subscription.last_provider_event_at
        )
        if newer:
            subscription.normalized_status = status
            subscription.last_provider_event_at = event.occurred_at
            metadata = dict(event.business_metadata or {})
            period_start = self._parse_provider_datetime(metadata.get("current_period_start_date"))
            period_end = self._parse_provider_datetime(metadata.get("current_period_end_date"))
            if period_start is not None:
                subscription.current_period_start = period_start
            if period_end is not None:
                subscription.current_period_end = period_end
            subscription.cancel_at_period_end = status is NormalizedSubscriptionStatus.CANCEL_REQUESTED
            legacy_status = {
                NormalizedSubscriptionStatus.PENDING: SubscriptionStatus.TRIALING,
                NormalizedSubscriptionStatus.ACTIVE: SubscriptionStatus.ACTIVE,
                NormalizedSubscriptionStatus.PAST_DUE: SubscriptionStatus.PAST_DUE,
                NormalizedSubscriptionStatus.CANCEL_REQUESTED: SubscriptionStatus.ACTIVE,
                NormalizedSubscriptionStatus.CANCELED: SubscriptionStatus.CANCELED,
                NormalizedSubscriptionStatus.EXPIRED: SubscriptionStatus.EXPIRED,
            }[status]
            subscription.status = legacy_status
        if status is NormalizedSubscriptionStatus.CANCEL_REQUESTED:
            intent = await db.scalar(
                select(SubscriptionCancelIntent)
                .where(SubscriptionCancelIntent.subscription_id == subscription.id)
                .order_by(SubscriptionCancelIntent.created_at.desc())
                .with_for_update()
            )
            if intent is not None and _value(intent.state) in {
                CancelIntentState.CALLING.value,
                CancelIntentState.UNKNOWN.value,
            }:
                intent.state = CancelIntentState.CONFIRMED
                intent.provider_evidence = {
                    "payment_event_id": str(event.id),
                    "event_id": event.event_id,
                    "raw_payload_sha256": event.raw_payload_sha256,
                }
                intent.stored_response = {
                    "subscription_id": str(subscription.id),
                    "state": "CONFIRMED",
                    "cancel_at_period_end": True,
                }
                intent.confirmed_at = event.occurred_at
                intent.last_error = None
        event.processing_state = PaymentEventProcessingState.APPLIED
        event.processed_at = datetime.now(timezone.utc)
        event.error = None
        await db.flush()
        return subscription

    @staticmethod
    def _cancel_provider_request_id(
        user_id: uuid.UUID,
        subscription_id: uuid.UUID,
        idempotency_key: str,
    ) -> str:
        digest = hashlib.sha256(
            f"{user_id}:{subscription_id}:{idempotency_key}:scheduled".encode("utf-8")
        ).hexdigest()
        return f"sc_{digest}"

    async def request_period_end_cancellation(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        subscription_id: uuid.UUID,
        idempotency_key: str,
    ) -> dict:
        clean_key = str(idempotency_key or "").strip()
        if not clean_key or len(clean_key) > 128:
            raise SubscriptionError(
                code="idempotency_key_required",
                message="A valid Idempotency-Key header is required.",
                status_code=400,
            )
        await lock_idempotency_scope(
            db,
            user_id=user_id,
            endpoint="subscriptions.cancel_at_period_end",
            key=clean_key,
        )
        subscription = await db.scalar(
            select(UserSubscription)
            .where(
                UserSubscription.id == subscription_id,
                UserSubscription.user_id == user_id,
            )
            .with_for_update()
        )
        if subscription is None:
            raise SubscriptionError(
                code="subscription_not_found",
                message="Subscription not found.",
                status_code=404,
            )
        request_hash = canonical_request_hash(
            {
                "subscription_id": str(subscription.id),
                "provider_subscription_id": subscription.provider_subscription_id,
                "mode": "scheduled",
                "onExecute": "cancel",
            }
        )
        existing = await db.scalar(
            select(SubscriptionCancelIntent)
            .where(
                SubscriptionCancelIntent.user_id == user_id,
                SubscriptionCancelIntent.idempotency_key == clean_key,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise SubscriptionError(
                    code="idempotency_payload_mismatch",
                    message="The Idempotency-Key was already used for another request.",
                    status_code=409,
                )
            return cancel_replay_or_raise(existing)
        if _value(subscription.normalized_status) not in {
            NormalizedSubscriptionStatus.ACTIVE.value,
            NormalizedSubscriptionStatus.PAST_DUE.value,
        }:
            raise SubscriptionError(
                code="subscription_not_cancelable",
                message="Subscription is not in a cancelable state.",
                status_code=409,
            )
        if not subscription.provider_subscription_id:
            raise SubscriptionError(
                code="provider_subscription_id_missing",
                message="Subscription requires reconciliation before cancellation.",
                status_code=409,
            )
        intent = SubscriptionCancelIntent(
            id=uuid.uuid4(),
            subscription_id=subscription.id,
            user_id=user_id,
            idempotency_key=clean_key,
            request_hash=request_hash,
            provider_request_id=self._cancel_provider_request_id(
                user_id,
                subscription.id,
                clean_key,
            ),
            state=CancelIntentState.CALLING,
            attempt_count=1,
            call_started_at=datetime.now(timezone.utc),
        )
        db.add(intent)
        await db.flush()
        await db.commit()
        try:
            response = await self._request(
                "POST",
                f"/v1/subscriptions/{subscription.provider_subscription_id}/cancel",
                json_body={"mode": "scheduled", "onExecute": "cancel"},
            )
        except httpx.RequestError as exc:
            intent.state = CancelIntentState.UNKNOWN
            intent.last_error = f"creem_cancel_ambiguous:{type(exc).__name__}"
            await db.flush()
            await db.commit()
            raise CancellationReconciliationPending(intent.id) from exc
        except httpx.HTTPStatusError as exc:
            intent.state = CancelIntentState.FAILED_RETRYABLE
            intent.last_error = f"creem_cancel_rejected:{exc.response.status_code}"
            await db.flush()
            await db.commit()
            raise CancellationReconciliationPending(intent.id) from exc
        response_id = str(response.get("id") or "").strip()
        response_status = str(response.get("status") or "").strip().lower()
        confirmed = response_status == "scheduled_cancel" or response.get("cancel_at_period_end") is True
        if response_id != subscription.provider_subscription_id or not confirmed:
            intent.state = CancelIntentState.UNKNOWN
            intent.last_error = "creem_cancel_response_unproven"
            await db.flush()
            await db.commit()
            raise CancellationReconciliationPending(intent.id)
        response_hash = hashlib.sha256(
            json.dumps(response, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        stored_response = {
            "subscription_id": str(subscription.id),
            "state": "CONFIRMED",
            "cancel_at_period_end": True,
        }
        intent.state = CancelIntentState.CONFIRMED
        intent.stored_response = stored_response
        intent.provider_evidence = {
            "provider_subscription_id": response_id,
            "provider_status": response_status,
            "response_sha256": response_hash,
        }
        intent.confirmed_at = datetime.now(timezone.utc)
        intent.last_error = None
        subscription.normalized_status = NormalizedSubscriptionStatus.CANCEL_REQUESTED
        subscription.cancel_at_period_end = True
        await db.flush()
        await db.commit()
        return stored_response

    async def cancel_current_subscription(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        *,
        idempotency_key: str,
    ) -> dict:
        subscription = await self.get_current_subscription(db, user_id)
        if subscription is None:
            raise SubscriptionError(
                code="subscription_not_found",
                message="Subscription not found.",
                status_code=404,
            )
        return await self.request_period_end_cancellation(
            db,
            user_id=user_id,
            subscription_id=subscription.id,
            idempotency_key=idempotency_key,
        )

    async def process_provider_event(
        self,
        db: AsyncSession,
        *,
        provider: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> UserSubscription | None:
        del db, provider, event_type, payload
        raise SubscriptionError(
            code="normalized_payment_event_required",
            message="Subscription events must pass through signed normalized ingestion.",
            status_code=409,
        )


subscription_service = SubscriptionService()
