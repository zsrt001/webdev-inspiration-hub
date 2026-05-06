"""Hosted payment orchestration for credit top-up."""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus
from app.models.credit_transaction import CreditTransactionType
from app.models.payment_event import PaymentEvent
from app.models.user import User
from app.services.credit_service import add_credits_async, get_balance_async, get_package_by_id
from app.services.subscription_service import subscription_service

settings = get_settings()
logger = logging.getLogger(__name__)


class PaymentError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class PaymentService:
    _ONE_TIME_WEBHOOK_EVENTS = {"checkout.completed", "checkout.succeeded", "checkout.paid", "payment.completed"}
    _SUBSCRIPTION_WEBHOOK_EVENTS = {
        "invoice.paid",
        "subscription.active",
        "subscription.created",
        "subscription.update",
        "subscription.updated",
        "subscription.paid",
        "subscription.payment_succeeded",
        "subscription.canceled",
        "subscription.cancelled",
        "subscription.scheduled_cancel",
        "subscription.past_due",
        "subscription.expired",
        "subscription.paused",
        "subscription.trialing",
        "subscription.unpaid",
        "refund.created",
        "dispute.created",
    }
    _COMPLETED_STATES = {"completed", "paid", "succeeded", "success"}
    _FAILED_STATES = {"failed"}
    _EXPIRED_STATES = {"expired"}
    _CANCELED_STATES = {"cancelled", "canceled"}

    def _status_value(self, status: CreditPurchaseStatus | str | None) -> str:
        value = status.value if hasattr(status, "value") else status
        normalized = str(value or "").strip().lower()
        aliases = {
            "created": CreditPurchaseStatus.PENDING.value,
            "pending": CreditPurchaseStatus.PENDING.value,
            "completed": CreditPurchaseStatus.PAID.value,
            "paid": CreditPurchaseStatus.PAID.value,
            "succeeded": CreditPurchaseStatus.PAID.value,
            "success": CreditPurchaseStatus.PAID.value,
            "failed": CreditPurchaseStatus.FAILED.value,
            "canceled": CreditPurchaseStatus.FAILED.value,
            "cancelled": CreditPurchaseStatus.FAILED.value,
            "expired": CreditPurchaseStatus.EXPIRED.value,
            "refunded": CreditPurchaseStatus.REFUNDED.value,
        }
        return aliases.get(normalized, normalized)

    def _is_paid(self, purchase: CreditPurchase) -> bool:
        return self._status_value(purchase.status) == CreditPurchaseStatus.PAID.value

    def _provider(self) -> str:
        return settings.payment_mode

    def _api_base_url(self) -> str:
        return (settings.creem_api_base_url or "https://api.creem.io").rstrip("/")

    def _headers(self) -> dict[str, str]:
        if not settings.creem_api_key:
            raise PaymentError(
                code="creem_not_configured",
                message="Payment provider is not configured.",
                status_code=503,
            )
        return {
            "x-api-key": settings.creem_api_key,
            "Content-Type": "application/json",
        }

    def _manual_checkout_base_url(self) -> str:
        return f"{settings.effective_webhook_base_url.rstrip('/')}/api/v1/payments/manual/checkout"

    def _product_id_for_package(self, package_id: str) -> str:
        mapping = {
            "pack_50": settings.creem_product_pack_50,
            "pack_120": settings.creem_product_pack_120,
            "pack_300": settings.creem_product_pack_300,
        }
        product_id = (mapping.get(package_id) or "").strip()
        if not product_id:
            raise PaymentError(
                code="creem_product_missing",
                message=f"Payment product is not configured for package {package_id}.",
                status_code=503,
            )
        return product_id

    def _allowed_return_hosts(self) -> set[str]:
        hosts = {"localhost", "127.0.0.1"}
        for raw in (settings.effective_frontend_base_url, settings.cors_allow_origins):
            if not raw:
                continue
            for item in str(raw).split(","):
                parsed = urlparse(item.strip())
                if parsed.hostname:
                    hosts.add(parsed.hostname.lower())
        return hosts

    def _default_return_url(self) -> str:
        return settings.effective_frontend_base_url.rstrip("/")

    def _safe_return_url(self, return_url: str | None) -> str:
        candidate = (return_url or "").strip()
        if not candidate:
            return self._default_return_url()
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return self._default_return_url()
        if parsed.hostname.lower() not in self._allowed_return_hosts():
            return self._default_return_url()
        if parsed.scheme != "https" and not settings.using_manual_review_payments:
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
        query.update({k: v for k, v in params.items() if v})
        return urlunparse(parsed._replace(query=urlencode(query), fragment=parsed.fragment))

    def _extract_checkout_dict(self, payload: Any, checkout_id: str | None = None) -> dict[str, Any]:
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    if checkout_id is None:
                        return item
                    candidate_id = str(item.get("checkout_id") or item.get("id") or "")
                    if candidate_id == checkout_id:
                        return item
            return {}
        if not isinstance(payload, dict):
            return {}
        for key in ("object", "checkout", "item", "result"):
            child = payload.get(key)
            if isinstance(child, dict):
                return child
        for key in ("data", "items", "checkouts"):
            child = payload.get(key)
            result = self._extract_checkout_dict(child, checkout_id)
            if result:
                return result
        return payload

    def _extract_event_id(self, payload: dict[str, Any], body: bytes) -> str:
        value = payload.get("id") or payload.get("event_id")
        if value is not None:
            text = str(value).strip()
            if text:
                return text
        digest = hashlib.sha256(body or repr(payload).encode("utf-8")).hexdigest()
        return f"generated:{digest}"

    def _extract_event_type(self, payload: dict[str, Any]) -> str:
        return str(
            payload.get("eventType")
            or payload.get("event_type")
            or payload.get("type")
            or payload.get("event")
            or ""
        ).strip().lower()

    def _extract_event_object(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("object", "subscription", "checkout", "payment"):
                child = data.get(key)
                if isinstance(child, dict):
                    return child
            return data
        for key in ("object", "subscription", "checkout", "payment"):
            child = payload.get(key)
            if isinstance(child, dict):
                return child
        return payload

    def _extract_object_id(self, payload: dict[str, Any]) -> str | None:
        event_object = self._extract_event_object(payload)
        for key in ("subscription_id", "provider_subscription_id", "checkout_id", "payment_id", "id"):
            value = event_object.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        return None

    async def _get_or_create_payment_event(
        self,
        db: AsyncSession,
        *,
        provider: str,
        event_id: str,
        event_type: str,
        object_id: str | None,
        payload: dict[str, Any],
    ) -> tuple[PaymentEvent, bool]:
        result = await db.execute(
            select(PaymentEvent)
            .where(PaymentEvent.provider == provider, PaymentEvent.event_id == event_id)
            .with_for_update()
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing, False

        event = PaymentEvent(
            provider=provider,
            event_id=event_id,
            event_type=event_type or "unknown",
            object_id=object_id,
            payload_json=payload,
        )
        db.add(event)
        await db.flush()
        return event, True

    def _extract_checkout_url(self, payload: dict[str, Any]) -> str | None:
        for key in ("checkout_url", "url", "hosted_checkout_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _extract_checkout_id(self, payload: dict[str, Any]) -> str | None:
        for key in ("checkout_id", "id"):
            value = payload.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        return None

    def _extract_payment_id(self, payload: dict[str, Any]) -> str | None:
        for key in ("payment_id", "order_id", "charge_id"):
            value = payload.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        order = payload.get("order")
        if isinstance(order, dict):
            for key in ("transaction", "id", "payment_id", "charge_id"):
                value = order.get(key)
                if value is not None:
                    text = str(value).strip()
                    if text:
                        return text
        return None

    def _normalize_status(self, payload: dict[str, Any]) -> str:
        for key in ("payment_status", "status"):
            value = payload.get(key)
            if value is not None:
                return str(value).strip().lower()
        order = payload.get("order")
        if isinstance(order, dict):
            value = order.get("status")
            if value is not None:
                return str(value).strip().lower()
        return ""

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

    async def _get_purchase_for_user(self, db: AsyncSession, purchase_id: str, user_id: uuid.UUID) -> CreditPurchase:
        try:
            purchase_uuid = uuid.UUID(str(purchase_id))
        except ValueError as exc:
            raise PaymentError(code="invalid_purchase_id", message="Invalid purchase ID.", status_code=400) from exc
        result = await db.execute(
            select(CreditPurchase).where(CreditPurchase.id == purchase_uuid).with_for_update()
        )
        purchase = result.scalar_one_or_none()
        if purchase is None or purchase.user_id != user_id:
            raise PaymentError(code="purchase_not_found", message="Purchase not found.", status_code=404)
        return purchase

    async def _get_purchase_by_id(self, db: AsyncSession, purchase_id: str) -> CreditPurchase:
        try:
            purchase_uuid = uuid.UUID(str(purchase_id))
        except ValueError as exc:
            raise PaymentError(code="invalid_purchase_id", message="Invalid purchase ID.", status_code=400) from exc
        result = await db.execute(
            select(CreditPurchase).where(CreditPurchase.id == purchase_uuid).with_for_update()
        )
        purchase = result.scalar_one_or_none()
        if purchase is None:
            raise PaymentError(code="purchase_not_found", message="Purchase not found.", status_code=404)
        return purchase

    async def _get_manual_purchase(self, db: AsyncSession, purchase_id: str, token: str) -> CreditPurchase:
        purchase = await self._get_purchase_by_id(db, purchase_id)
        if purchase.provider != "manual_review":
            raise PaymentError(code="purchase_not_found", message="Purchase not found.", status_code=404)
        if str(purchase.provider_request_id or "").strip() != str(token or "").strip():
            raise PaymentError(code="invalid_checkout_token", message="Invalid checkout token.", status_code=403)
        return purchase

    async def create_checkout(
        self,
        db: AsyncSession,
        *,
        user: User,
        package_id: str,
        return_url: str | None,
    ) -> CreditPurchase:
        package = get_package_by_id(package_id)
        if not package:
            raise PaymentError(code="package_not_found", message="Credit package not found.", status_code=404)

        safe_return_url = self._safe_return_url(return_url)
        purchase = CreditPurchase(
            user_id=user.id,
            provider=self._provider(),
            package_id=package_id,
            credits=int(package["credits"]),
            price_cents=int(round(float(package["price"]) * 100)),
            currency="USD",
            status=CreditPurchaseStatus.PENDING,
            provider_request_id=str(uuid.uuid4()),
            metadata_json={
                "package_label": package["label"],
                "price": float(package["price"]),
                "popular": bool(package["popular"]),
            },
        )
        db.add(purchase)
        await db.flush()

        if settings.using_manual_review_payments:
            purchase.provider_checkout_id = f"manual-{uuid.uuid4()}"
            purchase.checkout_url = self._append_query(
                self._manual_checkout_base_url(),
                purchase_id=str(purchase.id),
                token=purchase.provider_request_id,
            )
            purchase.status = CreditPurchaseStatus.PENDING
            purchase.metadata_json = {
                **(purchase.metadata_json or {}),
                "return_url": safe_return_url,
                "payment_provider": self._provider(),
                "checkout_display_name": settings.manual_payment_display_name,
                "manual_payment_contact": settings.manual_payment_contact,
                "manual_payment_instructions": settings.manual_payment_instructions,
            }
            await db.flush()
            return purchase

        product_id = self._product_id_for_package(package_id)
        success_url = self._append_query(
            safe_return_url,
            payment="success",
            purchase_id=str(purchase.id),
        )
        payload = {
            "product_id": product_id,
            "request_id": purchase.provider_request_id,
            "success_url": success_url,
            "metadata": {
                "purchase_id": str(purchase.id),
                "user_id": str(user.id),
                "package_id": package_id,
                "credits": int(package["credits"]),
            },
        }

        try:
            response_data = await self._request("POST", "/v1/checkouts", json_body=payload)
        except httpx.HTTPError as exc:
            logger.warning("creem_checkout_create_failed: %s", exc)
            purchase.status = CreditPurchaseStatus.FAILED
            purchase.last_error = f"creem_checkout_create_failed:{type(exc).__name__}"
            await db.flush()
            raise PaymentError(
                code="payment_provider_unavailable",
                message="Unable to start payment checkout.",
                status_code=503,
            ) from exc

        checkout_payload = self._extract_checkout_dict(response_data)
        purchase.provider_checkout_id = self._extract_checkout_id(checkout_payload)
        purchase.checkout_url = self._extract_checkout_url(checkout_payload)
        purchase.status = CreditPurchaseStatus.PENDING
        purchase.metadata_json = {
            **(purchase.metadata_json or {}),
            "creem_checkout": checkout_payload,
            "success_url": success_url,
        }
        await db.flush()

        if not purchase.checkout_url:
            purchase.status = CreditPurchaseStatus.FAILED
            purchase.last_error = "creem_checkout_url_missing"
            await db.flush()
            raise PaymentError(
                code="payment_checkout_invalid",
                message="Payment checkout URL is missing from provider response.",
                status_code=503,
            )

        return purchase

    async def _add_purchase_credits(
        self,
        db: AsyncSession,
        purchase: CreditPurchase,
        checkout_payload: dict[str, Any] | None,
    ) -> int:
        await add_credits_async(
            db,
            purchase.user_id,
            int(purchase.credits),
            transaction_type=CreditTransactionType.PURCHASE,
            source="credit_purchase",
            source_id=str(purchase.id),
            description=f"Credit package purchase: {purchase.package_id}",
            metadata={
                "provider": purchase.provider,
                "provider_checkout_id": purchase.provider_checkout_id,
                "provider_payment_id": purchase.provider_payment_id,
                "checkout_payload": checkout_payload or {},
            },
        )
        return await get_balance_async(db, purchase.user_id)

    async def finalize_purchase(
        self,
        db: AsyncSession,
        purchase: CreditPurchase,
        *,
        checkout_payload: dict[str, Any] | None = None,
        webhook_event_id: str | None = None,
    ) -> CreditPurchase:
        if self._is_paid(purchase):
            return purchase

        purchase.provider_checkout_id = (
            purchase.provider_checkout_id
            or self._extract_checkout_id(checkout_payload or {})
        )
        purchase.provider_payment_id = self._extract_payment_id(checkout_payload or {}) or purchase.provider_payment_id
        purchase.webhook_event_id = webhook_event_id or purchase.webhook_event_id
        purchase.status = CreditPurchaseStatus.PAID
        purchase.completed_at = datetime.now(timezone.utc)
        purchase.last_error = None
        purchase.metadata_json = {
            **(purchase.metadata_json or {}),
            "last_checkout_payload": checkout_payload or {},
        }
        await self._add_purchase_credits(db, purchase, checkout_payload)
        await db.flush()

        try:
            from app.services.email_service import send_payment_confirmation
            import asyncio
            user = await db.get(User, purchase.user_id)
            if user and user.email:
                pkg = get_package_by_id(purchase.package_id)
                asyncio.create_task(send_payment_confirmation(
                    to=user.email,
                    credits=purchase.credits,
                    package_name=pkg.get("name", purchase.package_id) if pkg else purchase.package_id,
                    amount_display=f"${purchase.price_cents / 100:.2f}" if purchase.price_cents else "N/A",
                ))
        except Exception as exc:
            logger.warning("Payment confirmation email failed: %s", exc)

        return purchase

    async def mark_purchase_terminal(
        self,
        db: AsyncSession,
        purchase: CreditPurchase,
        *,
        status: CreditPurchaseStatus,
        checkout_payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> CreditPurchase:
        if self._is_paid(purchase):
            return purchase
        purchase.status = status
        purchase.last_error = error or purchase.last_error
        purchase.metadata_json = {
            **(purchase.metadata_json or {}),
            "last_checkout_payload": checkout_payload or {},
        }
        await db.flush()
        return purchase

    async def get_manual_checkout_context(
        self,
        db: AsyncSession,
        *,
        purchase_id: str,
        token: str,
    ) -> dict[str, Any]:
        purchase = await self._get_manual_purchase(db, purchase_id, token)
        metadata = purchase.metadata_json if isinstance(purchase.metadata_json, dict) else {}
        return {
            "purchase": purchase,
            "return_url": str(metadata.get("return_url") or self._default_return_url()),
            "display_name": str(metadata.get("checkout_display_name") or settings.manual_payment_display_name),
            "contact": str(metadata.get("manual_payment_contact") or settings.manual_payment_contact or "").strip(),
            "instructions": str(
                metadata.get("manual_payment_instructions") or settings.manual_payment_instructions or ""
            ).strip(),
            "submitted_at": str(metadata.get("manual_submitted_at") or "").strip(),
        }

    async def acknowledge_manual_checkout(
        self,
        db: AsyncSession,
        *,
        purchase_id: str,
        token: str,
    ) -> CreditPurchase:
        purchase = await self._get_manual_purchase(db, purchase_id, token)
        metadata = purchase.metadata_json if isinstance(purchase.metadata_json, dict) else {}
        metadata["manual_submitted_at"] = datetime.now(timezone.utc).isoformat()
        metadata["manual_checkout_acknowledged"] = True
        purchase.metadata_json = metadata
        purchase.status = CreditPurchaseStatus.PENDING
        await db.flush()
        return purchase

    async def complete_manual_purchase(
        self,
        db: AsyncSession,
        *,
        purchase_id: str,
    ) -> CreditPurchase:
        purchase = await self._get_purchase_by_id(db, purchase_id)
        if purchase.provider != "manual_review":
            raise PaymentError(code="unsupported_payment_provider", message="Manual approval is not available.", status_code=400)
        return await self.finalize_purchase(
            db,
            purchase,
            checkout_payload={"provider": "manual_review", "status": "completed"},
        )

    async def fail_manual_purchase(
        self,
        db: AsyncSession,
        *,
        purchase_id: str,
        reason: str | None = None,
    ) -> CreditPurchase:
        purchase = await self._get_purchase_by_id(db, purchase_id)
        if purchase.provider != "manual_review":
            raise PaymentError(code="unsupported_payment_provider", message="Manual rejection is not available.", status_code=400)
        return await self.mark_purchase_terminal(
            db,
            purchase,
            status=CreditPurchaseStatus.FAILED,
            checkout_payload={"provider": "manual_review", "status": "failed"},
            error=(reason or "manual_review_rejected").strip(),
        )

    async def sync_checkout_status(
        self,
        db: AsyncSession,
        *,
        purchase: CreditPurchase,
        checkout_id: str | None = None,
    ) -> CreditPurchase:
        if purchase.provider == "manual_review":
            return purchase
        provider_checkout_id = (checkout_id or purchase.provider_checkout_id or "").strip()
        if not provider_checkout_id:
            return purchase

        try:
            response_data = await self._request("GET", f"/v1/checkouts?checkout_id={provider_checkout_id}")
        except httpx.HTTPError as exc:
            logger.warning("creem_checkout_status_failed: %s", exc)
            raise PaymentError(
                code="payment_status_unavailable",
                message="Unable to verify payment status.",
                status_code=503,
            ) from exc

        checkout_payload = self._extract_checkout_dict(response_data, provider_checkout_id)
        normalized_status = self._normalize_status(checkout_payload)
        purchase.provider_checkout_id = provider_checkout_id
        purchase.checkout_url = self._extract_checkout_url(checkout_payload) or purchase.checkout_url

        if normalized_status in self._COMPLETED_STATES:
            return await self.finalize_purchase(db, purchase, checkout_payload=checkout_payload)
        if normalized_status in self._FAILED_STATES:
            return await self.mark_purchase_terminal(
                db,
                purchase,
                status=CreditPurchaseStatus.FAILED,
                checkout_payload=checkout_payload,
                error=f"provider_status:{normalized_status}",
            )
        if normalized_status in self._EXPIRED_STATES:
            return await self.mark_purchase_terminal(
                db,
                purchase,
                status=CreditPurchaseStatus.EXPIRED,
                checkout_payload=checkout_payload,
                error=f"provider_status:{normalized_status}",
            )
        if normalized_status in self._CANCELED_STATES:
            return await self.mark_purchase_terminal(
                db,
                purchase,
                status=CreditPurchaseStatus.FAILED,
                checkout_payload=checkout_payload,
                error=f"provider_status:{normalized_status}",
            )

        purchase.status = CreditPurchaseStatus.PENDING
        purchase.metadata_json = {
            **(purchase.metadata_json or {}),
            "last_checkout_payload": checkout_payload,
        }
        await db.flush()
        return purchase

    def verify_webhook_signature(self, body: bytes, signature_header: str | None) -> bool:
        secret = (settings.creem_webhook_secret or "").strip()
        if not secret:
            return False
        if not signature_header:
            return False
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        candidates = []
        for piece in str(signature_header).split(","):
            piece = piece.strip()
            if not piece:
                continue
            if "=" in piece:
                _, _, value = piece.partition("=")
                piece = value.strip()
            candidates.append(piece)
        return any(hmac.compare_digest(expected, item) for item in candidates)

    async def get_purchase_status(
        self,
        db: AsyncSession,
        *,
        purchase_id: str,
        user_id: uuid.UUID,
        checkout_id: str | None = None,
    ) -> tuple[CreditPurchase, int]:
        purchase = await self._get_purchase_for_user(db, purchase_id, user_id)
        if self._status_value(purchase.status) == CreditPurchaseStatus.PENDING.value and (checkout_id or purchase.provider_checkout_id):
            purchase = await self.sync_checkout_status(db, purchase=purchase, checkout_id=checkout_id)
        balance = await get_balance_async(db, purchase.user_id)
        return purchase, balance

    async def process_webhook_event(self, db: AsyncSession, *, payload: dict[str, Any], body: bytes, signature_header: str | None) -> CreditPurchase | None:
        if not self.verify_webhook_signature(body, signature_header):
            raise PaymentError(code="invalid_webhook_signature", message="Invalid webhook signature.", status_code=401)

        provider = self._provider()
        event_type = self._extract_event_type(payload)
        event_id = self._extract_event_id(payload, body)
        payment_event, _created = await self._get_or_create_payment_event(
            db,
            provider=provider,
            event_id=event_id,
            event_type=event_type,
            object_id=self._extract_object_id(payload),
            payload=payload,
        )
        if payment_event.processed_at is not None:
            return None

        try:
            purchase: CreditPurchase | None = None
            if event_type in self._ONE_TIME_WEBHOOK_EVENTS:
                checkout_payload = self._extract_checkout_dict(payload.get("data") or payload)
                request_id = str(checkout_payload.get("request_id") or "").strip()
                checkout_id = self._extract_checkout_id(checkout_payload)

                if request_id:
                    result = await db.execute(
                        select(CreditPurchase).where(CreditPurchase.provider_request_id == request_id).with_for_update()
                    )
                    purchase = result.scalar_one_or_none()
                if purchase is None and checkout_id:
                    result = await db.execute(
                        select(CreditPurchase).where(CreditPurchase.provider_checkout_id == checkout_id).with_for_update()
                    )
                    purchase = result.scalar_one_or_none()
                if purchase is None:
                    raise PaymentError(code="purchase_not_found", message="Purchase not found for webhook.", status_code=404)
                purchase = await self.finalize_purchase(
                    db,
                    purchase,
                    checkout_payload=checkout_payload,
                    webhook_event_id=event_id,
                )
            elif event_type in self._SUBSCRIPTION_WEBHOOK_EVENTS:
                await subscription_service.process_provider_event(
                    db,
                    provider=provider,
                    event_type=event_type,
                    payload=self._extract_event_object(payload),
                )

            payment_event.processed_at = datetime.now(timezone.utc)
            payment_event.error = None
            await db.flush()
            return purchase
        except Exception as exc:
            payment_event.error = f"{type(exc).__name__}:{exc}"
            await db.flush()
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except ImportError:
                pass
            raise


payment_service = PaymentService()
