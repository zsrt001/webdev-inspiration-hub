"""Credit purchase model for hosted checkout providers."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CreditPurchaseStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    REFUNDED = "refunded"
    # Backward-compatible aliases for older code paths.
    CREATED = "pending"
    COMPLETED = "paid"
    CANCELED = "failed"


class PurchaseIntentState(str, Enum):
    NEW = "NEW"
    CALLING = "CALLING"
    READY = "READY"
    UNKNOWN = "UNKNOWN"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    CONFIRMED = "CONFIRMED"


class CreditPurchase(Base):
    """Persistent purchase record used for idempotent payment reconciliation."""

    __tablename__ = "credit_purchases"
    __table_args__ = (
        UniqueConstraint("provider_request_id", name="uq_credit_purchase_request_id"),
        UniqueConstraint("webhook_event_id", name="uq_credit_purchase_webhook_event_id"),
        UniqueConstraint("internal_metadata_id", name="uq_credit_purchase_internal_metadata"),
        UniqueConstraint("grant_transaction_id", name="uq_credit_purchase_grant_transaction"),
        UniqueConstraint("grant_lot_id", name="uq_credit_purchase_grant_lot"),
        CheckConstraint(
            "intent_state IN ('NEW','CALLING','READY','UNKNOWN','FAILED_RETRYABLE','CONFIRMED')",
            name="ck_credit_purchase_intent_state",
        ),
        CheckConstraint(
            "captured_minor_units >= 0 AND tax_minor_units >= 0 "
            "AND refunded_minor_units >= 0 AND disputed_minor_units >= 0",
            name="ck_credit_purchase_money_nonnegative",
        ),
        CheckConstraint(
            "refunded_minor_units <= captured_minor_units "
            "AND disputed_minor_units <= captured_minor_units",
            name="ck_credit_purchase_money_bounded",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), default="creem", index=True)
    package_id: Mapped[str] = mapped_column(String(64), index=True)
    credits: Mapped[int] = mapped_column(Integer, default=0)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[CreditPurchaseStatus] = mapped_column(String(32), default=CreditPurchaseStatus.PENDING, index=True)
    provider_request_id: Mapped[str] = mapped_column(String(128), index=True)
    intent_state: Mapped[PurchaseIntentState] = mapped_column(
        String(32), default=PurchaseIntentState.NEW, server_default=PurchaseIntentState.NEW.value, index=True
    )
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    catalog_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_catalog_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    catalog_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    internal_metadata_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    stored_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provider_checkout_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    webhook_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    captured_minor_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tax_minor_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    refunded_minor_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    disputed_minor_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    dispute_state: Mapped[str] = mapped_column(String(32), default="NONE", server_default="NONE")
    grant_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_transactions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    grant_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_grant_lots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    call_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", lazy="selectin")
