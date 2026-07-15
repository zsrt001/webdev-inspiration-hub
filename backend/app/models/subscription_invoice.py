"""Immutable provider-neutral subscription invoice and adjustment facts."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SubscriptionInvoice(Base):
    __tablename__ = "subscription_invoices"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_transaction_id",
            name="uq_subscription_invoice_provider_transaction",
        ),
        UniqueConstraint(
            "subscription_id",
            "period_start",
            "period_end",
            name="uq_subscription_invoice_period",
        ),
        UniqueConstraint("payment_event_id", name="uq_subscription_invoice_payment_event"),
        UniqueConstraint("credit_grant_id", name="uq_subscription_invoice_credit_grant"),
        CheckConstraint("period_end > period_start", name="ck_subscription_invoice_period"),
        CheckConstraint(
            "pre_tax_minor_units > 0 AND tax_minor_units >= 0",
            name="ck_subscription_invoice_amounts",
        ),
        CheckConstraint(
            "refunded_minor_units >= 0 AND disputed_minor_units >= 0 "
            "AND refunded_minor_units <= pre_tax_minor_units + tax_minor_units "
            "AND disputed_minor_units <= pre_tax_minor_units + tax_minor_units",
            name="ck_subscription_invoice_adjustment_bounds",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_subscriptions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    payment_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_invoice_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    pre_tax_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_minor_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    provider_status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_catalog_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    catalog_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    credit_grant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_credit_grants.id", ondelete="RESTRICT"),
        nullable=True,
    )
    refunded_minor_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    disputed_minor_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispute_state: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def total_minor_units(self) -> int:
        return int(self.pre_tax_minor_units) + int(self.tax_minor_units)


class SubscriptionInvoiceAdjustmentFact(Base):
    __tablename__ = "subscription_invoice_adjustment_facts"
    __table_args__ = (
        UniqueConstraint("payment_event_id", name="uq_subscription_adjustment_event"),
        UniqueConstraint(
            "provider",
            "provider_object_id",
            "adjustment_kind",
            "outcome",
            name="uq_subscription_adjustment_provider_outcome",
        ),
        CheckConstraint("amount_minor_units > 0", name="ck_subscription_adjustment_amount"),
        CheckConstraint(
            "adjustment_kind IN ('REFUND','DISPUTE')",
            name="ck_subscription_adjustment_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_invoices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payment_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    adjustment_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reversal_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_transactions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
