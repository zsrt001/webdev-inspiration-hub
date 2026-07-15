"""Verified normalized Provider events and immutable payment facts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PaymentEventProcessingState(str, Enum):
    RECEIVED = "RECEIVED"
    UNHANDLED = "UNHANDLED"
    APPLIED = "APPLIED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_payment_events_provider_event_id"),
        CheckConstraint(
            "pre_tax_minor_units IS NULL OR pre_tax_minor_units >= 0",
            name="ck_payment_event_pre_tax_nonnegative",
        ),
        CheckConstraint(
            "tax_minor_units IS NULL OR tax_minor_units >= 0",
            name="ck_payment_event_tax_nonnegative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), default="creem", server_default="creem", index=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    object_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pre_tax_minor_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tax_minor_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    normalized_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    processing_state: Mapped[PaymentEventProcessingState] = mapped_column(
        String(32),
        default=PaymentEventProcessingState.RECEIVED,
        server_default=PaymentEventProcessingState.RECEIVED.value,
        index=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PaymentCaptureFact(Base):
    __tablename__ = "payment_capture_facts"
    __table_args__ = (
        UniqueConstraint("purchase_id", name="uq_payment_capture_purchase"),
        UniqueConstraint("payment_event_id", name="uq_payment_capture_event"),
        UniqueConstraint("provider", "provider_payment_id", name="uq_payment_capture_provider_payment"),
        CheckConstraint("pre_tax_minor_units > 0 AND tax_minor_units >= 0", name="ck_payment_capture_amounts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credit_purchases.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_events.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pre_tax_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_minor_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    grant_transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credit_transactions.id", ondelete="RESTRICT"), nullable=False, unique=True)
    grant_lot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credit_grant_lots.id", ondelete="RESTRICT"), nullable=False, unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PaymentRefundFact(Base):
    __tablename__ = "payment_refund_facts"
    __table_args__ = (
        UniqueConstraint("payment_event_id", name="uq_payment_refund_event"),
        UniqueConstraint("provider", "provider_refund_id", name="uq_payment_refund_provider_refund"),
        CheckConstraint("refund_minor_units > 0", name="ck_payment_refund_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credit_purchases.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_events.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_refund_id: Mapped[str] = mapped_column(String(128), nullable=False)
    refund_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    reversal_transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("credit_transactions.id", ondelete="RESTRICT"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PaymentDisputeFact(Base):
    __tablename__ = "payment_dispute_facts"
    __table_args__ = (
        UniqueConstraint("payment_event_id", name="uq_payment_dispute_event"),
        UniqueConstraint("provider", "provider_dispute_id", "outcome", name="uq_payment_dispute_provider_outcome"),
        CheckConstraint("disputed_minor_units > 0", name="ck_payment_dispute_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credit_purchases.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_events.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_dispute_id: Mapped[str] = mapped_column(String(128), nullable=False)
    disputed_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reversal_transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("credit_transactions.id", ondelete="RESTRICT"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
