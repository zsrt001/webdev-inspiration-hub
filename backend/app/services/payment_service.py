"""Authoritative credit-pack checkout and signed monetary fact application."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import uuid

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus, PurchaseIntentState
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.payment_event import (
    PaymentCaptureFact,
    PaymentDisputeFact,
    PaymentEvent,
    PaymentEventProcessingState,
    PaymentRefundFact,
)
from app.models.user import User
from app.models.user_credit import UserCredit
from app.schemas.payment import AcceptedPaymentEvent, CheckoutRedirect, CreditPackStatusResponse
from app.services.billing_catalog_service import (
    BillingCatalogUnavailable,
    CheckoutCatalogSelection,
    require_checkout_catalog_product,
)
from app.services.credit_reversal_service import CreditReversalError, reverse_root_grant
from app.services.creem_event_service import (
    CreemEventError,
    ingest_verified_creem_event,
    verify_creem_signature,
)
from app.services.idempotency_service import (
    IdempotencyConflict,
    begin_idempotent_request,
    canonical_request_hash,
    complete_idempotent_request,
)
from app.services.payment_reconciliation_service import (
    PaymentReconciliationRequired,
    classify_monetary_reversal,
    freeze_purchase_and_open_case,
    open_payment_reconciliation_case,
    unfreeze_purchase_lineage,
)


settings = get_settings()
logger = logging.getLogger(__name__)


class PaymentError(RuntimeError):
    def __init__(self, *, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(code)


class CheckoutReconciliationPending(PaymentError):
    def __init__(self, purchase_id: uuid.UUID | None = None):
        self.purchase_id = purchase_id
        super().__init__(
            code="checkout_reconciliation_pending",
            message="Checkout status requires reconciliation before another Provider call.",
            status_code=409,
        )


class CheckoutStatusUnknown(CheckoutReconciliationPending):
    def __init__(self, purchase_id: uuid.UUID):
        super().__init__(purchase_id)
        self.code = "checkout_status_unknown"
        self.message = "The Provider call may have succeeded; status will be reconciled."


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def checkout_replay_or_raise(purchase: CreditPurchase) -> dict:
    state = _enum_value(purchase.intent_state)
    if state in {PurchaseIntentState.READY.value, PurchaseIntentState.CONFIRMED.value}:
        stored = purchase.stored_response
        if not isinstance(stored, dict) or not str(stored.get("checkout_url") or "").strip():
            raise CheckoutReconciliationPending(purchase.id)
        return dict(stored)
    if state in {
        PurchaseIntentState.CALLING.value,
        PurchaseIntentState.UNKNOWN.value,
        PurchaseIntentState.FAILED_RETRYABLE.value,
    }:
        raise CheckoutReconciliationPending(purchase.id)
    raise PaymentError(
        code="checkout_intent_not_ready",
        message="Checkout intent is not ready for replay.",
        status_code=409,
    )


def derive_purchase_state(purchase: CreditPurchase) -> str:
    captured = int(purchase.captured_minor_units or 0)
    refunded = int(purchase.refunded_minor_units or 0)
    dispute_state = str(purchase.dispute_state or "NONE").upper()
    if captured and 0 < refunded < captured:
        return "PARTIAL_RECONCILIATION_REQUIRED"
    if dispute_state in {"OPEN", "REVIEW", "PARTIAL"}:
        return "DISPUTED"
    if captured and (refunded == captured or dispute_state == "LOST"):
        return "REVERSED"
    if captured and purchase.grant_transaction_id is not None:
        return "PAID"
    intent_state = _enum_value(purchase.intent_state)
    if intent_state in {PurchaseIntentState.UNKNOWN.value, PurchaseIntentState.FAILED_RETRYABLE.value}:
        return "UNKNOWN"
    return "PENDING"


class PaymentService:
    _ONE_TIME_EVENTS = frozenset({"checkout.completed", "refund.created"})
    _DISPUTE_EVENTS = frozenset({"dispute.created", "dispute.updated", "dispute.closed"})

    def _provider(self) -> str:
        return "creem"

    def _api_base_url(self) -> str:
        return (settings.creem_api_base_url or "https://api.creem.io").rstrip("/")

    def _headers(self) -> dict[str, str]:
        api_key = str(settings.creem_api_key or "").strip()
        if not api_key:
            raise PaymentError(
                code="creem_not_configured",
                message="Payment provider is not configured.",
                status_code=503,
            )
        return {"x-api-key": api_key, "Content-Type": "application/json"}

    def _allowed_return_hosts(self) -> set[str]:
        hosts = {"localhost", "127.0.0.1"}
        for raw in (settings.effective_frontend_base_url, settings.cors_allow_origins):
            for item in str(raw or "").split(","):
                parsed = urlparse(item.strip())
                if parsed.hostname:
                    hosts.add(parsed.hostname.lower())
        return hosts

    def _default_return_url(self) -> str:
        return settings.effective_frontend_base_url.rstrip("/")

    def _safe_return_url(self, return_url: str | None) -> str:
        candidate = str(return_url or "").strip()
        if not candidate:
            return self._default_return_url()
        parsed = urlparse(candidate)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.hostname.lower() not in self._allowed_return_hosts()
        ):
            return self._default_return_url()
        if parsed.scheme != "https":
            default = self._default_return_url()
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

    def _extract_checkout_dict(self, payload: Any, checkout_id: str | None = None) -> dict[str, Any]:
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                if checkout_id is None or str(item.get("id") or item.get("checkout_id")) == checkout_id:
                    return item
            return {}
        if not isinstance(payload, dict):
            return {}
        for key in ("checkout", "result"):
            child = payload.get(key)
            if isinstance(child, dict):
                return child
        data = payload.get("data")
        if isinstance(data, (dict, list)):
            extracted = self._extract_checkout_dict(data, checkout_id)
            if extracted:
                return extracted
        return payload

    @staticmethod
    def _extract_checkout_url(payload: dict[str, Any]) -> str | None:
        for key in ("checkout_url", "url", "hosted_checkout_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _extract_checkout_id(payload: dict[str, Any]) -> str | None:
        for key in ("id", "checkout_id"):
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return None

    @staticmethod
    def _extract_product_id(payload: dict[str, Any]) -> str | None:
        value = payload.get("product_id") or payload.get("product")
        if isinstance(value, dict):
            value = value.get("id")
        if value is None:
            order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
            value = order.get("product")
            if isinstance(value, dict):
                value = value.get("id")
        return str(value).strip() if value is not None and str(value).strip() else None

    @staticmethod
    def _extract_payment_id(payload: dict[str, Any]) -> str | None:
        order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
        value = payload.get("payment_id") or order.get("transaction") or order.get("id")
        return str(value).strip() if value is not None and str(value).strip() else None

    @staticmethod
    def _normalize_status(payload: dict[str, Any]) -> str:
        order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
        return str(order.get("status") or payload.get("status") or "").strip().lower()

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
            result = response.json()
            if not isinstance(result, dict):
                raise PaymentError(
                    code="payment_provider_response_invalid",
                    message="Payment provider returned an invalid response.",
                    status_code=503,
                )
            return result

    @staticmethod
    def _provider_request_id(user_id: uuid.UUID, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"{user_id}:{idempotency_key}".encode("utf-8")).hexdigest()
        return f"cp_{digest}"

    @staticmethod
    def _selection_request_hash(
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

    def _validate_checkout_response(
        self,
        payload: dict[str, Any],
        *,
        purchase: CreditPurchase,
    ) -> tuple[str, str]:
        snapshot = dict(purchase.catalog_snapshot or {})
        checkout_id = self._extract_checkout_id(payload)
        checkout_url = self._extract_checkout_url(payload)
        returned_request_id = str(payload.get("request_id") or "").strip()
        returned_product_id = self._extract_product_id(payload)
        expected_product_id = str(snapshot.get("provider_product_id") or "").strip()
        if not checkout_id or returned_request_id != purchase.provider_request_id:
            raise PaymentError(
                code="payment_checkout_identity_mismatch",
                message="Payment checkout identity did not match the local intent.",
                status_code=503,
            )
        if not expected_product_id or returned_product_id != expected_product_id:
            raise PaymentError(
                code="payment_checkout_product_mismatch",
                message="Payment checkout product did not match the active catalog.",
                status_code=503,
            )
        if not checkout_url:
            raise PaymentError(
                code="payment_checkout_url_missing",
                message="Payment provider did not return a checkout URL.",
                status_code=503,
            )
        parsed_url = urlparse(checkout_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password:
            raise PaymentError(
                code="payment_checkout_url_invalid",
                message="Payment provider returned an unsafe checkout URL.",
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
            raise PaymentError(
                code="payment_checkout_amount_mismatch",
                message="Payment checkout amount did not match the active catalog.",
                status_code=503,
            )
        if returned_currency is not None and str(returned_currency).upper() != str(snapshot.get("currency") or ""):
            raise PaymentError(
                code="payment_checkout_currency_mismatch",
                message="Payment checkout currency did not match the active catalog.",
                status_code=503,
            )
        return checkout_id, checkout_url

    async def create_credit_pack_checkout(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        product_code: str,
        idempotency_key: str,
        return_url: str | None,
    ) -> CheckoutRedirect:
        clean_key = str(idempotency_key or "").strip()
        if not clean_key or len(clean_key) > 128:
            raise PaymentError(
                code="idempotency_key_required",
                message="A valid Idempotency-Key header is required.",
                status_code=400,
            )
        safe_return_url = self._safe_return_url(return_url)
        try:
            selection = await require_checkout_catalog_product(
                db,
                product_code=product_code,
                provider="creem",
            )
        except BillingCatalogUnavailable as exc:
            raise PaymentError(
                code="credit_catalog_unavailable",
                message="The active billing catalog is unavailable.",
                status_code=503,
            ) from exc
        request_hash = self._selection_request_hash(
            selection,
            user_id=user_id,
            return_url=safe_return_url,
        )
        try:
            idempotency = await begin_idempotent_request(
                db,
                user_id=user_id,
                endpoint="payments.credit_pack.checkout",
                key=clean_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as exc:
            raise PaymentError(
                code="idempotency_payload_mismatch",
                message="The Idempotency-Key was already used for another checkout request.",
                status_code=409,
            ) from exc
        provider_request_id = self._provider_request_id(user_id, clean_key)
        purchase = await db.scalar(
            select(CreditPurchase)
            .where(CreditPurchase.provider_request_id == provider_request_id)
            .with_for_update()
        )
        if purchase is not None:
            if purchase.user_id != user_id or purchase.request_hash != request_hash:
                raise PaymentError(
                    code="idempotency_payload_mismatch",
                    message="The Idempotency-Key was already used for another checkout request.",
                    status_code=409,
                )
            stored = checkout_replay_or_raise(purchase)
            return CheckoutRedirect.model_validate(stored)

        now = datetime.now(timezone.utc)
        snapshot = selection.as_snapshot()
        internal_metadata_id = uuid.uuid4()
        purchase = CreditPurchase(
            id=uuid.uuid4(),
            user_id=user_id,
            provider="creem",
            package_id=selection.product.product_code,
            credits=selection.product.credits,
            price_cents=selection.product.pre_tax_minor_units,
            currency=selection.product.currency,
            status=CreditPurchaseStatus.PENDING,
            provider_request_id=provider_request_id,
            intent_state=PurchaseIntentState.CALLING,
            request_hash=request_hash,
            catalog_version_id=selection.catalog_version_id,
            catalog_snapshot=snapshot,
            internal_metadata_id=internal_metadata_id,
            captured_minor_units=0,
            tax_minor_units=0,
            refunded_minor_units=0,
            disputed_minor_units=0,
            dispute_state="NONE",
            call_started_at=now,
            metadata_json={"return_url": safe_return_url},
        )
        db.add(purchase)
        await db.flush()
        await db.commit()  # durable CALLING boundary before external I/O

        success_url = self._append_query(
            safe_return_url,
            payment="return",
            purchase_id=str(purchase.id),
        )
        provider_request = {
            "product_id": selection.product.provider_product_id,
            "request_id": provider_request_id,
            "units": 1,
            "success_url": success_url,
            "metadata": {"vowpic_purchase_ref": str(internal_metadata_id)},
        }
        try:
            response_data = await self._request("POST", "/v1/checkouts", json_body=provider_request)
        except httpx.RequestError as exc:
            purchase.intent_state = PurchaseIntentState.UNKNOWN
            purchase.last_error = f"creem_checkout_ambiguous:{type(exc).__name__}"
            await db.flush()
            await db.commit()
            raise CheckoutStatusUnknown(purchase.id) from exc
        except httpx.HTTPStatusError as exc:
            purchase.intent_state = PurchaseIntentState.FAILED_RETRYABLE
            purchase.last_error = f"creem_checkout_rejected:{exc.response.status_code}"
            await db.flush()
            await db.commit()
            raise PaymentError(
                code="payment_provider_rejected_checkout",
                message="Payment provider rejected the checkout request.",
                status_code=503,
            ) from exc

        checkout_payload = self._extract_checkout_dict(response_data)
        try:
            checkout_id, checkout_url = self._validate_checkout_response(
                checkout_payload,
                purchase=purchase,
            )
        except PaymentError:
            purchase.intent_state = PurchaseIntentState.UNKNOWN
            purchase.last_error = "creem_checkout_response_validation_failed"
            await db.flush()
            await db.commit()
            raise
        purchase.provider_checkout_id = checkout_id
        purchase.checkout_url = checkout_url
        purchase.intent_state = PurchaseIntentState.READY
        purchase.ready_at = datetime.now(timezone.utc)
        purchase.last_error = None
        stored_response = {
            "purchase_id": str(purchase.id),
            "provider": "creem",
            "status": "READY",
            "checkout_url": checkout_url,
        }
        purchase.stored_response = stored_response
        await db.flush()
        await complete_idempotent_request(
            db,
            record_id=idempotency.record_id,
            response_status=201,
            response_json=stored_response,
        )
        await db.commit()
        return CheckoutRedirect.model_validate(stored_response)

    async def create_checkout(
        self,
        db: AsyncSession,
        *,
        user: User,
        product_code: str | None = None,
        package_id: str | None = None,
        idempotency_key: str,
        return_url: str | None,
    ) -> CheckoutRedirect:
        return await self.create_credit_pack_checkout(
            db,
            user_id=user.id,
            product_code=str(product_code or package_id or ""),
            idempotency_key=idempotency_key,
            return_url=return_url,
        )

    async def _get_purchase_for_user(
        self,
        db: AsyncSession,
        *,
        purchase_id: str,
        user_id: uuid.UUID,
    ) -> CreditPurchase:
        try:
            parsed_id = uuid.UUID(str(purchase_id))
        except ValueError as exc:
            raise PaymentError(
                code="invalid_purchase_id",
                message="Invalid purchase ID.",
                status_code=400,
            ) from exc
        purchase = await db.scalar(
            select(CreditPurchase).where(CreditPurchase.id == parsed_id)
        )
        if purchase is None or purchase.user_id != user_id:
            raise PaymentError(
                code="purchase_not_found",
                message="Purchase not found.",
                status_code=404,
            )
        return purchase

    async def get_purchase_status(
        self,
        db: AsyncSession,
        *,
        purchase_id: str,
        user_id: uuid.UUID,
        checkout_id: str | None = None,
    ) -> CreditPackStatusResponse:
        del checkout_id  # redirect/provider query values are never accounting authority
        purchase = await self._get_purchase_for_user(
            db,
            purchase_id=purchase_id,
            user_id=user_id,
        )
        credit = await db.scalar(select(UserCredit).where(UserCredit.user_id == user_id))
        accounting_balance = int(credit.balance or 0) if credit is not None else 0
        reserved = int(credit.reserved_balance or 0) if credit is not None else 0
        return CreditPackStatusResponse(
            purchase_id=purchase.id,
            provider="creem",
            product_code=purchase.package_id,
            state=derive_purchase_state(purchase),
            checkout_url=purchase.checkout_url,
            captured_minor_units=int(purchase.captured_minor_units or 0),
            tax_minor_units=int(purchase.tax_minor_units or 0),
            refunded_minor_units=int(purchase.refunded_minor_units or 0),
            disputed_minor_units=int(purchase.disputed_minor_units or 0),
            currency=str(purchase.currency or "USD").upper(),
            credits_granted=int(purchase.credits if purchase.grant_transaction_id is not None else 0),
            accounting_balance=accounting_balance,
            spendable_balance=max(0, accounting_balance - reserved),
        )

    @staticmethod
    def _retention_expiry(retention_tier: str, occurred_at: datetime) -> datetime:
        days_by_tier = {"paid_90d": 90, "subscription_180d": 180, "studio_365d": 365}
        days = days_by_tier.get(str(retention_tier))
        if days is None:
            raise PaymentReconciliationRequired("purchase_retention_tier_invalid")
        return occurred_at + timedelta(days=days)

    async def _mark_event_reconciliation(
        self,
        db: AsyncSession,
        *,
        event: PaymentEvent,
        purchase: CreditPurchase | None,
        reason_code: str,
    ) -> None:
        await open_payment_reconciliation_case(
            db,
            purchase=purchase,
            case_key=f"event:{event.event_id}",
            subject_type="credit_purchase" if purchase is not None else "payment_event",
            subject_id=str(purchase.id if purchase is not None else event.id),
            reason_code=reason_code,
            raw_payload_sha256=str(event.raw_payload_sha256),
        )
        event.processing_state = PaymentEventProcessingState.RECONCILIATION_REQUIRED
        event.error = reason_code

    async def _find_checkout_purchase(
        self,
        db: AsyncSession,
        event: PaymentEvent,
    ) -> CreditPurchase | None:
        metadata = dict(event.business_metadata or {})
        internal_ref: uuid.UUID | None = None
        try:
            if metadata.get("vowpic_purchase_ref"):
                internal_ref = uuid.UUID(str(metadata["vowpic_purchase_ref"]))
        except ValueError:
            internal_ref = None
        conditions = []
        if event.request_id:
            conditions.append(CreditPurchase.provider_request_id == event.request_id)
        if internal_ref is not None:
            conditions.append(CreditPurchase.internal_metadata_id == internal_ref)
        provider_checkout_id = str(metadata.get("provider_checkout_id") or "").strip()
        if provider_checkout_id:
            conditions.append(CreditPurchase.provider_checkout_id == provider_checkout_id)
        if not conditions:
            return None
        rows = list(
            (
                await db.scalars(
                    select(CreditPurchase).where(or_(*conditions)).with_for_update()
                )
            ).all()
        )
        unique = {row.id: row for row in rows}
        return next(iter(unique.values())) if len(unique) == 1 else None

    async def _apply_checkout_capture(self, db: AsyncSession, event: PaymentEvent) -> CreditPurchase | None:
        purchase = await self._find_checkout_purchase(db, event)
        if purchase is None:
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=None,
                reason_code="checkout_purchase_not_found",
            )
            return None
        snapshot = dict(purchase.catalog_snapshot or {})
        metadata = dict(event.business_metadata or {})
        provider_product_id = str(metadata.get("provider_product_id") or "")
        provider_payment_id = str(metadata.get("provider_payment_id") or "")
        expected_status = str(event.normalized_status or "").lower()
        if expected_status not in {"paid", "completed", "succeeded"}:
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=purchase,
                reason_code="checkout_status_not_paid",
            )
            return purchase
        if (
            not provider_payment_id
            or provider_product_id != str(snapshot.get("provider_product_id") or "")
            or event.pre_tax_minor_units is None
            or int(event.pre_tax_minor_units) != int(snapshot.get("pre_tax_minor_units") or -1)
            or event.tax_minor_units is None
            or event.currency != str(snapshot.get("currency") or "")
        ):
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=purchase,
                reason_code="checkout_money_or_catalog_mismatch",
            )
            return purchase
        existing_capture = await db.scalar(
            select(PaymentCaptureFact).where(
                or_(
                    PaymentCaptureFact.purchase_id == purchase.id,
                    PaymentCaptureFact.provider_payment_id == provider_payment_id,
                )
            )
        )
        if existing_capture is not None:
            if (
                existing_capture.purchase_id != purchase.id
                or int(existing_capture.pre_tax_minor_units) != int(event.pre_tax_minor_units)
                or int(existing_capture.tax_minor_units) != int(event.tax_minor_units)
                or existing_capture.currency != event.currency
            ):
                await self._mark_event_reconciliation(
                    db,
                    event=event,
                    purchase=purchase,
                    reason_code="capture_fact_conflict",
                )
                return purchase
            event.processing_state = PaymentEventProcessingState.APPLIED
            event.processed_at = datetime.now(timezone.utc)
            event.error = None
            return purchase

        credit = await db.scalar(
            select(UserCredit).where(UserCredit.user_id == purchase.user_id).with_for_update()
        )
        if credit is None:
            credit = UserCredit(
                id=uuid.uuid4(),
                user_id=purchase.user_id,
                balance=0,
                reserved_balance=0,
            )
            db.add(credit)
            await db.flush()
        prior_balance = int(credit.balance or 0)
        next_balance = prior_balance + int(purchase.credits)
        debt_offset = min(int(purchase.credits), max(0, -prior_balance))
        transaction_id = uuid.uuid4()
        lot_id = uuid.uuid4()
        retention_tier = str(snapshot.get("retention_tier") or "")
        root = CreditTransaction(
            id=transaction_id,
            user_id=purchase.user_id,
            transaction_type=CreditTransactionType.PURCHASE,
            amount=int(purchase.credits),
            balance_after=next_balance,
            source="credit_purchase",
            source_id=str(purchase.id),
            description=f"Verified Creem credit pack: {purchase.package_id}",
            metadata_json={
                "provider": "creem",
                "provider_payment_id": provider_payment_id,
                "payment_event_id": str(event.id),
            },
            root_transaction_id=transaction_id,
            request_id=f"creem-capture:{provider_payment_id}",
        )
        lot = CreditGrantLot(
            id=lot_id,
            user_id=purchase.user_id,
            root_transaction_id=transaction_id,
            source_type=GrantLotSourceType.PURCHASE,
            source_id=str(purchase.id),
            original_amount=int(purchase.credits),
            debt_offset_amount=debt_offset,
            reversed_amount=0,
            frozen_amount=0,
            consumed_amount=0,
            retention_tier=retention_tier,
            expires_at=self._retention_expiry(retention_tier, event.occurred_at),
        )
        capture = PaymentCaptureFact(
            id=uuid.uuid4(),
            purchase_id=purchase.id,
            payment_event_id=event.id,
            provider="creem",
            provider_payment_id=provider_payment_id,
            pre_tax_minor_units=int(event.pre_tax_minor_units),
            tax_minor_units=int(event.tax_minor_units),
            currency=str(event.currency),
            grant_transaction_id=transaction_id,
            grant_lot_id=lot_id,
            occurred_at=event.occurred_at,
        )
        db.add(root)
        db.add(lot)
        db.add(capture)
        credit.balance = next_balance
        purchase.provider_payment_id = provider_payment_id
        purchase.provider_checkout_id = str(metadata.get("provider_checkout_id") or purchase.provider_checkout_id or "") or None
        purchase.captured_minor_units = int(event.pre_tax_minor_units) + int(event.tax_minor_units)
        purchase.tax_minor_units = int(event.tax_minor_units)
        purchase.grant_transaction_id = transaction_id
        purchase.grant_lot_id = lot_id
        purchase.intent_state = PurchaseIntentState.CONFIRMED
        purchase.status = CreditPurchaseStatus.PAID
        purchase.confirmed_at = event.occurred_at
        purchase.completed_at = event.occurred_at
        purchase.webhook_event_id = event.event_id
        if purchase.stored_response:
            purchase.stored_response = {**purchase.stored_response, "status": "CONFIRMED"}
        event.processing_state = PaymentEventProcessingState.APPLIED
        event.processed_at = datetime.now(timezone.utc)
        event.error = None
        await db.flush()
        return purchase

    async def _capture_for_event(self, db: AsyncSession, event: PaymentEvent) -> PaymentCaptureFact | None:
        provider_payment_id = str((event.business_metadata or {}).get("provider_payment_id") or "")
        if not provider_payment_id:
            return None
        return await db.scalar(
            select(PaymentCaptureFact).where(
                PaymentCaptureFact.provider == "creem",
                PaymentCaptureFact.provider_payment_id == provider_payment_id,
            )
        )

    async def _apply_refund(self, db: AsyncSession, event: PaymentEvent) -> CreditPurchase | None:
        capture = await self._capture_for_event(db, event)
        if capture is None:
            from app.services.subscription_service import subscription_service

            if await subscription_service.apply_subscription_adjustment_event(db, event=event):
                return None
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=None,
                reason_code="refund_capture_not_found",
            )
            return None
        purchase = await db.scalar(
            select(CreditPurchase).where(CreditPurchase.id == capture.purchase_id).with_for_update()
        )
        if purchase is None:
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=None,
                reason_code="refund_purchase_not_found",
            )
            return None
        metadata = dict(event.business_metadata or {})
        provider_refund_id = str(metadata.get("provider_refund_id") or event.object_id or "")
        try:
            amount = int(str(metadata.get("event_minor_units") or ""))
        except ValueError:
            amount = 0
        if amount <= 0 or event.currency != purchase.currency:
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=purchase,
                reason_code="refund_money_invalid",
            )
            return purchase
        existing = await db.scalar(
            select(PaymentRefundFact).where(PaymentRefundFact.payment_event_id == event.id)
        )
        if existing is not None:
            event.processing_state = PaymentEventProcessingState.APPLIED
            event.processed_at = datetime.now(timezone.utc)
            event.error = None
            return purchase
        try:
            classification = classify_monetary_reversal(
                captured_minor_units=int(purchase.captured_minor_units or 0),
                already_refunded_minor_units=int(purchase.refunded_minor_units or 0),
                event_minor_units=amount,
            )
        except PaymentReconciliationRequired as exc:
            classification = (
                "OVER_CAPTURE"
                if exc.code == "monetary_reversal_exceeds_capture"
                else "PARTIAL_RECONCILIATION_REQUIRED"
            )
            db.add(
                PaymentRefundFact(
                    id=uuid.uuid4(),
                    purchase_id=purchase.id,
                    payment_event_id=event.id,
                    provider="creem",
                    provider_refund_id=provider_refund_id,
                    refund_minor_units=amount,
                    currency=str(event.currency),
                    classification=classification,
                    occurred_at=event.occurred_at,
                )
            )
            if amount + int(purchase.refunded_minor_units or 0) <= int(purchase.captured_minor_units or 0):
                purchase.refunded_minor_units = int(purchase.refunded_minor_units or 0) + amount
            await freeze_purchase_and_open_case(
                db,
                purchase=purchase,
                case_key=f"refund:{provider_refund_id}",
                reason_code=exc.code,
                raw_payload_sha256=str(event.raw_payload_sha256),
            )
            event.processing_state = PaymentEventProcessingState.RECONCILIATION_REQUIRED
            event.error = exc.code
            await db.flush()
            return purchase

        if classification != "FULL" or purchase.grant_transaction_id is None:
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=purchase,
                reason_code="refund_root_lineage_missing",
            )
            return purchase
        try:
            reversal = await reverse_root_grant(
                db,
                user_id=purchase.user_id,
                root_transaction_id=purchase.grant_transaction_id,
                amount=int(purchase.credits),
                request_id=f"creem-refund:{provider_refund_id}",
                reason_code="provider_full_refund",
                transaction_type=CreditTransactionType.PURCHASE_REVERSAL,
                now=event.occurred_at,
            )
        except CreditReversalError as exc:
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=purchase,
                reason_code=exc.code,
            )
            return purchase
        db.add(
            PaymentRefundFact(
                id=uuid.uuid4(),
                purchase_id=purchase.id,
                payment_event_id=event.id,
                provider="creem",
                provider_refund_id=provider_refund_id,
                refund_minor_units=amount,
                currency=str(event.currency),
                classification="FULL",
                reversal_transaction_id=reversal.transaction.id,
                occurred_at=event.occurred_at,
            )
        )
        purchase.refunded_minor_units = amount
        purchase.status = CreditPurchaseStatus.REFUNDED
        event.processing_state = PaymentEventProcessingState.APPLIED
        event.processed_at = datetime.now(timezone.utc)
        event.error = None
        await db.flush()
        return purchase

    @staticmethod
    def _dispute_outcome(event: PaymentEvent) -> str:
        metadata = dict(event.business_metadata or {})
        explicit = str(metadata.get("dispute_outcome") or "").strip().upper()
        status = str(event.normalized_status or "").strip().upper()
        event_type = str(event.event_type).lower()
        if explicit in {"WON", "LOST", "OPEN"}:
            return explicit
        if status in {"WON", "CLOSED_WON", "RESOLVED_WON"}:
            return "WON"
        if status in {"LOST", "CHARGEBACK", "CLOSED_LOST"}:
            return "LOST"
        if event_type == "dispute.created":
            return "OPEN"
        return "REVIEW"

    async def _apply_dispute(self, db: AsyncSession, event: PaymentEvent) -> CreditPurchase | None:
        capture = await self._capture_for_event(db, event)
        if capture is None:
            from app.services.subscription_service import subscription_service

            if await subscription_service.apply_subscription_adjustment_event(db, event=event):
                return None
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=None,
                reason_code="dispute_capture_not_found",
            )
            return None
        purchase = await db.scalar(
            select(CreditPurchase).where(CreditPurchase.id == capture.purchase_id).with_for_update()
        )
        if purchase is None:
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=None,
                reason_code="dispute_purchase_not_found",
            )
            return None
        metadata = dict(event.business_metadata or {})
        provider_dispute_id = str(metadata.get("provider_dispute_id") or event.object_id or "")
        try:
            amount = int(str(metadata.get("event_minor_units") or ""))
        except ValueError:
            amount = 0
        outcome = self._dispute_outcome(event)
        if amount <= 0 or event.currency != purchase.currency:
            await self._mark_event_reconciliation(
                db,
                event=event,
                purchase=purchase,
                reason_code="dispute_money_invalid",
            )
            return purchase
        existing = await db.scalar(
            select(PaymentDisputeFact).where(PaymentDisputeFact.payment_event_id == event.id)
        )
        if existing is not None:
            event.processing_state = PaymentEventProcessingState.APPLIED
            event.processed_at = datetime.now(timezone.utc)
            event.error = None
            return purchase

        reversal_transaction_id: uuid.UUID | None = None
        if amount > int(purchase.captured_minor_units or 0) or outcome == "REVIEW":
            await freeze_purchase_and_open_case(
                db,
                purchase=purchase,
                case_key=f"dispute:{provider_dispute_id}:{outcome}",
                reason_code="dispute_amount_or_outcome_reconciliation",
                raw_payload_sha256=str(event.raw_payload_sha256),
            )
            event.processing_state = PaymentEventProcessingState.RECONCILIATION_REQUIRED
            event.error = "dispute_amount_or_outcome_reconciliation"
        elif outcome == "OPEN":
            await freeze_purchase_and_open_case(
                db,
                purchase=purchase,
                case_key=f"dispute:{provider_dispute_id}:open",
                reason_code="provider_dispute_open",
                raw_payload_sha256=str(event.raw_payload_sha256),
            )
            purchase.dispute_state = "OPEN"
            purchase.disputed_minor_units = max(int(purchase.disputed_minor_units or 0), amount)
            event.processing_state = PaymentEventProcessingState.APPLIED
            event.processed_at = datetime.now(timezone.utc)
            event.error = None
        elif outcome == "WON":
            if purchase.grant_lot_id is not None:
                lot = await db.scalar(
                    select(CreditGrantLot)
                    .where(CreditGrantLot.id == purchase.grant_lot_id)
                    .with_for_update()
                )
                if lot is None:
                    await self._mark_event_reconciliation(
                        db,
                        event=event,
                        purchase=purchase,
                        reason_code="dispute_grant_lot_missing",
                    )
                    return purchase
                unfreeze_purchase_lineage(lot)
            purchase.dispute_state = "WON"
            purchase.disputed_minor_units = max(int(purchase.disputed_minor_units or 0), amount)
            event.processing_state = PaymentEventProcessingState.APPLIED
            event.processed_at = datetime.now(timezone.utc)
            event.error = None
        elif outcome == "LOST":
            if amount != int(purchase.captured_minor_units or 0) or purchase.grant_transaction_id is None:
                await freeze_purchase_and_open_case(
                    db,
                    purchase=purchase,
                    case_key=f"dispute:{provider_dispute_id}:lost",
                    reason_code="partial_dispute_reconciliation_required",
                    raw_payload_sha256=str(event.raw_payload_sha256),
                )
                event.processing_state = PaymentEventProcessingState.RECONCILIATION_REQUIRED
                event.error = "partial_dispute_reconciliation_required"
            else:
                try:
                    reversal = await reverse_root_grant(
                        db,
                        user_id=purchase.user_id,
                        root_transaction_id=purchase.grant_transaction_id,
                        amount=int(purchase.credits),
                        request_id=f"creem-dispute:{provider_dispute_id}:lost",
                        reason_code="provider_dispute_lost",
                        transaction_type=CreditTransactionType.DISPUTE_REVERSAL,
                        now=event.occurred_at,
                    )
                except CreditReversalError as exc:
                    await self._mark_event_reconciliation(
                        db,
                        event=event,
                        purchase=purchase,
                        reason_code=exc.code,
                    )
                    return purchase
                reversal_transaction_id = reversal.transaction.id
                purchase.dispute_state = "LOST"
                purchase.disputed_minor_units = amount
                event.processing_state = PaymentEventProcessingState.APPLIED
                event.processed_at = datetime.now(timezone.utc)
                event.error = None
        db.add(
            PaymentDisputeFact(
                id=uuid.uuid4(),
                purchase_id=purchase.id,
                payment_event_id=event.id,
                provider="creem",
                provider_dispute_id=provider_dispute_id,
                disputed_minor_units=amount,
                currency=str(event.currency),
                outcome=outcome,
                reversal_transaction_id=reversal_transaction_id,
                occurred_at=event.occurred_at,
            )
        )
        await db.flush()
        return purchase

    async def apply_payment_event(
        self,
        db: AsyncSession,
        *,
        payment_event_id: uuid.UUID,
    ) -> CreditPurchase | None:
        event = await db.scalar(
            select(PaymentEvent).where(PaymentEvent.id == payment_event_id).with_for_update()
        )
        if event is None:
            raise PaymentError(
                code="payment_event_not_found",
                message="Payment event was not found.",
                status_code=404,
            )
        state = _enum_value(event.processing_state)
        if state in {
            PaymentEventProcessingState.APPLIED.value,
            PaymentEventProcessingState.UNHANDLED.value,
            PaymentEventProcessingState.RECONCILIATION_REQUIRED.value,
        }:
            return None
        event_type = str(event.event_type).lower()
        if event_type == "checkout.completed":
            from app.services.subscription_service import subscription_service

            if await subscription_service.apply_checkout_completed_event(
                db,
                event=event,
            ):
                return None
            return await self._apply_checkout_capture(db, event)
        if event_type == "refund.created":
            return await self._apply_refund(db, event)
        if event_type in self._DISPUTE_EVENTS:
            return await self._apply_dispute(db, event)
        if event_type.startswith("subscription."):
            from app.services.subscription_service import subscription_service

            await subscription_service.apply_normalized_payment_event(db, event=event)
            return None
        event.processing_state = PaymentEventProcessingState.UNHANDLED
        event.processed_at = datetime.now(timezone.utc)
        event.error = "event_type_unhandled"
        await db.flush()
        return None

    def verify_webhook_signature(self, body: bytes, signature_header: str | None) -> bool:
        try:
            verify_creem_signature(
                body,
                signature_header,
                str(settings.creem_webhook_secret or "").encode("utf-8"),
            )
        except CreemEventError:
            return False
        return True

    async def process_webhook_event(
        self,
        db: AsyncSession,
        *,
        payload: dict[str, Any] | None = None,
        body: bytes,
        signature_header: str | None,
    ) -> AcceptedPaymentEvent:
        """Persist, apply, and acknowledge one signed Creem event idempotently."""

        del payload
        try:
            accepted = await ingest_verified_creem_event(
                db,
                raw_body=body,
                signature=signature_header,
                webhook_secret=str(settings.creem_webhook_secret or "").encode("utf-8"),
            )
        except CreemEventError as exc:
            raise PaymentError(
                code=exc.code,
                message="Payment webhook was rejected.",
                status_code=exc.status_code,
            ) from exc
        event = await db.scalar(
            select(PaymentEvent).where(
                PaymentEvent.provider == "creem",
                PaymentEvent.event_id == accepted.event_id,
            )
        )
        if event is None:
            raise PaymentError(
                code="payment_event_not_found",
                message="Payment event was not found after ingestion.",
                status_code=500,
            )
        try:
            await self.apply_payment_event(db, payment_event_id=event.id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return accepted

    async def request_refund_review(self) -> None:
        """Preserve the legacy route without pretending Creem exposes a refund API."""
        raise PaymentError(
            code="refund_requires_support",
            message="Refunds require support review and confirmation through Creem.",
            status_code=409,
        )


payment_service = PaymentService()
