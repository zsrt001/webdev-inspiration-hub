"""Idempotent subscription period credit grant model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SubscriptionCreditGrant(Base):
    """Tracks monthly subscription credit grants to prevent duplicate issuance."""

    __tablename__ = "subscription_credit_grants"
    __table_args__ = (
        UniqueConstraint("subscription_id", "period_key", name="uq_subscription_credit_grant_period"),
        UniqueConstraint("invoice_id", name="uq_subscription_credit_grant_invoice"),
        UniqueConstraint("grant_lot_id", name="uq_subscription_credit_grant_lot"),
        UniqueConstraint(
            "subscription_id",
            "period_start",
            "period_end",
            name="uq_subscription_credit_grant_exact_period",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_subscriptions.id", ondelete="RESTRICT"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    period_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_invoices.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    credit_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_transactions.id", ondelete="RESTRICT"),
        index=True,
    )
    grant_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_grant_lots.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscription: Mapped["UserSubscription"] = relationship("UserSubscription", back_populates="credit_grants", lazy="selectin")
    user: Mapped["User"] = relationship("User", lazy="selectin")
    credit_transaction: Mapped["CreditTransaction"] = relationship("CreditTransaction", lazy="selectin")
