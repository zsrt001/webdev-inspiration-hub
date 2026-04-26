"""Credit purchase model for hosted checkout providers."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
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


class CreditPurchase(Base):
    """Persistent purchase record used for idempotent payment reconciliation."""

    __tablename__ = "credit_purchases"
    __table_args__ = (
        UniqueConstraint("provider_request_id", name="uq_credit_purchase_request_id"),
        UniqueConstraint("webhook_event_id", name="uq_credit_purchase_webhook_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), default="creem", index=True)
    package_id: Mapped[str] = mapped_column(String(64), index=True)
    credits: Mapped[int] = mapped_column(Integer, default=0)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[CreditPurchaseStatus] = mapped_column(String(32), default=CreditPurchaseStatus.PENDING, index=True)
    provider_request_id: Mapped[str] = mapped_column(String(128), index=True)
    provider_checkout_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    webhook_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", lazy="selectin")
