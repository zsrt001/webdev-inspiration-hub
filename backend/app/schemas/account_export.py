"""Strict user-owned account data export contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _ExportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccountExportProfile(_ExportModel):
    user_id: UUID
    username: str | None
    email: str | None
    nickname: str | None
    role: str
    status: str
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AccountExportMerge(_ExportModel):
    merge_id: UUID
    canonical_user_id: UUID
    legacy_user_id: UUID
    created_at: datetime


class AccountExportIdentity(_ExportModel):
    identity_id: UUID
    user_id: UUID
    provider: str
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AccountExportOrder(_ExportModel):
    order_id: UUID
    user_id: UUID
    status: str
    template_id: str | None
    reservation_id: UUID | None
    generation_job_id: UUID | None
    source_asset_ids: tuple[UUID, ...]
    preview_asset_ids: tuple[UUID, ...]
    final_asset_ids: tuple[UUID, ...]
    settlement_status: str
    delivery_status: str
    source_images_expires_at: datetime | None
    expires_at: datetime | None
    deleted_at: datetime | None
    storage_cleanup_status: str
    price_minor_units: int
    paid_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AccountExportLedgerEntry(_ExportModel):
    transaction_id: UUID
    user_id: UUID
    transaction_type: str
    amount: int
    balance_after: int
    source: str | None
    root_transaction_id: UUID | None
    reversal_of_transaction_id: UUID | None
    provider_attempt_id: UUID | None
    created_at: datetime


class AccountExportPurchase(_ExportModel):
    purchase_id: UUID
    user_id: UUID
    product_code: str
    credits: int
    pre_tax_minor_units: int
    currency: str
    status: str
    captured_minor_units: int
    tax_minor_units: int
    refunded_minor_units: int
    disputed_minor_units: int
    dispute_state: str
    grant_transaction_id: UUID | None
    grant_lot_id: UUID | None
    confirmed_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AccountExportRefund(_ExportModel):
    refund_id: UUID
    purchase_id: UUID
    payment_event_id: UUID
    refund_minor_units: int
    currency: str
    classification: str
    reversal_transaction_id: UUID | None
    occurred_at: datetime
    created_at: datetime


class AccountExportDispute(_ExportModel):
    dispute_id: UUID
    purchase_id: UUID
    payment_event_id: UUID
    disputed_minor_units: int
    currency: str
    outcome: str
    reversal_transaction_id: UUID | None
    occurred_at: datetime
    created_at: datetime


class AccountExportSubscription(_ExportModel):
    subscription_id: UUID
    user_id: UUID
    product_code: str | None
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    paid_through_at: datetime | None
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime


class AccountExportInvoice(_ExportModel):
    invoice_id: UUID
    subscription_id: UUID
    user_id: UUID
    payment_event_id: UUID
    period_start: datetime
    period_end: datetime
    pre_tax_minor_units: int
    tax_minor_units: int
    currency: str
    status: str
    credit_grant_id: UUID | None
    refunded_minor_units: int
    disputed_minor_units: int
    dispute_state: str
    occurred_at: datetime
    created_at: datetime


class AccountExportInvoiceAdjustment(_ExportModel):
    adjustment_id: UUID
    invoice_id: UUID
    payment_event_id: UUID
    kind: str
    amount_minor_units: int
    currency: str
    outcome: str
    reversal_transaction_id: UUID | None
    occurred_at: datetime
    created_at: datetime


class AccountExportConsentRecord(_ExportModel):
    record_id: UUID
    record_type: Literal["INVITE", "INVITE_EVENT", "CONSENT_CASE"]
    host_user_id: UUID
    partner_user_id: UUID | None
    order_id: UUID | None
    job_id: UUID | None
    asset_ids: tuple[UUID, ...]
    status: str
    action: str | None
    created_at: datetime
    updated_at: datetime | None


class AccountExportMedia(_ExportModel):
    asset_id: UUID
    owner_user_id: UUID
    upload_batch_id: UUID | None
    order_id: UUID | None
    job_id: UUID | None
    parent_asset_id: UUID | None
    role: str
    status: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str
    byte_size: int = Field(ge=0)
    width: int | None
    height: int | None
    expires_at: datetime
    read_revoked_at: datetime | None
    deletion_reason: str | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AccountExportRetention(_ExportModel):
    user_id: UUID
    account_closed_at: datetime | None
    closure_reason: str | None
    media_cleanup_pending: bool
    active_media_count: int = Field(ge=0)
    pending_deletion_count: int = Field(ge=0)
    deleted_media_count: int = Field(ge=0)


class AccountExportAuditReference(_ExportModel):
    reference_id: UUID
    kind: Literal["ACCOUNT_MERGE", "ACCOUNT_CLOSURE", "PARTNER_CONSENT"]
    occurred_at: datetime


class AccountExport(_ExportModel):
    schema_version: Literal["account-export.v1"]
    export_id: UUID
    generated_at: datetime
    canonical_user_id: UUID
    included_user_ids: tuple[UUID, ...]
    profile: AccountExportProfile
    merged_accounts: tuple[AccountExportMerge, ...]
    identities: tuple[AccountExportIdentity, ...]
    orders: tuple[AccountExportOrder, ...]
    ledger: tuple[AccountExportLedgerEntry, ...]
    purchases: tuple[AccountExportPurchase, ...]
    refunds: tuple[AccountExportRefund, ...]
    disputes: tuple[AccountExportDispute, ...]
    subscriptions: tuple[AccountExportSubscription, ...]
    invoices: tuple[AccountExportInvoice, ...]
    invoice_adjustments: tuple[AccountExportInvoiceAdjustment, ...]
    consent_records: tuple[AccountExportConsentRecord, ...]
    media: tuple[AccountExportMedia, ...]
    retention: tuple[AccountExportRetention, ...]
    audit_references: tuple[AccountExportAuditReference, ...]
