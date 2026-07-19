"""Raw-body verified Creem event ingestion and normalization."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any
import uuid

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.models.payment_event import PaymentEvent, PaymentEventProcessingState
from app.schemas.payment import AcceptedPaymentEvent, NormalizedPaymentEvent


KNOWN_EVENT_TYPES = frozenset(
    {
        "checkout.completed",
        "refund.created",
        "dispute.created",
        "dispute.updated",
        "dispute.closed",
        "subscription.active",
        "subscription.paid",
        "subscription.canceled",
        "subscription.scheduled_cancel",
        "subscription.past_due",
        "subscription.expired",
        "subscription.update",
        "subscription.trialing",
        "subscription.paused",
    }
)


class CreemEventError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 400):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class InvalidWebhookSignature(CreemEventError):
    def __init__(self) -> None:
        super().__init__("invalid_webhook_signature", status_code=401)


class PaymentEventHashConflict(CreemEventError):
    def __init__(self) -> None:
        super().__init__("payment_event_hash_conflict", status_code=409)


def verify_creem_signature(raw_body: bytes, signature: str | None, webhook_secret: bytes) -> None:
    if not raw_body or not webhook_secret:
        raise InvalidWebhookSignature()
    supplied = str(signature or "").strip().lower()
    if len(supplied) != 64 or any(char not in "0123456789abcdef" for char in supplied):
        raise InvalidWebhookSignature()
    expected = hmac.new(webhook_secret, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise InvalidWebhookSignature()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CreemEventError("webhook_json_duplicate_key")
        result[key] = value
    return result


def parse_creem_raw_body(raw_body: bytes) -> dict[str, Any]:
    try:
        decoded = raw_body.decode("utf-8")
        payload = json.loads(decoded, object_pairs_hook=_reject_duplicate_json_keys)
    except CreemEventError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CreemEventError("webhook_json_invalid") from exc
    if not isinstance(payload, dict):
        raise CreemEventError("webhook_json_object_required")
    return payload


def _clean_identifier(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate or len(candidate) > 128:
        return None
    return candidate


def _as_nonnegative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _parse_occurred_at(value: Any) -> datetime:
    if isinstance(value, bool):
        raise CreemEventError("webhook_occurred_at_invalid")
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise CreemEventError("webhook_occurred_at_invalid") from exc
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise CreemEventError("webhook_occurred_at_invalid") from exc
        if parsed.tzinfo is None:
            raise CreemEventError("webhook_occurred_at_timezone_required")
        parsed = parsed.astimezone(timezone.utc)
    else:
        raise CreemEventError("webhook_occurred_at_required")
    return parsed


def _object_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _object_identifier(value: Any) -> str | None:
    if isinstance(value, dict):
        return _clean_identifier(value.get("id"))
    return _clean_identifier(value)


def _metadata_strings(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _clean_identifier(raw_key)
        if key is None or len(result) >= 20:
            continue
        if isinstance(raw_value, (str, int, float, bool)) and not isinstance(raw_value, (dict, list)):
            text_value = str(raw_value).strip()
            if text_value and len(text_value) <= 512:
                result[key] = text_value
    return result


def normalize_creem_event(
    payload: dict[str, Any],
    raw_payload_sha256: str,
) -> NormalizedPaymentEvent:
    event_id = _clean_identifier(payload.get("id") or payload.get("event_id"))
    event_type = _clean_identifier(
        payload.get("eventType") or payload.get("event_type") or payload.get("type")
    )
    event_type = event_type.lower() if event_type else None
    raw_event_object = payload.get("object") or payload.get("data")
    if not isinstance(raw_event_object, dict) and event_type:
        if event_type.startswith("subscription."):
            raw_event_object = payload.get("subscription")
        elif event_type == "checkout.completed":
            raw_event_object = payload.get("checkout")
    event_object = _object_value(raw_event_object)
    object_id = _clean_identifier(event_object.get("id"))
    if not event_id or not event_type or not object_id:
        raise CreemEventError("webhook_event_identity_invalid")
    occurred_at = _parse_occurred_at(payload.get("created_at") or payload.get("createdAt"))

    request_id = _clean_identifier(event_object.get("request_id"))
    customer_id = _object_identifier(event_object.get("customer"))
    normalized_status = _clean_identifier(event_object.get("status")) or "unknown"
    business_metadata = _metadata_strings(event_object.get("metadata"))
    pre_tax_minor_units: int | None = None
    tax_minor_units: int | None = None
    currency: str | None = None

    if event_type == "checkout.completed":
        order = _object_value(event_object.get("order"))
        product = _object_value(event_object.get("product"))
        provider_product_id = (
            _object_identifier(event_object.get("product"))
            or _object_identifier(order.get("product"))
        )
        provider_payment_id = _clean_identifier(order.get("transaction") or order.get("id"))
        provider_order_id = _clean_identifier(order.get("id"))
        customer_id = customer_id or _object_identifier(order.get("customer"))
        normalized_status = _clean_identifier(order.get("status")) or normalized_status
        pre_tax_minor_units = (
            _as_nonnegative_int(order.get("sub_total"))
            if order.get("sub_total") is not None
            else _as_nonnegative_int(product.get("price"))
        )
        if pre_tax_minor_units is None:
            pre_tax_minor_units = _as_nonnegative_int(order.get("amount"))
        tax_minor_units = _as_nonnegative_int(order.get("tax_amount"))
        if tax_minor_units is None:
            amount_paid = _as_nonnegative_int(order.get("amount_paid"))
            if amount_paid is not None and pre_tax_minor_units is not None and amount_paid >= pre_tax_minor_units:
                tax_minor_units = amount_paid - pre_tax_minor_units
        currency = _clean_identifier(order.get("currency") or product.get("currency"))
        if provider_product_id:
            business_metadata["provider_product_id"] = provider_product_id
        if provider_payment_id:
            business_metadata["provider_payment_id"] = provider_payment_id
        if provider_order_id:
            business_metadata["provider_order_id"] = provider_order_id
        provider_subscription_id = _object_identifier(
            event_object.get("subscription") or payload.get("subscription")
        )
        if provider_subscription_id:
            business_metadata["provider_subscription_id"] = provider_subscription_id
        business_metadata["provider_checkout_id"] = object_id
    elif event_type == "refund.created":
        transaction = _object_value(event_object.get("transaction"))
        event_amount = _as_nonnegative_int(event_object.get("refund_amount"))
        provider_payment_id = _clean_identifier(transaction.get("id"))
        currency = _clean_identifier(event_object.get("refund_currency"))
        if event_amount is not None:
            business_metadata["event_minor_units"] = str(event_amount)
        if provider_payment_id:
            business_metadata["provider_payment_id"] = provider_payment_id
        business_metadata["provider_refund_id"] = object_id
    elif event_type.startswith("dispute."):
        transaction = _object_value(event_object.get("transaction"))
        event_amount = _as_nonnegative_int(event_object.get("amount"))
        provider_payment_id = _clean_identifier(transaction.get("id"))
        currency = _clean_identifier(event_object.get("currency"))
        if event_amount is not None:
            business_metadata["event_minor_units"] = str(event_amount)
        if provider_payment_id:
            business_metadata["provider_payment_id"] = provider_payment_id
        business_metadata["provider_dispute_id"] = object_id
        explicit_outcome = _clean_identifier(event_object.get("outcome"))
        if explicit_outcome:
            business_metadata["dispute_outcome"] = explicit_outcome.lower()
    elif event_type.startswith("subscription."):
        checkout = _object_value(payload.get("checkout"))
        order = _object_value(payload.get("order"))
        product = _object_value(event_object.get("product") or payload.get("product"))
        transaction = _object_value(
            event_object.get("last_transaction") or payload.get("transaction")
        )
        request_id = request_id or _clean_identifier(checkout.get("request_id"))
        for key, value in _metadata_strings(checkout.get("metadata")).items():
            business_metadata.setdefault(key, value)
        provider_product_id = (
            _object_identifier(event_object.get("product"))
            or _object_identifier(payload.get("product"))
            or _object_identifier(order.get("product"))
        )
        if provider_product_id:
            business_metadata["provider_product_id"] = provider_product_id
        customer_id = customer_id or _object_identifier(event_object.get("customer"))
        customer_id = customer_id or _object_identifier(payload.get("customer"))
        customer_id = customer_id or _object_identifier(order.get("customer"))
        pre_tax_minor_units = _as_nonnegative_int(product.get("price"))
        if pre_tax_minor_units is None:
            pre_tax_minor_units = _as_nonnegative_int(order.get("sub_total"))
        if pre_tax_minor_units is None:
            pre_tax_minor_units = _as_nonnegative_int(order.get("amount"))
        tax_minor_units = _as_nonnegative_int(
            transaction.get("tax_amount")
            if transaction.get("tax_amount") is not None
            else order.get("tax_amount")
        )
        if tax_minor_units is None:
            amount_paid = _as_nonnegative_int(order.get("amount_paid"))
            if (
                amount_paid is not None
                and pre_tax_minor_units is not None
                and amount_paid >= pre_tax_minor_units
            ):
                tax_minor_units = amount_paid - pre_tax_minor_units
        currency = _clean_identifier(
            transaction.get("currency")
            or product.get("currency")
            or order.get("currency")
        )
        transaction_id = _clean_identifier(
            event_object.get("last_transaction_id")
            or transaction.get("id")
            or order.get("transaction")
        )
        if transaction_id:
            business_metadata["last_transaction_id"] = transaction_id
        provider_invoice_id = _clean_identifier(
            transaction.get("invoice") or transaction.get("order") or order.get("id")
        )
        if provider_invoice_id:
            business_metadata["provider_invoice_id"] = provider_invoice_id
        provider_checkout_id = _object_identifier(checkout)
        if provider_checkout_id:
            business_metadata["provider_checkout_id"] = provider_checkout_id
        for field in (
            "current_period_start_date",
            "current_period_end_date",
            "next_transaction_date",
        ):
            field_value = _clean_identifier(event_object.get(field))
            if field_value:
                business_metadata[field] = field_value
        for source_field, target_field in (
            ("period_start", "current_period_start_date"),
            ("period_end", "current_period_end_date"),
        ):
            if target_field not in business_metadata:
                field_value = _clean_identifier(transaction.get(source_field))
                if field_value:
                    business_metadata[target_field] = field_value

    if currency is not None:
        currency = currency.upper()
    try:
        return NormalizedPaymentEvent.model_validate(
            {
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "object_id": object_id,
                "request_id": request_id,
                "customer_id": customer_id,
                "pre_tax_minor_units": pre_tax_minor_units,
                "tax_minor_units": tax_minor_units,
                "currency": currency,
                "normalized_status": normalized_status.lower(),
                "business_metadata": business_metadata,
                "raw_payload_sha256": raw_payload_sha256,
            }
        )
    except ValidationError as exc:
        raise CreemEventError("webhook_normalization_invalid") from exc


async def _lock_event_identity(db: AsyncSession, *, provider: str, event_id: str) -> None:
    digest = hashlib.sha256(f"{provider}:{event_id}".encode("utf-8")).digest()
    lock_key = int.from_bytes(digest[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


async def ingest_verified_creem_event(
    db: AsyncSession,
    *,
    raw_body: bytes,
    signature: str | None,
    webhook_secret: bytes,
) -> AcceptedPaymentEvent:
    """Verify, normalize, deduplicate, persist and enqueue in one transaction."""

    verify_creem_signature(raw_body, signature, webhook_secret)
    payload = parse_creem_raw_body(raw_body)
    raw_hash = hashlib.sha256(raw_body).hexdigest()
    normalized = normalize_creem_event(payload, raw_hash)
    await _lock_event_identity(db, provider="creem", event_id=normalized.event_id)
    existing = await db.scalar(
        select(PaymentEvent)
        .where(
            PaymentEvent.provider == "creem",
            PaymentEvent.event_id == normalized.event_id,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.raw_payload_sha256 != raw_hash:
            raise PaymentEventHashConflict()
        state = existing.processing_state.value if hasattr(existing.processing_state, "value") else str(existing.processing_state)
        return AcceptedPaymentEvent(
            event_id=normalized.event_id,
            created=False,
            processing_state=state,
        )

    event_id = uuid.uuid4()
    initial_state = (
        PaymentEventProcessingState.RECEIVED
        if normalized.event_type in KNOWN_EVENT_TYPES
        else PaymentEventProcessingState.UNHANDLED
    )
    event = PaymentEvent(
        id=event_id,
        provider="creem",
        event_id=normalized.event_id,
        event_type=normalized.event_type,
        object_id=normalized.object_id,
        payload_json=payload,
        raw_payload_sha256=normalized.raw_payload_sha256,
        occurred_at=normalized.occurred_at,
        request_id=normalized.request_id,
        customer_id=normalized.customer_id,
        pre_tax_minor_units=normalized.pre_tax_minor_units,
        tax_minor_units=normalized.tax_minor_units,
        currency=normalized.currency,
        normalized_status=normalized.normalized_status,
        business_metadata=dict(normalized.business_metadata),
        processing_state=initial_state,
    )
    db.add(event)
    db.add(
        OutboxEvent(
            id=uuid.uuid4(),
            aggregate_type="payment_event",
            aggregate_id=event_id,
            event_type="payment.event.received",
            dedupe_key=f"payment-event:{event_id}",
            payload_version="vowpic.payment-event.v1",
            payload_json={"payment_event_id": str(event_id)},
            status=OutboxEventStatus.PENDING,
            attempt_count=0,
            next_attempt_at=datetime.now(timezone.utc),
            fencing_token=0,
        )
    )
    await db.flush()
    await db.commit()
    return AcceptedPaymentEvent(
        event_id=normalized.event_id,
        created=True,
        processing_state=initial_state.value,
    )
