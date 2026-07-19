"""Build a strict account export from canonical and merge-linked user facts."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterable, Sequence
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_tombstone import AccountTombstone
from app.models.credit_purchase import CreditPurchase
from app.models.credit_transaction import CreditTransaction
from app.models.media_asset import MediaAsset
from app.models.order import Order
from app.models.partner_consent_case import PartnerConsentCase
from app.models.partner_invite import PartnerInvite
from app.models.partner_invite_event import PartnerInviteEvent
from app.models.payment_event import PaymentDisputeFact, PaymentRefundFact
from app.models.subscription_invoice import (
    SubscriptionInvoice,
    SubscriptionInvoiceAdjustmentFact,
)
from app.models.user import User
from app.models.user_account_merge import UserAccountMerge
from app.models.user_identity import UserIdentity
from app.models.user_subscription import UserSubscription
from app.schemas.account_export import AccountExport


FORBIDDEN_EXPORT_KEYS = frozenset(
    {
        "access_token",
        "auth_subject",
        "avatar_url",
        "checkout_url",
        "client_request_id",
        "device_hash",
        "email_hash",
        "generation_params",
        "ip_address",
        "ip_hash",
        "last_error",
        "last_error_detail",
        "metadata_json",
        "object_key",
        "openid",
        "password",
        "payload_json",
        "provider_customer_id",
        "provider_dispute_id",
        "provider_evidence",
        "provider_invoice_id",
        "provider_job_id",
        "provider_payment_id",
        "provider_refund_id",
        "provider_request_id",
        "provider_subscription_id",
        "provider_transaction_id",
        "raw_payload_sha256",
        "refresh_token",
        "request_hash",
        "request_snapshot",
        "secret",
        "source_image_urls",
        "stored_response",
        "subject",
        "token_hash",
        "unionid",
        "user_agent",
    }
)
_FORBIDDEN_KEY_FRAGMENTS = (
    "password",
    "token",
    "secret",
    "object_key",
    "permanent_url",
    "public_url",
    "internal_path",
    "embedding",
    "raw_payload",
)


class AccountExportError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _uuid_tuple(values: Iterable[Any] | None, label: str) -> tuple[uuid.UUID, ...]:
    result: list[uuid.UUID] = []
    for value in values or ():
        try:
            result.append(uuid.UUID(str(value)))
        except (TypeError, ValueError) as exc:
            raise AccountExportError(f"{label}_contains_invalid_uuid") from exc
    return tuple(result)


def reject_forbidden_export_keys(value: Any, path: str = "$") -> None:
    """Reject forbidden names and URL/path-shaped values after strict projection."""

    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_EXPORT_KEYS or any(
                fragment in normalized for fragment in _FORBIDDEN_KEY_FRAGMENTS
            ):
                raise AccountExportError(f"forbidden_export_key:{path}.{key}")
            if ("hash" in normalized or "sha256" in normalized) and normalized != "content_sha256":
                raise AccountExportError(f"forbidden_export_hash:{path}.{key}")
            if normalized.endswith("_url") or normalized.endswith("_path"):
                raise AccountExportError(f"forbidden_export_location:{path}.{key}")
            reject_forbidden_export_keys(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            reject_forbidden_export_keys(item, f"{path}[{index}]")
        return
    if isinstance(value, str) and "://" in value:
        raise AccountExportError(f"forbidden_export_url_value:{path}")


async def _projected_rows(
    db: AsyncSession,
    columns: Sequence[Any],
    condition: Any,
    *order_by: Any,
) -> list[Any]:
    statement = select(*columns).where(condition)
    if order_by:
        statement = statement.order_by(*order_by)
    result = await db.execute(statement)
    return [SimpleNamespace(**dict(row)) for row in result.mappings().all()]


async def _resolve_account_lineage(
    db: AsyncSession,
    requested_user_id: uuid.UUID,
) -> tuple[uuid.UUID, list[UserAccountMerge]]:
    incoming_canonical_user_id = await db.scalar(
        select(UserAccountMerge.canonical_user_id).where(
            UserAccountMerge.legacy_user_id == requested_user_id
        )
    )
    canonical_user_id = (
        incoming_canonical_user_id
        if incoming_canonical_user_id is not None
        else requested_user_id
    )
    merges = await _projected_rows(
        db,
        (
            UserAccountMerge.id,
            UserAccountMerge.canonical_user_id,
            UserAccountMerge.legacy_user_id,
            UserAccountMerge.created_at,
        ),
        UserAccountMerge.canonical_user_id == canonical_user_id,
        UserAccountMerge.created_at,
        UserAccountMerge.id,
    )
    if any(merge.canonical_user_id != canonical_user_id for merge in merges):
        raise AccountExportError("account_merge_lineage_invalid")
    return canonical_user_id, merges


def _assemble_account_export(
    *,
    canonical_user_id: uuid.UUID,
    requested_user_id: uuid.UUID,
    users: list[User],
    merges: list[UserAccountMerge],
    identities: list[UserIdentity],
    orders: list[Order],
    ledger: list[CreditTransaction],
    purchases: list[CreditPurchase],
    refunds: list[PaymentRefundFact],
    disputes: list[PaymentDisputeFact],
    subscriptions: list[UserSubscription],
    invoices: list[SubscriptionInvoice],
    adjustments: list[SubscriptionInvoiceAdjustmentFact],
    invites: list[PartnerInvite],
    invite_events: list[PartnerInviteEvent],
    consent_cases: list[PartnerConsentCase],
    media: list[MediaAsset],
    tombstones: list[AccountTombstone],
    generated_at: datetime,
    export_id: uuid.UUID,
) -> AccountExport:
    users_by_id = {user.id: user for user in users}
    expected_user_ids = {
        canonical_user_id,
        *(merge.legacy_user_id for merge in merges),
    }
    if requested_user_id not in expected_user_ids or canonical_user_id not in users_by_id:
        raise AccountExportError("account_not_found")
    if set(users_by_id) != expected_user_ids:
        raise AccountExportError("account_export_user_scope_drift")
    scoped_collections = (
        ("identity", identities, "user_id"),
        ("order", orders, "user_id"),
        ("ledger", ledger, "user_id"),
        ("purchase", purchases, "user_id"),
        ("subscription", subscriptions, "user_id"),
        ("invoice", invoices, "user_id"),
        ("media", media, "owner_user_id"),
        ("tombstone", tombstones, "user_id"),
    )
    for label, rows, field in scoped_collections:
        if any(getattr(row, field) not in expected_user_ids for row in rows):
            raise AccountExportError(f"account_export_{label}_scope_drift")
    canonical = users_by_id[canonical_user_id]
    included_user_ids = tuple(sorted(users_by_id, key=str))
    invoice_ids = {invoice.id for invoice in invoices}
    purchase_ids = {purchase.id for purchase in purchases}
    if any(row.purchase_id not in purchase_ids for row in (*refunds, *disputes)):
        raise AccountExportError("account_export_payment_scope_drift")
    if any(row.invoice_id not in invoice_ids for row in adjustments):
        raise AccountExportError("account_export_invoice_scope_drift")
    if any(
        invite.host_user_id not in expected_user_ids
        and invite.partner_user_id not in expected_user_ids
        for invite in invites
    ):
        raise AccountExportError("account_export_invite_scope_drift")
    if any(
        case.host_user_id not in expected_user_ids
        and case.partner_user_id not in expected_user_ids
        for case in consent_cases
    ):
        raise AccountExportError("account_export_consent_scope_drift")
    invite_by_id = {invite.id: invite for invite in invites}

    consent_records: list[dict[str, Any]] = []
    for invite in invites:
        asset_ids = (invite.partner_asset_id,) if invite.partner_asset_id else ()
        consent_records.append(
            {
                "record_id": invite.id,
                "record_type": "INVITE",
                "host_user_id": invite.host_user_id,
                "partner_user_id": invite.partner_user_id,
                "order_id": invite.order_id,
                "job_id": invite.job_id,
                "asset_ids": asset_ids,
                "status": _value(invite.status),
                "action": None,
                "created_at": invite.created_at,
                "updated_at": invite.updated_at,
            }
        )
    for event in invite_events:
        invite = invite_by_id.get(event.invite_id)
        if invite is None:
            raise AccountExportError("partner_invite_event_without_visible_invite")
        consent_records.append(
            {
                "record_id": event.id,
                "record_type": "INVITE_EVENT",
                "host_user_id": invite.host_user_id,
                "partner_user_id": invite.partner_user_id,
                "order_id": invite.order_id,
                "job_id": invite.job_id,
                "asset_ids": (),
                "status": event.to_status,
                "action": event.command,
                "created_at": event.created_at,
                "updated_at": None,
            }
        )
    for case in consent_cases:
        consent_records.append(
            {
                "record_id": case.id,
                "record_type": "CONSENT_CASE",
                "host_user_id": case.host_user_id,
                "partner_user_id": case.partner_user_id,
                "order_id": case.order_id,
                "job_id": case.job_id,
                "asset_ids": _uuid_tuple(case.owned_asset_ids, "partner_consent_assets"),
                "status": _value(case.status),
                "action": case.reason_code,
                "created_at": case.created_at,
                "updated_at": case.updated_at,
            }
        )

    media_statuses = [_value(asset.status) for asset in media]
    tombstone_by_user = {row.user_id: row for row in tombstones}
    export = AccountExport.model_validate(
        {
            "schema_version": "account-export.v1",
            "export_id": export_id,
            "generated_at": generated_at,
            "canonical_user_id": canonical_user_id,
            "included_user_ids": included_user_ids,
            "profile": {
                "user_id": canonical.id,
                "username": canonical.username,
                "email": canonical.email,
                "nickname": canonical.nickname,
                "role": canonical.role,
                "status": canonical.status,
                "last_login_at": canonical.last_login_at,
                "created_at": canonical.created_at,
                "updated_at": canonical.updated_at,
            },
            "merged_accounts": [
                {
                    "merge_id": row.id,
                    "canonical_user_id": row.canonical_user_id,
                    "legacy_user_id": row.legacy_user_id,
                    "created_at": row.created_at,
                }
                for row in merges
            ],
            "identities": [
                {
                    "identity_id": row.id,
                    "user_id": row.user_id,
                    "provider": row.provider,
                    "revoked_at": row.revoked_at,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in identities
            ],
            "orders": [
                {
                    "order_id": row.id,
                    "user_id": row.user_id,
                    "status": _value(row.status),
                    "template_id": row.template_id,
                    "reservation_id": row.reservation_id,
                    "generation_job_id": row.generation_job_id,
                    "source_asset_ids": _uuid_tuple(row.source_asset_ids, "order_source_assets"),
                    "preview_asset_ids": _uuid_tuple(row.preview_asset_ids, "order_preview_assets"),
                    "final_asset_ids": _uuid_tuple(row.final_asset_ids, "order_final_assets"),
                    "settlement_status": row.settlement_status,
                    "delivery_status": row.delivery_status,
                    "source_images_expires_at": row.source_images_expires_at,
                    "expires_at": row.expires_at,
                    "deleted_at": row.deleted_at,
                    "storage_cleanup_status": row.storage_cleanup_status,
                    "price_minor_units": row.price_cents,
                    "paid_at": row.paid_at,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in orders
            ],
            "ledger": [
                {
                    "transaction_id": row.id,
                    "user_id": row.user_id,
                    "transaction_type": _value(row.transaction_type),
                    "amount": row.amount,
                    "balance_after": row.balance_after,
                    "source": row.source,
                    "root_transaction_id": row.root_transaction_id,
                    "reversal_of_transaction_id": row.reversal_of_transaction_id,
                    "provider_attempt_id": row.provider_attempt_id,
                    "created_at": row.created_at,
                }
                for row in ledger
            ],
            "purchases": [
                {
                    "purchase_id": row.id,
                    "user_id": row.user_id,
                    "product_code": row.package_id,
                    "credits": row.credits,
                    "pre_tax_minor_units": row.price_cents,
                    "currency": row.currency,
                    "status": _value(row.status),
                    "captured_minor_units": row.captured_minor_units,
                    "tax_minor_units": row.tax_minor_units,
                    "refunded_minor_units": row.refunded_minor_units,
                    "disputed_minor_units": row.disputed_minor_units,
                    "dispute_state": row.dispute_state,
                    "grant_transaction_id": row.grant_transaction_id,
                    "grant_lot_id": row.grant_lot_id,
                    "confirmed_at": row.confirmed_at,
                    "completed_at": row.completed_at,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in purchases
            ],
            "refunds": [
                {
                    "refund_id": row.id,
                    "purchase_id": row.purchase_id,
                    "payment_event_id": row.payment_event_id,
                    "refund_minor_units": row.refund_minor_units,
                    "currency": row.currency,
                    "classification": row.classification,
                    "reversal_transaction_id": row.reversal_transaction_id,
                    "occurred_at": row.occurred_at,
                    "created_at": row.created_at,
                }
                for row in refunds
            ],
            "disputes": [
                {
                    "dispute_id": row.id,
                    "purchase_id": row.purchase_id,
                    "payment_event_id": row.payment_event_id,
                    "disputed_minor_units": row.disputed_minor_units,
                    "currency": row.currency,
                    "outcome": row.outcome,
                    "reversal_transaction_id": row.reversal_transaction_id,
                    "occurred_at": row.occurred_at,
                    "created_at": row.created_at,
                }
                for row in disputes
            ],
            "subscriptions": [
                {
                    "subscription_id": row.id,
                    "user_id": row.user_id,
                    "product_code": row.product_code,
                    "status": _value(row.normalized_status),
                    "current_period_start": row.current_period_start,
                    "current_period_end": row.current_period_end,
                    "paid_through_at": row.paid_through_at,
                    "cancel_at_period_end": row.cancel_at_period_end,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in subscriptions
            ],
            "invoices": [
                {
                    "invoice_id": row.id,
                    "subscription_id": row.subscription_id,
                    "user_id": row.user_id,
                    "payment_event_id": row.payment_event_id,
                    "period_start": row.period_start,
                    "period_end": row.period_end,
                    "pre_tax_minor_units": row.pre_tax_minor_units,
                    "tax_minor_units": row.tax_minor_units,
                    "currency": row.currency,
                    "status": row.provider_status,
                    "credit_grant_id": row.credit_grant_id,
                    "refunded_minor_units": row.refunded_minor_units,
                    "disputed_minor_units": row.disputed_minor_units,
                    "dispute_state": row.dispute_state,
                    "occurred_at": row.occurred_at,
                    "created_at": row.created_at,
                }
                for row in invoices
            ],
            "invoice_adjustments": [
                {
                    "adjustment_id": row.id,
                    "invoice_id": row.invoice_id,
                    "payment_event_id": row.payment_event_id,
                    "kind": row.adjustment_kind,
                    "amount_minor_units": row.amount_minor_units,
                    "currency": row.currency,
                    "outcome": row.outcome,
                    "reversal_transaction_id": row.reversal_transaction_id,
                    "occurred_at": row.occurred_at,
                    "created_at": row.created_at,
                }
                for row in adjustments
                if row.invoice_id in invoice_ids
            ],
            "consent_records": consent_records,
            "media": [
                {
                    "asset_id": row.id,
                    "owner_user_id": row.owner_user_id,
                    "upload_batch_id": row.upload_batch_id,
                    "order_id": row.order_id,
                    "job_id": row.job_id,
                    "parent_asset_id": row.parent_asset_id,
                    "role": _value(row.role),
                    "status": _value(row.status),
                    "content_sha256": row.sha256,
                    "mime_type": row.mime_type,
                    "byte_size": row.byte_size,
                    "width": row.width,
                    "height": row.height,
                    "expires_at": row.expires_at,
                    "read_revoked_at": row.read_revoked_at,
                    "deletion_reason": row.deletion_reason,
                    "deleted_at": row.deleted_at,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in media
            ],
            "retention": [
                {
                    "user_id": user_id,
                    "account_closed_at": (
                        tombstone_by_user[user_id].closed_at
                        if user_id in tombstone_by_user
                        else None
                    ),
                    "closure_reason": (
                        tombstone_by_user[user_id].closure_reason
                        if user_id in tombstone_by_user
                        else None
                    ),
                    "media_cleanup_pending": (
                        tombstone_by_user[user_id].media_cleanup_pending
                        if user_id in tombstone_by_user
                        else False
                    ),
                    "active_media_count": sum(
                        asset.owner_user_id == user_id and _value(asset.status) == "ACTIVE"
                        for asset in media
                    ),
                    "pending_deletion_count": sum(
                        asset.owner_user_id == user_id
                        and _value(asset.status) in {"PENDING_DELETE", "DELETE_FAILED"}
                        for asset in media
                    ),
                    "deleted_media_count": sum(
                        asset.owner_user_id == user_id and _value(asset.status) == "DELETED"
                        for asset in media
                    ),
                }
                for user_id in included_user_ids
            ],
            "audit_references": [
                *[
                    {
                        "reference_id": row.id,
                        "kind": "ACCOUNT_MERGE",
                        "occurred_at": row.created_at,
                    }
                    for row in merges
                ],
                *[
                    {
                        "reference_id": row.user_id,
                        "kind": "ACCOUNT_CLOSURE",
                        "occurred_at": row.closed_at,
                    }
                    for row in tombstones
                ],
                *[
                    {
                        "reference_id": row.id,
                        "kind": "PARTNER_CONSENT",
                        "occurred_at": row.created_at,
                    }
                    for row in consent_cases
                ],
            ],
        }
    )
    payload = export.model_dump(mode="json")
    reject_forbidden_export_keys(payload)
    if len(media_statuses) != len(export.media):
        raise AccountExportError("account_export_media_projection_incomplete")
    return export


async def build_account_export(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    generated_at: datetime | None = None,
    export_id: uuid.UUID | None = None,
) -> AccountExport:
    """Return only the Cookie user's canonical and immutable merge-linked facts."""

    canonical_user_id, merges = await _resolve_account_lineage(db, user_id)
    linked_ids = {canonical_user_id, *(row.legacy_user_id for row in merges)}
    condition = lambda column: column.in_(linked_ids)

    users = await _projected_rows(
        db,
        (
            User.id,
            User.username,
            User.email,
            User.nickname,
            User.role,
            User.status,
            User.last_login_at,
            User.created_at,
            User.updated_at,
        ),
        condition(User.id),
        User.created_at,
        User.id,
    )
    identities = await _projected_rows(
        db,
        (
            UserIdentity.id,
            UserIdentity.user_id,
            UserIdentity.provider,
            UserIdentity.revoked_at,
            UserIdentity.created_at,
            UserIdentity.updated_at,
        ),
        condition(UserIdentity.user_id),
        UserIdentity.created_at,
        UserIdentity.id,
    )
    orders = await _projected_rows(
        db,
        (
            Order.id,
            Order.user_id,
            Order.status,
            Order.template_id,
            Order.reservation_id,
            Order.generation_job_id,
            Order.source_asset_ids,
            Order.preview_asset_ids,
            Order.final_asset_ids,
            Order.settlement_status,
            Order.delivery_status,
            Order.source_images_expires_at,
            Order.expires_at,
            Order.deleted_at,
            Order.storage_cleanup_status,
            Order.price_cents,
            Order.paid_at,
            Order.created_at,
            Order.updated_at,
        ),
        condition(Order.user_id),
        Order.created_at,
        Order.id,
    )
    ledger = await _projected_rows(
        db,
        (
            CreditTransaction.id,
            CreditTransaction.user_id,
            CreditTransaction.transaction_type,
            CreditTransaction.amount,
            CreditTransaction.balance_after,
            CreditTransaction.source,
            CreditTransaction.root_transaction_id,
            CreditTransaction.reversal_of_transaction_id,
            CreditTransaction.provider_attempt_id,
            CreditTransaction.created_at,
        ),
        condition(CreditTransaction.user_id),
        CreditTransaction.created_at,
        CreditTransaction.id,
    )
    purchases = await _projected_rows(
        db,
        (
            CreditPurchase.id,
            CreditPurchase.user_id,
            CreditPurchase.package_id,
            CreditPurchase.credits,
            CreditPurchase.price_cents,
            CreditPurchase.currency,
            CreditPurchase.status,
            CreditPurchase.captured_minor_units,
            CreditPurchase.tax_minor_units,
            CreditPurchase.refunded_minor_units,
            CreditPurchase.disputed_minor_units,
            CreditPurchase.dispute_state,
            CreditPurchase.grant_transaction_id,
            CreditPurchase.grant_lot_id,
            CreditPurchase.confirmed_at,
            CreditPurchase.completed_at,
            CreditPurchase.created_at,
            CreditPurchase.updated_at,
        ),
        condition(CreditPurchase.user_id),
        CreditPurchase.created_at,
        CreditPurchase.id,
    )
    purchase_ids = {row.id for row in purchases}
    refunds = (
        await _projected_rows(
            db,
            (
                PaymentRefundFact.id,
                PaymentRefundFact.purchase_id,
                PaymentRefundFact.payment_event_id,
                PaymentRefundFact.refund_minor_units,
                PaymentRefundFact.currency,
                PaymentRefundFact.classification,
                PaymentRefundFact.reversal_transaction_id,
                PaymentRefundFact.occurred_at,
                PaymentRefundFact.created_at,
            ),
            PaymentRefundFact.purchase_id.in_(purchase_ids),
            PaymentRefundFact.created_at,
            PaymentRefundFact.id,
        )
        if purchase_ids
        else []
    )
    disputes = (
        await _projected_rows(
            db,
            (
                PaymentDisputeFact.id,
                PaymentDisputeFact.purchase_id,
                PaymentDisputeFact.payment_event_id,
                PaymentDisputeFact.disputed_minor_units,
                PaymentDisputeFact.currency,
                PaymentDisputeFact.outcome,
                PaymentDisputeFact.reversal_transaction_id,
                PaymentDisputeFact.occurred_at,
                PaymentDisputeFact.created_at,
            ),
            PaymentDisputeFact.purchase_id.in_(purchase_ids),
            PaymentDisputeFact.created_at,
            PaymentDisputeFact.id,
        )
        if purchase_ids
        else []
    )
    subscriptions = await _projected_rows(
        db,
        (
            UserSubscription.id,
            UserSubscription.user_id,
            UserSubscription.product_code,
            UserSubscription.normalized_status,
            UserSubscription.current_period_start,
            UserSubscription.current_period_end,
            UserSubscription.paid_through_at,
            UserSubscription.cancel_at_period_end,
            UserSubscription.created_at,
            UserSubscription.updated_at,
        ),
        condition(UserSubscription.user_id),
        UserSubscription.created_at,
        UserSubscription.id,
    )
    invoices = await _projected_rows(
        db,
        (
            SubscriptionInvoice.id,
            SubscriptionInvoice.subscription_id,
            SubscriptionInvoice.user_id,
            SubscriptionInvoice.payment_event_id,
            SubscriptionInvoice.period_start,
            SubscriptionInvoice.period_end,
            SubscriptionInvoice.pre_tax_minor_units,
            SubscriptionInvoice.tax_minor_units,
            SubscriptionInvoice.currency,
            SubscriptionInvoice.provider_status,
            SubscriptionInvoice.credit_grant_id,
            SubscriptionInvoice.refunded_minor_units,
            SubscriptionInvoice.disputed_minor_units,
            SubscriptionInvoice.dispute_state,
            SubscriptionInvoice.occurred_at,
            SubscriptionInvoice.created_at,
        ),
        condition(SubscriptionInvoice.user_id),
        SubscriptionInvoice.created_at,
        SubscriptionInvoice.id,
    )
    invoice_ids = {row.id for row in invoices}
    adjustments = (
        await _projected_rows(
            db,
            (
                SubscriptionInvoiceAdjustmentFact.id,
                SubscriptionInvoiceAdjustmentFact.invoice_id,
                SubscriptionInvoiceAdjustmentFact.payment_event_id,
                SubscriptionInvoiceAdjustmentFact.adjustment_kind,
                SubscriptionInvoiceAdjustmentFact.amount_minor_units,
                SubscriptionInvoiceAdjustmentFact.currency,
                SubscriptionInvoiceAdjustmentFact.outcome,
                SubscriptionInvoiceAdjustmentFact.reversal_transaction_id,
                SubscriptionInvoiceAdjustmentFact.occurred_at,
                SubscriptionInvoiceAdjustmentFact.created_at,
            ),
            SubscriptionInvoiceAdjustmentFact.invoice_id.in_(invoice_ids),
            SubscriptionInvoiceAdjustmentFact.created_at,
            SubscriptionInvoiceAdjustmentFact.id,
        )
        if invoice_ids
        else []
    )
    invites = await _projected_rows(
        db,
        (
            PartnerInvite.id,
            PartnerInvite.host_user_id,
            PartnerInvite.partner_user_id,
            PartnerInvite.status,
            PartnerInvite.partner_asset_id,
            PartnerInvite.order_id,
            PartnerInvite.job_id,
            PartnerInvite.created_at,
            PartnerInvite.updated_at,
        ),
        or_(
            PartnerInvite.host_user_id.in_(linked_ids),
            PartnerInvite.partner_user_id.in_(linked_ids),
        ),
        PartnerInvite.created_at,
        PartnerInvite.id,
    )
    invite_ids = {row.id for row in invites}
    invite_events = (
        await _projected_rows(
            db,
            (
                PartnerInviteEvent.id,
                PartnerInviteEvent.invite_id,
                PartnerInviteEvent.command,
                PartnerInviteEvent.to_status,
                PartnerInviteEvent.created_at,
            ),
            PartnerInviteEvent.invite_id.in_(invite_ids),
            PartnerInviteEvent.created_at,
            PartnerInviteEvent.id,
        )
        if invite_ids
        else []
    )
    consent_cases = await _projected_rows(
        db,
        (
            PartnerConsentCase.id,
            PartnerConsentCase.host_user_id,
            PartnerConsentCase.partner_user_id,
            PartnerConsentCase.order_id,
            PartnerConsentCase.job_id,
            PartnerConsentCase.owned_asset_ids,
            PartnerConsentCase.status,
            PartnerConsentCase.reason_code,
            PartnerConsentCase.created_at,
            PartnerConsentCase.updated_at,
        ),
        or_(
            PartnerConsentCase.host_user_id.in_(linked_ids),
            PartnerConsentCase.partner_user_id.in_(linked_ids),
        ),
        PartnerConsentCase.created_at,
        PartnerConsentCase.id,
    )
    media = await _projected_rows(
        db,
        (
            MediaAsset.id,
            MediaAsset.owner_user_id,
            MediaAsset.upload_batch_id,
            MediaAsset.order_id,
            MediaAsset.job_id,
            MediaAsset.parent_asset_id,
            MediaAsset.role,
            MediaAsset.status,
            MediaAsset.sha256,
            MediaAsset.mime_type,
            MediaAsset.byte_size,
            MediaAsset.width,
            MediaAsset.height,
            MediaAsset.expires_at,
            MediaAsset.read_revoked_at,
            MediaAsset.deletion_reason,
            MediaAsset.deleted_at,
            MediaAsset.created_at,
            MediaAsset.updated_at,
        ),
        condition(MediaAsset.owner_user_id),
        MediaAsset.created_at,
        MediaAsset.id,
    )
    tombstones = await _projected_rows(
        db,
        (
            AccountTombstone.user_id,
            AccountTombstone.closure_reason,
            AccountTombstone.closed_at,
            AccountTombstone.media_cleanup_pending,
        ),
        condition(AccountTombstone.user_id),
        AccountTombstone.closed_at,
    )

    return _assemble_account_export(
        canonical_user_id=canonical_user_id,
        requested_user_id=user_id,
        users=users,
        merges=merges,
        identities=identities,
        orders=orders,
        ledger=ledger,
        purchases=purchases,
        refunds=refunds,
        disputes=disputes,
        subscriptions=subscriptions,
        invoices=invoices,
        adjustments=adjustments,
        invites=invites,
        invite_events=invite_events,
        consent_cases=consent_cases,
        media=media,
        tombstones=tombstones,
        generated_at=generated_at or datetime.now(timezone.utc),
        export_id=export_id or uuid.uuid4(),
    )
